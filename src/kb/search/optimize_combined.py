"""
Optimize combined search parameters via grid search over eval questions.

Uses a cascade/confidence-gate approach: when the distinctive (concepts) search
has a clear top result (high similarity gap), trust it. When it's ambiguous,
fall back to another strategy. Grid-searches over the gate threshold, fallback
method, and tag-boost magnitude to maximize P@1 and MRR.

Designed to be re-run whenever the lesson set, question set, or embeddings
change. Outputs a JSON report with full grid results and recommended parameters.

Architecture:
    1. Embed all eval queries (once, cached in memory)
    2. For each query, pre-compute: default top-10, concepts top-10, tag hits
    3. For each parameter combo, simulate the cascade and score against expected

Parameters searched:
    - gate_threshold: similarity gap (rank1 - rank5 from concepts) above which
      we trust distinctive alone. Range: 0.005 to 0.10 in steps.
    - fallback: what to use when distinctive is NOT confident.
      Options: 'default', 'rrf' (RRF of default+concepts+tags)
    - tag_boost: per-tag RRF bonus when fallback='rrf'. Range: 0.0 to 0.01.
    - concepts_weight: RRF weight for concepts when fallback='rrf'. Range: 1-3.

Usage:
    .venv/bin/python src/kb/search/optimize_combined.py
    .venv/bin/python src/kb/search/optimize_combined.py --questions data/search_eval_fp_free.json
    .venv/bin/python src/kb/search/optimize_combined.py --output data/combined_optimization.json
    .venv/bin/python src/kb/search/optimize_combined.py --top-n 20  # show top 20 combos
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

DEFAULT_QUESTIONS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "search_eval_fp_free.json")


def precompute_searches(db: Database, questions: list[dict], top_k: int = 10) -> list[dict]:
    """Embed all queries and pre-compute search results for each method.

    Returns list of dicts, one per question, each containing:
        - question fields (id, query, expected_doc_id, etc.)
        - default_hits: list of {document_id, similarity, title}
        - concepts_hits: list
        - tag_hits: list
        - concepts_gap: float (rank1 - rank5 similarity from concepts)
        - default_gap: float (rank1 - rank5 similarity from default)
    """
    print(f"Embedding {len(questions)} queries...", end=" ", flush=True)
    t0 = time.time()
    query_texts = [q["query"] for q in questions]

    CHUNK_SIZE = 100
    all_embeddings = []
    for i in range(0, len(query_texts), CHUNK_SIZE):
        chunk = query_texts[i:i + CHUNK_SIZE]
        embeddings, _ = get_embeddings(chunk, task_type="RETRIEVAL_QUERY")
        all_embeddings.extend(embeddings)
    print(f"done ({time.time() - t0:.1f}s)")

    print(f"Running searches (3 methods x {len(questions)} queries)...", end=" ", flush=True)
    t0 = time.time()

    precomputed = []
    for q, embedding in zip(questions, all_embeddings):
        default_hits = db.search_lessons(embedding, limit=top_k, method="default")
        concepts_hits = db.search_lessons(embedding, limit=top_k, method="concepts")
        terms = [t.strip() for t in q["query"].split() if t.strip()]
        tag_hits = db.search_by_concepts(terms, limit=top_k) if terms else []

        concepts_sims = [h["similarity"] for h in concepts_hits]
        default_sims = [h["similarity"] for h in default_hits]

        precomputed.append({
            **q,
            "default_hits": default_hits,
            "concepts_hits": concepts_hits,
            "tag_hits": tag_hits,
            "concepts_gap": concepts_sims[0] - concepts_sims[4] if len(concepts_sims) >= 5 else 0,
            "default_gap": default_sims[0] - default_sims[4] if len(default_sims) >= 5 else 0,
        })

    print(f"done ({time.time() - t0:.1f}s)")
    return precomputed


def simulate_cascade(precomputed: list[dict], gate_threshold: float,
                     fallback: str, tag_boost: float, concepts_weight: float,
                     top_k: int = 10) -> dict:
    """Simulate cascade search for one parameter combination.

    For each query:
        1. Check concepts_gap against gate_threshold
        2. If gap >= threshold: use concepts results directly
        3. If gap < threshold: use fallback strategy

    Returns metrics dict with p_at_1, p_at_3, p_at_5, mrr, found_rate.
    """
    RRF_K = 60

    p1 = p3 = p5 = found = rr_sum = 0
    n = len(precomputed)

    for q in precomputed:
        # Decide: trust distinctive or fall back?
        if q["concepts_gap"] >= gate_threshold:
            # Confident — use concepts results directly
            hit_ids = [h["document_id"] for h in q["concepts_hits"][:top_k]]
        elif fallback == "default":
            # Fall back to default embedding search
            hit_ids = [h["document_id"] for h in q["default_hits"][:top_k]]
        else:
            # fallback == "rrf": merge with RRF
            scores = {}
            for rank, hit in enumerate(q["default_hits"], 1):
                doc_id = hit["document_id"]
                scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (RRF_K + rank)

            for rank, hit in enumerate(q["concepts_hits"], 1):
                doc_id = hit["document_id"]
                scores[doc_id] = scores.get(doc_id, 0) + concepts_weight / (RRF_K + rank)

            for hit in q["tag_hits"]:
                doc_id = hit["document_id"]
                match_count = hit.get("match_count", 1)
                scores[doc_id] = scores.get(doc_id, 0) + tag_boost * match_count

            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
            hit_ids = [doc_id for doc_id, _ in ranked]

        # Score
        expected = q["expected_doc_id"]
        if expected in hit_ids:
            rank = hit_ids.index(expected) + 1
            found += 1
            rr_sum += 1.0 / rank
            if rank == 1:
                p1 += 1
            if rank <= 3:
                p3 += 1
            if rank <= 5:
                p5 += 1

    return {
        "p_at_1": p1 / n,
        "p_at_3": p3 / n,
        "p_at_5": p5 / n,
        "mrr": rr_sum / n,
        "found_rate": found / n,
        "count": n,
    }


def run_grid_search(precomputed: list[dict], top_k: int = 10) -> list[dict]:
    """Run grid search over cascade parameters.

    Returns list of {params, metrics} dicts sorted by MRR descending.
    """
    # Parameter grid
    gate_thresholds = [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.07, 0.10]
    fallbacks = ["default", "rrf"]
    tag_boosts = [0.0, 0.003, 0.006, 0.01]
    concepts_weights = [1.0, 1.5, 2.0, 3.0]

    # Also test "always distinctive" and "always default" baselines
    results = []

    # Baseline: always distinctive
    m = simulate_cascade(precomputed, gate_threshold=0.0, fallback="default",
                         tag_boost=0, concepts_weight=1, top_k=top_k)
    results.append({
        "params": {"strategy": "always_concepts", "gate_threshold": 0.0},
        "metrics": m,
    })

    # Baseline: always default
    m = simulate_cascade(precomputed, gate_threshold=999.0, fallback="default",
                         tag_boost=0, concepts_weight=1, top_k=top_k)
    results.append({
        "params": {"strategy": "always_default", "gate_threshold": 999.0},
        "metrics": m,
    })

    total = len(gate_thresholds) * (1 + len(tag_boosts) * len(concepts_weights))
    print(f"Grid search: {total} combinations + 2 baselines...", end=" ", flush=True)
    t0 = time.time()

    for gate in gate_thresholds:
        # Fallback = default (no tag_boost / concepts_weight needed)
        m = simulate_cascade(precomputed, gate_threshold=gate, fallback="default",
                             tag_boost=0, concepts_weight=1, top_k=top_k)
        results.append({
            "params": {
                "strategy": "cascade",
                "gate_threshold": gate,
                "fallback": "default",
            },
            "metrics": m,
        })

        # Fallback = rrf (search over tag_boost and concepts_weight)
        for tb in tag_boosts:
            for cw in concepts_weights:
                m = simulate_cascade(precomputed, gate_threshold=gate, fallback="rrf",
                                     tag_boost=tb, concepts_weight=cw, top_k=top_k)
                results.append({
                    "params": {
                        "strategy": "cascade",
                        "gate_threshold": gate,
                        "fallback": "rrf",
                        "tag_boost": tb,
                        "concepts_weight": cw,
                    },
                    "metrics": m,
                })

    print(f"done ({time.time() - t0:.1f}s)")

    # Sort by MRR descending, then P@1 descending
    results.sort(key=lambda r: (r["metrics"]["mrr"], r["metrics"]["p_at_1"]), reverse=True)
    return results


def print_report(results: list[dict], top_n: int = 15):
    """Print a summary of the grid search results."""
    print("\n" + "=" * 90)
    print("CASCADE SEARCH OPTIMIZATION REPORT")
    print("=" * 90)

    # Find baselines
    always_concepts = next(r for r in results if r["params"].get("strategy") == "always_concepts")
    always_default = next(r for r in results if r["params"].get("strategy") == "always_default")

    print("\nBaselines:")
    for label, r in [("Always Distinctive", always_concepts), ("Always Default", always_default)]:
        m = r["metrics"]
        print(f"  {label:<22} P@1={m['p_at_1']:.1%}  P@3={m['p_at_3']:.1%}  P@5={m['p_at_5']:.1%}  MRR={m['mrr']:.3f}  Found={m['found_rate']:.1%}")

    # Top N results
    print(f"\nTop {top_n} parameter combinations:")
    print(f"{'#':>3} {'P@1':>7} {'P@3':>7} {'P@5':>7} {'MRR':>7} {'Found':>7}  Parameters")
    print("-" * 90)

    for i, r in enumerate(results[:top_n], 1):
        m = r["metrics"]
        p = r["params"]
        if p.get("strategy") in ("always_concepts", "always_default"):
            param_str = p["strategy"]
        elif p.get("fallback") == "default":
            param_str = f"gate={p['gate_threshold']:.3f} fallback=default"
        else:
            param_str = (f"gate={p['gate_threshold']:.3f} fallback=rrf "
                        f"cw={p.get('concepts_weight', 1):.1f} tb={p.get('tag_boost', 0):.4f}")
        print(f"{i:>3} {m['p_at_1']:>7.1%} {m['p_at_3']:>7.1%} {m['p_at_5']:>7.1%} {m['mrr']:>7.3f} {m['found_rate']:>7.1%}  {param_str}")

    # Best result
    best = results[0]
    bm = best["metrics"]
    bp = best["params"]
    cm = always_concepts["metrics"]

    print(f"\nBest combo vs Always Distinctive:")
    print(f"  P@1:   {cm['p_at_1']:.1%} -> {bm['p_at_1']:.1%}  ({bm['p_at_1'] - cm['p_at_1']:+.1%})")
    print(f"  MRR:   {cm['mrr']:.3f} -> {bm['mrr']:.3f}  ({bm['mrr'] - cm['mrr']:+.3f})")
    print(f"  Found: {cm['found_rate']:.1%} -> {bm['found_rate']:.1%}  ({bm['found_rate'] - cm['found_rate']:+.1%})")

    # Gate threshold analysis
    print("\nGate threshold sensitivity (fallback=default):")
    print(f"{'Threshold':>10} {'P@1':>7} {'MRR':>7} {'Found':>7} {'%gated':>7}")
    print("-" * 45)

    for r in results:
        p = r["params"]
        if p.get("strategy") != "cascade" or p.get("fallback") != "default":
            continue
        m = r["metrics"]
        # Count how many queries pass the gate
        n_gated = sum(1 for q in precomputed if q["concepts_gap"] >= p["gate_threshold"])
        pct_gated = n_gated / len(precomputed)
        print(f"{p['gate_threshold']:>10.3f} {m['p_at_1']:>7.1%} {m['mrr']:>7.3f} {m['found_rate']:>7.1%} {pct_gated:>7.1%}")


# Module-level for report access
precomputed = []


def main():
    global precomputed

    parser = argparse.ArgumentParser(description="Optimize combined search via grid search")
    parser.add_argument("--questions", type=str, default=DEFAULT_QUESTIONS,
                        help="Path to eval question file")
    parser.add_argument("--top-k", type=int, default=10,
                        help="Number of search results per query")
    parser.add_argument("--top-n", type=int, default=15,
                        help="Show top N parameter combinations")
    parser.add_argument("--output", type=str, default=None,
                        help="Write full grid results to JSON file")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment", file=sys.stderr)
        sys.exit(1)
    if not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    db = Database(database_url)
    db.initialize()

    # Load questions
    questions_path = os.path.normpath(args.questions)
    with open(questions_path) as f:
        qfile = json.load(f)
    questions = qfile["questions"]
    print(f"Loaded {len(questions)} questions from {questions_path}")

    # Filter to searchable questions (expected doc in either index)
    default_ids = set(db.get_embedding_hashes(method="default").keys())
    concepts_ids = set(db.get_embedding_hashes(method="concepts").keys())
    embedded_ids = default_ids | concepts_ids
    searchable = [q for q in questions if q["expected_doc_id"] in embedded_ids]
    skipped = len(questions) - len(searchable)
    if skipped:
        print(f"Skipped {skipped} questions (expected doc not in any index)")
    print(f"Evaluating {len(searchable)} questions (default={len(default_ids)}, concepts={len(concepts_ids)} lessons)")

    # Precompute all searches
    precomputed = precompute_searches(db, searchable, top_k=args.top_k)

    # Run grid search
    results = run_grid_search(precomputed, top_k=args.top_k)

    # Report
    print_report(results, top_n=args.top_n)

    # Write output
    if args.output:
        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "questions_file": questions_path,
            "question_count": len(searchable),
            "top_k": args.top_k,
            "grid_results": results,
            "recommended": results[0],
        }
        output_path = os.path.normpath(args.output)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nFull results written to {output_path}")

    db.close()


if __name__ == "__main__":
    main()
