"""
LLM-as-judge search validation.

Samples queries from the search eval question file, runs RAG search, then asks
an LLM to rate each result's relevance (0-3). Compares RAG ranking to LLM
relevance ranking to answer: how close is cheap RAG to what an expensive LLM
would consider the best results?

Usage:
    .venv/bin/python src/kb/search/judge_search_eval.py --sample 5 --verbose
    .venv/bin/python src/kb/search/judge_search_eval.py --sample 50
    .venv/bin/python src/kb/search/judge_search_eval.py --sample 50 --output data/judge_results.json
"""

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Add src/kb to path for infrastructure imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from database import Database
from generate_search_eval import format_condensed_for_prompt
from llm import LLM, get_embeddings

DEFAULT_QUESTIONS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "search_eval_questions.json")

JUDGE_SYSTEM_PROMPT = """You are evaluating whether a Feldenkrais lesson is relevant to a search query.

You will be given:
1. A search query (what someone typed to find a lesson)
2. A condensed lesson card (summary of a specific lesson)

Rate how relevant the lesson is to the query on a 0-3 scale:
- 0 = irrelevant (the lesson has nothing to do with the query)
- 1 = tangentially related (same body area or broad theme, but not what the searcher wants)
- 2 = good match (would help someone searching for this)
- 3 = excellent match (directly addresses the query)

Return ONLY a JSON object with two fields:
{"relevance": <0-3>, "reason": "<one sentence explanation>"}"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "integer", "minimum": 0, "maximum": 3},
        "reason": {"type": "string"},
    },
    "required": ["relevance", "reason"],
}


def sample_queries(questions: list[dict], n: int, seed: int) -> list[dict]:
    """Stratified sample: pick evenly across categories, then fill remainder randomly."""
    rng = random.Random(seed)
    by_cat = defaultdict(list)
    for q in questions:
        by_cat[q["category"]].append(q)

    per_cat = n // len(by_cat)
    remainder = n - per_cat * len(by_cat)

    sampled = []
    leftover = []
    for cat in sorted(by_cat):
        pool = list(by_cat[cat])
        rng.shuffle(pool)
        sampled.extend(pool[:per_cat])
        leftover.extend(pool[per_cat:])

    rng.shuffle(leftover)
    sampled.extend(leftover[:remainder])
    return sampled


def bulk_load_condensed(db: Database, doc_ids: set[str]) -> dict[str, dict]:
    """Load condensed cards for arbitrary doc_ids using 2 DB round-trips.

    Returns {doc_id: {"condensed": data, "title": title}} for docs that have condensed data.
    """
    cache_suffix = ":pass_0b_condensed_v4"

    # Round-trip 1: get transcript text for all needed doc_ids
    placeholders = ",".join(["%s"] * len(doc_ids))
    with db.get_cursor(commit=False) as cursor:
        cursor.execute(
            f"""
            SELECT a.document_id::text as doc_id,
                   d.title,
                   a.data->>'text' as text
            FROM analysis a
            JOIN document d ON d.id = a.document_id
            WHERE a.type_id = 1
              AND a.data->>'text' IS NOT NULL
              AND a.document_id::text IN ({placeholders})
            """,
            tuple(doc_ids),
        )
        doc_info = {}
        cache_key_to_doc = {}
        for row in cursor.fetchall():
            text = (row["text"] or "").strip()
            if text:
                cache_key = hashlib.sha256(
                    (text + cache_suffix).encode()
                ).hexdigest()[:16]
                doc_info[row["doc_id"]] = {"title": row["title"], "cache_key": cache_key}
                cache_key_to_doc[cache_key] = row["doc_id"]

    if not cache_key_to_doc:
        return {}

    # Round-trip 2: bulk-load cache entries
    key_placeholders = ",".join(["%s"] * len(cache_key_to_doc))
    with db.get_cursor(commit=False) as cursor:
        cursor.execute(
            f"SELECT key, data FROM cache WHERE key IN ({key_placeholders})",
            tuple(cache_key_to_doc.keys()),
        )
        result = {}
        for row in cursor.fetchall():
            doc_id = cache_key_to_doc[row["key"]]
            result[doc_id] = {
                "condensed": row["data"],
                "title": doc_info[doc_id]["title"],
            }
        return result


def judge_relevance(llm: LLM, query: str, lesson_card_text: str) -> dict:
    """Ask LLM to rate relevance of a lesson to a query. Returns {"relevance": 0-3, "reason": "..."}."""
    prompt = f"Search query: {query}\n\nLesson card:\n{lesson_card_text}"
    try:
        result = llm.complete_structured_json(
            prompt, JUDGE_SCHEMA, system=JUDGE_SYSTEM_PROMPT, max_tokens=256,
        )
        # Clamp relevance to 0-3
        result["relevance"] = max(0, min(3, int(result.get("relevance", 0))))
        return result
    except Exception as e:
        return {"relevance": -1, "reason": f"ERROR: {e}"}


def compute_ndcg(relevances: list[int]) -> float:
    """Compute NDCG for a single query's result list.

    relevances: list of relevance scores in RAG rank order.
    """
    if not relevances or all(r <= 0 for r in relevances):
        return 0.0

    def dcg(rels):
        return sum(r / math.log2(i + 2) for i, r in enumerate(rels))

    actual = dcg(relevances)
    ideal = dcg(sorted(relevances, reverse=True))
    return actual / ideal if ideal > 0 else 0.0


def compute_metrics(query_results: list[dict]) -> dict:
    """Compute all metrics from judged query results."""
    metrics = {}

    # Collect all judgments
    all_judgments = []  # (rank_position, relevance, category, query_id)
    for qr in query_results:
        for j in qr["judgments"]:
            if j["relevance"] >= 0:  # skip errors
                all_judgments.append({
                    "rank": j["rank"],
                    "relevance": j["relevance"],
                    "category": qr["category"],
                    "query_id": qr["id"],
                })

    # 1. Average relevance at each rank
    by_rank = defaultdict(list)
    for j in all_judgments:
        if j["rank"] is not None:
            by_rank[j["rank"]].append(j["relevance"])
    avg_by_rank = {k: sum(v) / len(v) for k, v in sorted(by_rank.items())}
    metrics["avg_relevance_by_rank"] = avg_by_rank

    # 2. NDCG per query, then average
    ndcgs = {"overall": [], "by_category": defaultdict(list)}
    for qr in query_results:
        rels = [j["relevance"] for j in qr["judgments"] if j["relevance"] >= 0 and j["rank"] is not None]
        if rels:
            n = compute_ndcg(rels)
            ndcgs["overall"].append(n)
            ndcgs["by_category"][qr["category"]].append(n)

    metrics["ndcg"] = {
        "overall": sum(ndcgs["overall"]) / len(ndcgs["overall"]) if ndcgs["overall"] else 0,
        "by_category": {
            cat: sum(vals) / len(vals) for cat, vals in sorted(ndcgs["by_category"].items())
        },
    }

    # 3. Expected lesson analysis
    expected_stats = {"expected_is_best": 0, "total": 0, "avg_expected_score": [], "avg_rank1_score": []}
    for qr in query_results:
        expected_j = next((j for j in qr["judgments"] if j.get("is_expected")), None)
        rank1_j = next((j for j in qr["judgments"] if j["rank"] == 1), None)
        if expected_j and expected_j["relevance"] >= 0:
            expected_stats["total"] += 1
            expected_stats["avg_expected_score"].append(expected_j["relevance"])
            if rank1_j and rank1_j["relevance"] >= 0:
                expected_stats["avg_rank1_score"].append(rank1_j["relevance"])
                if expected_j["relevance"] >= rank1_j["relevance"]:
                    expected_stats["expected_is_best"] += 1

    metrics["expected_lesson"] = {
        "total": expected_stats["total"],
        "expected_is_best_pct": (
            expected_stats["expected_is_best"] / expected_stats["total"]
            if expected_stats["total"] else 0
        ),
        "avg_expected_score": (
            sum(expected_stats["avg_expected_score"]) / len(expected_stats["avg_expected_score"])
            if expected_stats["avg_expected_score"] else 0
        ),
        "avg_rank1_score": (
            sum(expected_stats["avg_rank1_score"]) / len(expected_stats["avg_rank1_score"])
            if expected_stats["avg_rank1_score"] else 0
        ),
    }

    # 4. Relevance distribution
    dist = defaultdict(int)
    dist_by_rank = defaultdict(lambda: defaultdict(int))
    for j in all_judgments:
        dist[j["relevance"]] += 1
        if j["rank"] is not None:
            dist_by_rank[j["rank"]][j["relevance"]] += 1
    total_judged = sum(dist.values())
    metrics["relevance_distribution"] = {
        "counts": dict(dist),
        "percentages": {k: v / total_judged for k, v in dist.items()} if total_judged else {},
        "total": total_judged,
    }
    metrics["relevance_distribution_by_rank"] = {
        rank: dict(counts) for rank, counts in sorted(dist_by_rank.items())
    }

    # 5. Per-category breakdown
    cat_metrics = {}
    categories = sorted(set(qr["category"] for qr in query_results))
    for cat in categories:
        cat_judgments = [j for j in all_judgments if j["category"] == cat]
        cat_dist = defaultdict(int)
        for j in cat_judgments:
            cat_dist[j["relevance"]] += 1
        cat_total = sum(cat_dist.values())
        cat_metrics[cat] = {
            "count": len([qr for qr in query_results if qr["category"] == cat]),
            "relevance_distribution": {k: v / cat_total for k, v in cat_dist.items()} if cat_total else {},
        }
    metrics["by_category"] = cat_metrics

    return metrics


def print_report(metrics: dict, query_results: list[dict], timing: dict, args, model_name: str):
    """Print the evaluation report."""
    print("\n" + "=" * 70)
    print("LLM-AS-JUDGE SEARCH VALIDATION")
    print("=" * 70)
    print(f"  Sample size: {len(query_results)} queries")
    print(f"  Judge model: {model_name}")
    print(f"  Top-K: {args.top_k}")
    print(f"  Timing: embed {timing['embed']:.1f}s, search {timing['search']:.1f}s, judging {timing['judge']:.1f}s")

    total_calls = sum(len(qr["judgments"]) for qr in query_results)
    errors = sum(1 for qr in query_results for j in qr["judgments"] if j["relevance"] < 0)
    print(f"  LLM calls: {total_calls} ({errors} errors)")

    # Section 1: Relevance distribution
    print(f"\n--- RELEVANCE DISTRIBUTION ---")
    rd = metrics["relevance_distribution"]
    print(f"\n  {'Score':<8} {'Count':>6} {'Pct':>7}")
    print(f"  {'-' * 23}")
    for score in range(4):
        count = rd["counts"].get(score, 0)
        pct = rd["percentages"].get(score, 0)
        labels = {0: "irrelevant", 1: "tangential", 2: "good", 3: "excellent"}
        print(f"  {score} ({labels[score]:<10}) {count:>5} {pct:>7.1%}")

    # By rank position
    print(f"\n  Avg relevance by rank position:")
    for rank, avg in metrics["avg_relevance_by_rank"].items():
        if rank <= 10:
            print(f"    Rank {rank:>2}: {avg:.2f}")

    # Section 2: NDCG
    print(f"\n--- NDCG (RAG vs LLM ranking agreement) ---")
    print(f"  Overall: {metrics['ndcg']['overall']:.3f}")
    for cat, val in metrics["ndcg"]["by_category"].items():
        print(f"  {cat}: {val:.3f}")

    # Section 3: Expected lesson analysis
    print(f"\n--- EXPECTED LESSON ANALYSIS ---")
    el = metrics["expected_lesson"]
    print(f"  Queries with expected lesson judged: {el['total']}")
    print(f"  Expected lesson scored >= rank-1 result: {el['expected_is_best_pct']:.1%}")
    print(f"  Avg LLM score for expected lesson: {el['avg_expected_score']:.2f}")
    print(f"  Avg LLM score for rank-1 result: {el['avg_rank1_score']:.2f}")

    # Section 4: Per-category breakdown
    print(f"\n--- PER-CATEGORY BREAKDOWN ---")
    for cat, cm in metrics["by_category"].items():
        dist_str = ", ".join(f"{k}={v:.0%}" for k, v in sorted(cm["relevance_distribution"].items()))
        print(f"  {cat} ({cm['count']} queries): {dist_str}")

    # Section 5: Worst cases
    worst = []
    for qr in query_results:
        rank1 = next((j for j in qr["judgments"] if j["rank"] == 1), None)
        if rank1 and 0 <= rank1["relevance"] <= 1:
            worst.append(qr)

    if worst:
        print(f"\n--- WORST CASES ({len(worst)} queries where rank-1 scored 0-1) ---")
        for qr in worst[:10]:
            rank1 = next(j for j in qr["judgments"] if j["rank"] == 1)
            print(f"\n  [{qr['id']}] ({qr['category']}) {qr['query']}")
            print(f"    Expected: {qr['expected_title']}")
            print(f"    Rank-1: {rank1['title']} (relevance={rank1['relevance']})")
            print(f"      Reason: {rank1['reason']}")
    else:
        print(f"\n  No worst cases (all rank-1 results scored 2+)")

    print()


def main():
    parser = argparse.ArgumentParser(description="LLM-as-judge search validation")
    parser.add_argument("--questions", type=str, default=DEFAULT_QUESTIONS, help="Path to question file")
    parser.add_argument("--sample", type=int, default=50, help="Number of queries to sample")
    parser.add_argument("--top-k", type=int, default=10, help="Number of search results to retrieve")
    parser.add_argument("--verbose", action="store_true", help="Show details for each judgment")
    parser.add_argument("--output", type=str, default=None, help="Write detailed results to JSON file")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for sampling")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment", file=sys.stderr)
        sys.exit(1)

    if not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    # Load questions
    questions_path = os.path.normpath(args.questions)
    if not os.path.exists(questions_path):
        print(f"ERROR: Question file not found: {questions_path}", file=sys.stderr)
        sys.exit(1)

    with open(questions_path) as f:
        qfile = json.load(f)

    questions = qfile["questions"]
    print(f"Loaded {len(questions)} questions (version {qfile.get('version', '?')})")

    # Sample queries
    sample_size = min(args.sample, len(questions))
    sampled = sample_queries(questions, sample_size, args.seed)
    cats = defaultdict(int)
    for q in sampled:
        cats[q["category"]] += 1
    cat_str = ", ".join(f"{k}={v}" for k, v in sorted(cats.items()))
    print(f"Sampled {len(sampled)} queries ({cat_str})")

    db = Database(database_url)
    db.initialize()

    # Embed all queries
    print(f"\nEmbedding {len(sampled)} queries...", end=" ", flush=True)
    t0 = time.time()
    query_texts = [q["query"] for q in sampled]
    CHUNK_SIZE = 100
    all_embeddings = []
    for i in range(0, len(query_texts), CHUNK_SIZE):
        chunk = query_texts[i:i + CHUNK_SIZE]
        embeddings, _ = get_embeddings(chunk, task_type="RETRIEVAL_QUERY")
        all_embeddings.extend(embeddings)
    embed_time = time.time() - t0
    print(f"done ({embed_time:.1f}s)")

    # Search for each query
    print(f"Searching (top-{args.top_k})...", end=" ", flush=True)
    t0 = time.time()
    search_results = []
    for q, embedding in zip(sampled, all_embeddings):
        hits = db.search_lessons(embedding, limit=args.top_k)
        search_results.append(hits)
    search_time = time.time() - t0
    print(f"done ({search_time:.1f}s)")

    # Collect all unique doc_ids that need condensed cards
    all_doc_ids = set()
    for q in sampled:
        all_doc_ids.add(q["expected_doc_id"])
    for hits in search_results:
        for h in hits:
            all_doc_ids.add(h["document_id"])

    print(f"Loading condensed cards for {len(all_doc_ids)} unique lessons...", end=" ", flush=True)
    condensed_map = bulk_load_condensed(db, all_doc_ids)
    print(f"got {len(condensed_map)}")

    # Judge each (query, lesson) pair
    llm = LLM(provider="gemini", model="gemini-2.0-flash")
    model_name = "gemini-2.0-flash"

    query_results = []
    total_calls = 0
    skipped = 0

    print(f"\nJudging relevance...", flush=True)
    t0 = time.time()

    for qi, (q, hits) in enumerate(zip(sampled, search_results)):
        # Build list of (doc_id, title, rank) to judge — search results + expected
        to_judge = []
        seen_doc_ids = set()

        for rank_idx, h in enumerate(hits):
            doc_id = h["document_id"]
            seen_doc_ids.add(doc_id)
            to_judge.append({
                "doc_id": doc_id,
                "title": h["title"],
                "rank": rank_idx + 1,
                "is_expected": doc_id == q["expected_doc_id"],
            })

        # Add expected lesson if not already in results
        if q["expected_doc_id"] not in seen_doc_ids:
            to_judge.append({
                "doc_id": q["expected_doc_id"],
                "title": q["expected_title"],
                "rank": None,  # not in top-K
                "is_expected": True,
            })

        judgments = []
        for item in to_judge:
            if item["doc_id"] not in condensed_map:
                skipped += 1
                continue

            card_data = condensed_map[item["doc_id"]]
            card_text = format_condensed_for_prompt(card_data["condensed"], card_data["title"])
            result = judge_relevance(llm, q["query"], card_text)
            total_calls += 1

            judgment = {
                "doc_id": item["doc_id"],
                "title": item["title"],
                "rank": item["rank"],
                "is_expected": item["is_expected"],
                "relevance": result["relevance"],
                "reason": result["reason"],
            }
            judgments.append(judgment)

            if args.verbose:
                rank_str = f"rank={item['rank']}" if item['rank'] else "expected(not in top-K)"
                exp_str = " *EXPECTED*" if item["is_expected"] else ""
                print(f"  [{q['id']}] {rank_str} rel={result['relevance']}{exp_str} — {item['title'][:50]}")

        qr = {
            "id": q["id"],
            "query": q["query"],
            "category": q["category"],
            "expected_doc_id": q["expected_doc_id"],
            "expected_title": q["expected_title"],
            "judgments": judgments,
        }
        query_results.append(qr)

        if (qi + 1) % 10 == 0:
            print(f"  ... {qi + 1}/{len(sampled)} queries, {total_calls} LLM calls", flush=True)

    judge_time = time.time() - t0
    print(f"done ({judge_time:.1f}s, {total_calls} calls, {skipped} skipped)")

    # Compute metrics
    metrics = compute_metrics(query_results)

    timing = {"embed": embed_time, "search": search_time, "judge": judge_time}
    print_report(metrics, query_results, timing, args, model_name)

    # Write JSON output
    if args.output:
        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "questions_file": questions_path,
            "sample_size": len(sampled),
            "top_k": args.top_k,
            "seed": args.seed,
            "model": model_name,
            "timing": timing,
            "metrics": metrics,
            "query_results": query_results,
        }
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Detailed results written to {args.output}")

    db.close()


if __name__ == "__main__":
    main()
