"""
Evaluate lesson search quality against a question file.

Embeds each test query, runs search, and scores whether the expected lesson
appears in the top-K results. Reports P@1, P@3, P@5, MRR overall and by
category (action, body, experience).

Queries are assigned a specificity band based on similarity-score spread.
Use --band to filter to a single band, or see the per-band breakdown in the
report.

Specificity bands (derived 2026-03-02 from 1788 eval queries):
    The "gap" is rank-1 similarity minus rank-5 similarity for each query.
    A small gap means many lessons match equally well (vague query); a large
    gap means the top result stands out (specific query).  We embedded all
    1788 queries, searched top-10, and computed gap percentiles:
        p50 = 0.0167  (median — splits vague vs specific halves)
        p67 = 0.0215  (splits specific half into thirds)
        p83 = 0.0297

    Bands:
        low       gap < 0.0167   bottom 50%  (vague — many equally-good answers)
        mid       0.0167–0.0215  next 17%
        mid-high  0.0215–0.0297  next 17%
        high      gap >= 0.0297  top 17%     (specific — clear best answer)

    Eval results by band (P@1 / MRR):
        low=7.9%/0.151  mid=15.8%/0.243  mid-high=19.1%/0.283  high=49.7%/0.578
    The monotonic increase confirms the metric tracks real specificity.

Usage:
    .venv/bin/python src/kb/search/run_search_eval.py
    .venv/bin/python src/kb/search/run_search_eval.py --verbose
    .venv/bin/python src/kb/search/run_search_eval.py --failures-only
    .venv/bin/python src/kb/search/run_search_eval.py --top-k 20
    .venv/bin/python src/kb/search/run_search_eval.py --band high
    .venv/bin/python src/kb/search/run_search_eval.py --band mid-high,high
    .venv/bin/python src/kb/search/run_search_eval.py --output results.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add src/kb to path for infrastructure imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from database import Database
from llm import get_embeddings

DEFAULT_QUESTIONS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "search_eval_questions.json")

# Specificity band thresholds (gap = rank1_similarity - rank5_similarity).
# Derived 2026-03-02 from percentiles of 1788 eval queries against the full
# embedding index (669 lessons).  See module docstring for methodology.
BAND_THRESHOLDS = {
    "low":      (None,   0.0167),  # bottom 50%
    "mid":      (0.0167, 0.0215),  # 50th–67th percentile
    "mid-high": (0.0215, 0.0297),  # 67th–83rd percentile
    "high":     (0.0297, None),    # top 17%
}
BAND_ORDER = ["low", "mid", "mid-high", "high"]


def classify_band(gap: float) -> str:
    """Assign a specificity band based on similarity gap."""
    for band in BAND_ORDER:
        lo, hi = BAND_THRESHOLDS[band]
        if (lo is None or gap >= lo) and (hi is None or gap < hi):
            return band
    return "high"  # edge case: gap exactly at boundary


def _cascade_search(db: Database, query: str, embedding, top_k: int = 10) -> list[dict]:
    """Cascade search: trust distinctive when confident, RRF fallback otherwise.

    Same logic as web.py _combined_search but for eval context.
    Optimized via grid search (optimize_combined.py, 2026-03-03):
        gate_threshold=0.07, fallback=rrf, concepts_weight=3.0, tag_boost=0.0

    Returns list of {document_id, title, similarity} sorted by score.
    """
    GATE_THRESHOLD = 0.07
    RRF_K = 60
    CONCEPTS_WEIGHT = 3.0

    default_hits = db.search_lessons(embedding, limit=20, method="default")
    concepts_hits = db.search_lessons(embedding, limit=20, method="concepts")

    concepts_sims = [h["similarity"] for h in concepts_hits]
    concepts_gap = concepts_sims[0] - concepts_sims[4] if len(concepts_sims) >= 5 else 0

    if concepts_gap >= GATE_THRESHOLD:
        return concepts_hits[:top_k]

    # RRF fallback
    scores = {}
    result_data = {}

    for rank, hit in enumerate(default_hits, 1):
        doc_id = hit["document_id"]
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (RRF_K + rank)
        if doc_id not in result_data:
            result_data[doc_id] = hit

    for rank, hit in enumerate(concepts_hits, 1):
        doc_id = hit["document_id"]
        scores[doc_id] = scores.get(doc_id, 0) + CONCEPTS_WEIGHT / (RRF_K + rank)
        if doc_id not in result_data:
            result_data[doc_id] = hit

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    results = []
    for doc_id, score in ranked:
        r = dict(result_data[doc_id])
        r["similarity"] = round(score, 6)
        results.append(r)
    return results


def run_eval(db: Database, questions_path: str | None = None, top_k: int = 10,
             quiet: bool = False, method: str = "default") -> dict:
    """Run search evaluation and return structured results.

    Callable from the iteration loop or standalone. Does NOT close the db.

    Args:
        db: Initialized Database instance.
        questions_path: Path to question JSON file. Defaults to DEFAULT_QUESTIONS.
        top_k: Number of search results to retrieve per query.
        quiet: If True, suppress progress output.
        method: Embedding method to search against ('default' or 'concepts').

    Returns:
        Dict with keys: metrics, results, missing_from_index, embed_time, search_time, qfile, method.
    """
    questions_path = os.path.normpath(questions_path or DEFAULT_QUESTIONS)
    if not os.path.exists(questions_path):
        raise FileNotFoundError(f"Question file not found: {questions_path}")

    with open(questions_path) as f:
        qfile = json.load(f)

    questions = qfile["questions"]
    if not quiet:
        print(f"Loaded {len(questions)} questions (version {qfile.get('version', '?')})")
        if method != "default":
            print(f"Search method: {method}")

    if method == "combined":
        # For combined, a question is searchable if expected doc is in either index
        default_ids = set(db.get_embedding_hashes(method="default").keys())
        concepts_ids = set(db.get_embedding_hashes(method="concepts").keys())
        embedded_ids = default_ids | concepts_ids
        if not quiet:
            print(f"Embedding index: default={len(default_ids)}, concepts={len(concepts_ids)}, union={len(embedded_ids)}")
    else:
        embedded_ids = set(db.get_embedding_hashes(method=method).keys())
        if not quiet:
            print(f"Embedding index ({method}) has {len(embedded_ids)} lessons")

    searchable = []
    missing_from_index = []
    for q in questions:
        if q["expected_doc_id"] in embedded_ids:
            searchable.append(q)
        else:
            missing_from_index.append(q)

    if missing_from_index and not quiet:
        unique_missing = {q["expected_doc_id"] for q in missing_from_index}
        print(f"WARNING: {len(missing_from_index)} questions ({len(unique_missing)} lessons) not in index")

    if not searchable:
        raise RuntimeError("No searchable questions — all expected docs missing from index.")

    # Batch embed all queries
    if not quiet:
        print(f"Embedding {len(searchable)} queries...", end=" ", flush=True)
    t0 = time.time()
    query_texts = [q["query"] for q in searchable]

    CHUNK_SIZE = 100
    all_embeddings = []
    for i in range(0, len(query_texts), CHUNK_SIZE):
        chunk = query_texts[i:i + CHUNK_SIZE]
        embeddings, _ = get_embeddings(chunk, task_type="RETRIEVAL_QUERY")
        all_embeddings.extend(embeddings)

    embed_time = time.time() - t0
    if not quiet:
        print(f"done ({embed_time:.1f}s)")

    # Search
    if not quiet:
        print(f"Searching (top-{top_k}, method={method})...", flush=True)
    t0 = time.time()
    results = []

    for q, embedding in zip(searchable, all_embeddings):
        if method == "combined":
            hits = _cascade_search(db, q["query"], embedding, top_k)
        else:
            hits = db.search_lessons(embedding, limit=top_k, method=method)
        hit_ids = [h["document_id"] for h in hits]
        sims = [h["similarity"] for h in hits]

        rank = None
        if q["expected_doc_id"] in hit_ids:
            rank = hit_ids.index(q["expected_doc_id"]) + 1

        # For combined, compute gap from concepts results (cascade gate signal)
        if method == "combined":
            concepts_hits_for_gap = db.search_lessons(embedding, limit=10, method="concepts")
            concepts_sims = [h["similarity"] for h in concepts_hits_for_gap]
            gap = concepts_sims[0] - concepts_sims[4] if len(concepts_sims) >= 5 else 0
        else:
            gap = sims[0] - sims[4] if len(sims) >= 5 else 0
        band = classify_band(gap)

        result = {
            "id": q["id"],
            "query": q["query"],
            "category": q["category"],
            "tags": q.get("tags", []),
            "expected_doc_id": q["expected_doc_id"],
            "expected_title": q["expected_title"],
            "rank": rank,
            "band": band,
            "gap": round(gap, 4),
            "top_hits": [
                {"title": h["title"], "similarity": round(h["similarity"], 4), "doc_id": h["document_id"]}
                for h in hits[:5]
            ],
        }
        results.append(result)

    search_time = time.time() - t0
    if not quiet:
        print(f"done ({search_time:.1f}s)")

    categories = sorted(set(r["category"] for r in results))
    metrics = compute_metrics(results, categories, top_k)

    return {
        "metrics": metrics,
        "results": results,
        "missing_from_index": missing_from_index,
        "embed_time": embed_time,
        "search_time": search_time,
        "qfile": qfile,
        "method": method,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate search quality against test questions")
    parser.add_argument("--questions", type=str, default=DEFAULT_QUESTIONS, help="Path to question file")
    parser.add_argument("--top-k", type=int, default=10, help="Number of search results to retrieve")
    parser.add_argument("--band", type=str, default=None,
                        help="Filter to specificity band(s): low, mid, mid-high, high (comma-separated)")
    parser.add_argument("--method", type=str, default="default",
                        help="Embedding method: 'default' or 'concepts' (distinctive)")
    parser.add_argument("--verbose", action="store_true", help="Show details for every query")
    parser.add_argument("--failures-only", action="store_true", help="Only show queries where expected doc was not found")
    parser.add_argument("--output", type=str, default=None, help="Write detailed results to JSON file")
    args = parser.parse_args()

    # Parse band filter
    band_filter = None
    if args.band:
        band_filter = set(b.strip() for b in args.band.split(","))
        invalid = band_filter - set(BAND_ORDER)
        if invalid:
            print(f"ERROR: Unknown band(s): {invalid}. Valid: {', '.join(BAND_ORDER)}", file=sys.stderr)
            sys.exit(1)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment", file=sys.stderr)
        sys.exit(1)

    if not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    db = Database(database_url)
    db.initialize()

    eval_result = run_eval(db, questions_path=args.questions, top_k=args.top_k, method=args.method)
    metrics = eval_result["metrics"]
    results = eval_result["results"]
    missing_from_index = eval_result["missing_from_index"]
    qfile = eval_result["qfile"]

    # Apply band filter if requested
    if band_filter:
        pre_count = len(results)
        results = [r for r in results if r["band"] in band_filter]
        print(f"Band filter ({args.band}): {pre_count} -> {len(results)} queries")
        if not results:
            print("No queries remain after band filter.")
            db.close()
            return
        # Recompute metrics for filtered set
        categories = sorted(set(r["category"] for r in results))
        metrics = compute_metrics(results, categories, args.top_k)

    # Print report
    print_report(metrics, results, missing_from_index, args, qfile,
                 eval_result["embed_time"], eval_result["search_time"], band_filter)

    # Optionally write JSON output
    if args.output:
        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "questions_file": os.path.normpath(args.questions),
            "top_k": args.top_k,
            "band_filter": list(band_filter) if band_filter else None,
            "metrics": metrics,
            "results": results,
            "missing_from_index": missing_from_index,
        }
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nDetailed results written to {args.output}")

    db.close()


def compute_metrics(results: list[dict], categories: list[str], top_k: int) -> dict:
    """Compute P@1, P@3, P@5, MRR overall, per category, and per band."""

    def calc(subset):
        n = len(subset)
        if n == 0:
            return {"count": 0, "p_at_1": 0, "p_at_3": 0, "p_at_5": 0, "mrr": 0, "found_rate": 0}
        p1 = sum(1 for r in subset if r["rank"] == 1) / n
        p3 = sum(1 for r in subset if r["rank"] is not None and r["rank"] <= 3) / n
        p5 = sum(1 for r in subset if r["rank"] is not None and r["rank"] <= 5) / n
        found = sum(1 for r in subset if r["rank"] is not None) / n
        rr_sum = sum(1.0 / r["rank"] for r in subset if r["rank"] is not None)
        mrr = rr_sum / n
        return {"count": n, "p_at_1": p1, "p_at_3": p3, "p_at_5": p5, "mrr": mrr, "found_rate": found}

    metrics = {"overall": calc(results)}
    for cat in categories:
        subset = [r for r in results if r["category"] == cat]
        metrics[cat] = calc(subset)

    # Per-band metrics
    metrics["bands"] = {}
    for band in BAND_ORDER:
        subset = [r for r in results if r["band"] == band]
        if subset:
            metrics["bands"][band] = calc(subset)

    # Per-tag metrics
    all_tags = sorted(set(tag for r in results for tag in r.get("tags", [])))
    if all_tags:
        metrics["tags"] = {}
        for tag in all_tags:
            subset = [r for r in results if tag in r.get("tags", [])]
            metrics["tags"][tag] = calc(subset)

    return metrics


def print_report(metrics, results, missing_from_index, args, qfile, embed_time, search_time, band_filter):
    """Print the evaluation report."""
    print("\n" + "=" * 70)
    method_label = args.method if hasattr(args, 'method') else "default"
    print(f"SEARCH EVALUATION REPORT — method: {method_label}")
    print("=" * 70)
    print(f"  Questions file: version {qfile.get('version', '?')}, {len(qfile['questions'])} total queries")
    print(f"  Evaluated: {len(results)} queries, top-{args.top_k}")
    if band_filter:
        print(f"  Band filter: {', '.join(sorted(band_filter))}")
    print(f"  Timing: embed {embed_time:.1f}s, search {search_time:.1f}s")
    if missing_from_index:
        print(f"  Skipped: {len(missing_from_index)} queries (expected doc not in index)")

    # Metrics table
    print(f"\n{'Category':<12} {'Count':>6} {'P@1':>7} {'P@3':>7} {'P@5':>7} {'MRR':>7} {'Found':>7}")
    print("-" * 60)
    for key in ["overall"] + sorted(k for k in metrics if k not in ("overall", "bands")):
        m = metrics[key]
        label = key.upper() if key == "overall" else key
        print(f"{label:<12} {m['count']:>6} {m['p_at_1']:>7.1%} {m['p_at_3']:>7.1%} {m['p_at_5']:>7.1%} {m['mrr']:>7.3f} {m['found_rate']:>7.1%}")

    # Band breakdown
    if metrics.get("bands"):
        print(f"\n{'Band':<12} {'Count':>6} {'P@1':>7} {'P@3':>7} {'P@5':>7} {'MRR':>7} {'Found':>7}")
        print("-" * 60)
        for band in BAND_ORDER:
            if band in metrics["bands"]:
                m = metrics["bands"][band]
                print(f"{band:<12} {m['count']:>6} {m['p_at_1']:>7.1%} {m['p_at_3']:>7.1%} {m['p_at_5']:>7.1%} {m['mrr']:>7.3f} {m['found_rate']:>7.1%}")

    # Tag breakdown
    if metrics.get("tags"):
        print(f"\n{'Tag':<25} {'Count':>6} {'P@1':>7} {'P@3':>7} {'P@5':>7} {'MRR':>7} {'Found':>7}")
        print("-" * 70)
        for tag in sorted(metrics["tags"]):
            m = metrics["tags"][tag]
            print(f"{tag:<25} {m['count']:>6} {m['p_at_1']:>7.1%} {m['p_at_3']:>7.1%} {m['p_at_5']:>7.1%} {m['mrr']:>7.3f} {m['found_rate']:>7.1%}")

    # Failures
    failures = [r for r in results if r["rank"] is None]
    near_misses = [r for r in results if r["rank"] is not None and r["rank"] > 1]

    if failures:
        print(f"\n--- FAILURES ({len(failures)} queries, expected not in top-{args.top_k}) ---")
        for r in failures:
            tags_str = ",".join(r.get("tags", [])) if r.get("tags") else ""
            tag_label = f" [{tags_str}]" if tags_str else ""
            print(f"\n  [{r['id']}] ({r['category']}, {r['band']}{tag_label}) {r['query']}")
            print(f"    Expected: {r['expected_title']}")
            print(f"    Got instead:")
            for i, h in enumerate(r["top_hits"][:3]):
                print(f"      {i+1}. {h['title']} ({h['similarity']:.4f})")

    if near_misses and not args.failures_only:
        print(f"\n--- NEAR MISSES ({len(near_misses)} queries, found but rank > 1) ---")
        for r in sorted(near_misses, key=lambda r: r["rank"], reverse=True):
            tags_str = ",".join(r.get("tags", [])) if r.get("tags") else ""
            tag_label = f" [{tags_str}]" if tags_str else ""
            print(f"  [{r['id']}] rank={r['rank']} ({r['category']}, {r['band']}{tag_label}) {r['query']}")
            print(f"    Expected: {r['expected_title']}")

    if args.verbose and not args.failures_only:
        print(f"\n--- ALL RESULTS ---")
        for r in results:
            rank_str = f"rank={r['rank']}" if r['rank'] else "NOT FOUND"
            print(f"  [{r['id']}] {rank_str} ({r['category']}, {r['band']}) {r['query']}")

    if missing_from_index:
        unique_titles = sorted(set(q["expected_title"] for q in missing_from_index))
        print(f"\n--- NOT IN INDEX ({len(unique_titles)} lessons) ---")
        for title in unique_titles[:20]:
            print(f"  - {title}")
        if len(unique_titles) > 20:
            print(f"  ... and {len(unique_titles) - 20} more")

    print()


if __name__ == "__main__":
    main()
