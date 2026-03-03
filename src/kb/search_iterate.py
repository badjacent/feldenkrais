"""Auto-iteration pipeline for search document optimization.

Generates LLM-written search documents, evaluates search quality,
analyzes failures, and iterates on the generation prompt.

Usage:
    # Record baseline metrics (version 0 = current embeddings)
    .venv/bin/python src/kb/search_iterate.py --baseline

    # Dry-run: generate 3 search docs and print them
    .venv/bin/python src/kb/search_iterate.py --step generate --version 1 --limit 3

    # Single step
    .venv/bin/python src/kb/search_iterate.py --step generate --version 1
    .venv/bin/python src/kb/search_iterate.py --step embed --version 1
    .venv/bin/python src/kb/search_iterate.py --step eval --version 1
    .venv/bin/python src/kb/search_iterate.py --step analyze --version 1

    # Full auto loop
    .venv/bin/python src/kb/search_iterate.py --auto --max-iterations 5

    # View history
    .venv/bin/python src/kb/search_iterate.py --history

    # Compare versions
    .venv/bin/python src/kb/search_iterate.py --compare 0 1
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from database import Database
from llm import LLM
from run_search_eval import run_eval, BAND_ORDER
from search_doc import (
    generate_all_search_docs,
    embed_search_docs,
    load_prompt,
    load_search_doc,
    PROMPTS_DIR,
)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
ITERATIONS_DIR = DATA_DIR / "search_iterations"


def load_history() -> dict:
    """Load iteration history from disk."""
    path = ITERATIONS_DIR / "history.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"iterations": []}


def save_history(history: dict) -> None:
    """Save iteration history to disk."""
    ITERATIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = ITERATIONS_DIR / "history.json"
    path.write_text(json.dumps(history, indent=2))


def save_iteration_detail(version: int, data: dict) -> None:
    """Save detailed per-iteration data."""
    ITERATIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = ITERATIONS_DIR / f"iter_{version}.json"
    path.write_text(json.dumps(data, indent=2))


def record_baseline(db: Database) -> dict:
    """Record baseline (version 0) metrics using current embeddings."""
    print("Recording baseline (version 0) metrics...")
    eval_result = run_eval(db)

    metrics = eval_result["metrics"]
    results = eval_result["results"]

    entry = {
        "version": 0,
        "prompt": "baseline (build_summary_from_condensed)",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    }

    history = load_history()
    # Replace existing v0 if present
    history["iterations"] = [e for e in history["iterations"] if e["version"] != 0]
    history["iterations"].insert(0, entry)
    save_history(history)

    # Save detail
    save_iteration_detail(0, {
        "version": 0,
        "timestamp": entry["timestamp"],
        "metrics": metrics,
        "results": results,
        "missing_from_index": eval_result["missing_from_index"],
    })

    _print_metrics_summary(metrics, "Baseline (v0)")
    return entry


def record_eval(db: Database, version: int) -> dict:
    """Run eval and record metrics for a version."""
    print(f"\nRunning eval for v{version}...")
    eval_result = run_eval(db)

    metrics = eval_result["metrics"]
    results = eval_result["results"]

    entry = {
        "version": version,
        "prompt": f"data/search_doc_prompts/v{version}.md",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    }

    history = load_history()
    history["iterations"] = [e for e in history["iterations"] if e["version"] != version]
    history["iterations"].append(entry)
    history["iterations"].sort(key=lambda e: e["version"])
    save_history(history)

    # Save detail
    save_iteration_detail(version, {
        "version": version,
        "timestamp": entry["timestamp"],
        "metrics": metrics,
        "results": results,
        "missing_from_index": eval_result["missing_from_index"],
    })

    _print_metrics_summary(metrics, f"v{version}")
    return entry


def _print_metrics_summary(metrics: dict, label: str) -> None:
    """Print a compact metrics summary."""
    m = metrics["overall"]
    print(f"\n  {label}: P@1={m['p_at_1']:.1%}  P@3={m['p_at_3']:.1%}  MRR={m['mrr']:.3f}  Found={m['found_rate']:.1%}")
    if metrics.get("bands"):
        parts = []
        for band in BAND_ORDER:
            if band in metrics["bands"]:
                b = metrics["bands"][band]
                parts.append(f"{band}={b['p_at_1']:.1%}")
        print(f"  Bands P@1: {', '.join(parts)}")


def sample_failures(results: list[dict], max_total: int = 30) -> list[dict]:
    """Sample failures stratified by specificity band.

    Target distribution: 15 low, 10 mid, 5 mid-high/high.
    Falls back to available if fewer in a band.
    """
    # Separate failures by band
    by_band = {}
    for r in results:
        if r["rank"] is None or r["rank"] > 1:  # Include near-misses too
            band = r["band"]
            by_band.setdefault(band, []).append(r)

    # Target counts per band
    targets = {"low": 15, "mid": 10, "mid-high": 3, "high": 2}
    sampled = []

    for band in BAND_ORDER:
        available = by_band.get(band, [])
        target = targets.get(band, 0)
        if available:
            n = min(target, len(available))
            sampled.extend(random.sample(available, n))

    # Fill remaining slots from any band
    remaining = max_total - len(sampled)
    if remaining > 0:
        sampled_ids = {r["id"] for r in sampled}
        all_failures = [r for r in results if (r["rank"] is None or r["rank"] > 1) and r["id"] not in sampled_ids]
        if all_failures:
            sampled.extend(random.sample(all_failures, min(remaining, len(all_failures))))

    return sampled


def build_failure_analysis_prompt(
    failures: list[dict],
    db: Database,
    version: int,
    current_prompt: str,
) -> str:
    """Build the failure analysis prompt for the LLM.

    For each failure, includes the expected lesson's search doc AND
    the top-ranked wrong lesson's search doc for side-by-side comparison.
    """
    parts = [
        "You are analyzing search evaluation failures for a Feldenkrais lesson search system.",
        "Each lesson has a 'search document' — an LLM-generated description designed for semantic search.",
        "When a user query fails to find the right lesson, it means the search documents aren't distinctive enough.",
        "",
        f"There are {len(failures)} failures below. For each one you see:",
        "- The user's search query",
        "- The EXPECTED lesson's search document (the one that should have been found)",
        "- The TOP-RANKED lesson's search document (what was found instead)",
        "",
        "## Current generation prompt",
        "```",
        current_prompt,
        "```",
        "",
        "## Failures",
    ]

    for i, failure in enumerate(failures):
        parts.append(f"\n### Failure {i+1}")
        parts.append(f"**Query**: {failure['query']}")
        parts.append(f"**Category**: {failure['category']}, **Band**: {failure['band']}")
        parts.append(f"**Expected**: {failure['expected_title']}")

        # Load expected lesson's search doc
        expected_doc = load_search_doc(db, failure["expected_doc_id"], version)
        if expected_doc:
            parts.append(f"\n**Expected lesson's search doc**:\n{expected_doc}")
        else:
            parts.append("\n**Expected lesson's search doc**: [not available]")

        # Load top-ranked wrong lesson's search doc
        if failure["top_hits"]:
            top_hit = failure["top_hits"][0]
            if top_hit["doc_id"] != failure["expected_doc_id"]:
                wrong_doc = load_search_doc(db, top_hit["doc_id"], version)
                if wrong_doc:
                    parts.append(f"\n**Top-ranked lesson ({top_hit['title']}) search doc**:\n{wrong_doc}")

        rank_str = f"rank={failure['rank']}" if failure["rank"] else "NOT FOUND in top-10"
        parts.append(f"\n**Result**: {rank_str}")

    parts.extend([
        "",
        "## Your task",
        "",
        "Analyze these failures and produce a revised search doc generation prompt.",
        "",
        "1. Identify patterns across failures:",
        "   - What generic content appears in BOTH expected and wrong search docs, making them hard to distinguish?",
        "   - What distinctive signals are MISSING from the expected lesson's search doc?",
        "   - Are there category-specific patterns (action queries fail differently than body queries)?",
        "",
        "2. For each pattern, suggest a concrete change to the generation prompt.",
        "",
        "3. Write the complete revised prompt. Keep the same overall structure but incorporate your improvements.",
        "   The prompt should still be 3-6 paragraphs of instruction for generating search docs.",
        "",
        "Respond with JSON matching this structure:",
        "```json",
        '{',
        '  "patterns": [',
        '    {"pattern": "description of a failure pattern", "fix": "how the prompt should change"}',
        '  ],',
        '  "revised_prompt": "the complete new prompt text"',
        '}',
        "```",
    ])

    return "\n".join(parts)


def analyze_failures(db: Database, version: int, results: list[dict]) -> dict:
    """Analyze eval failures and generate a revised prompt.

    Uses claude-sonnet for strong reasoning on this single expensive call.
    """
    current_prompt = load_prompt(version)

    failures = sample_failures(results)
    print(f"Sampled {len(failures)} failures for analysis")

    if not failures:
        print("No failures to analyze!")
        return {"patterns": [], "revised_prompt": current_prompt}

    analysis_prompt = build_failure_analysis_prompt(failures, db, version, current_prompt)

    print("Running failure analysis (claude-sonnet)...")
    llm = LLM(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        default_max_tokens=8192,
    )

    response = llm.complete(analysis_prompt)

    # Parse JSON from response (may be wrapped in ```json blocks)
    text = response.strip()
    if text.startswith("```"):
        # Strip markdown code fence
        lines = text.split("\n")
        lines = lines[1:]  # remove opening ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        analysis = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"WARNING: Failed to parse analysis JSON: {e}", file=sys.stderr)
        print(f"Raw response:\n{response[:500]}", file=sys.stderr)
        return {"patterns": [], "revised_prompt": current_prompt, "raw_response": response}

    # Save the revised prompt
    next_version = version + 1
    revised_prompt = analysis.get("revised_prompt", "")
    if revised_prompt:
        prompt_path = PROMPTS_DIR / f"v{next_version}.md"
        prompt_path.write_text(revised_prompt)
        print(f"Saved revised prompt to {prompt_path}")

    patterns = analysis.get("patterns", [])
    print(f"Found {len(patterns)} failure patterns:")
    for p in patterns:
        print(f"  - {p.get('pattern', '?')}")

    return {
        "patterns": patterns,
        "revised_prompt": revised_prompt,
        "failure_sample_ids": [f["id"] for f in failures],
        "failure_count": len(failures),
    }


def run_auto_loop(db: Database, start_version: int = 1, max_iterations: int = 5,
                  convergence_threshold: float = 0.01) -> None:
    """Run the full auto-iteration loop.

    For each iteration:
    1. Generate search docs with current prompt
    2. Embed them
    3. Run eval
    4. Check convergence
    5. Analyze failures → produce next prompt
    """
    history = load_history()
    prev_p1 = None
    if history["iterations"]:
        last = history["iterations"][-1]
        prev_p1 = last["metrics"]["overall"]["p_at_1"]
        print(f"Previous best P@1: {prev_p1:.1%} (v{last['version']})")

    for version in range(start_version, start_version + max_iterations):
        print(f"\n{'='*70}")
        print(f"ITERATION v{version}")
        print(f"{'='*70}")

        # 1. Generate search docs
        print(f"\n--- Step 1: Generate search docs (v{version}) ---")
        gen_result = generate_all_search_docs(db, version)
        print(f"Generated: {gen_result['generated']}, cached: {gen_result['cached']}, errors: {gen_result['errors']}")

        # 2. Embed
        print(f"\n--- Step 2: Embed search docs ---")
        embed_result = embed_search_docs(db, version)

        # 3. Eval
        print(f"\n--- Step 3: Evaluate ---")
        entry = record_eval(db, version)
        current_p1 = entry["metrics"]["overall"]["p_at_1"]

        # Update history entry with generation/embed info
        history = load_history()
        for e in history["iterations"]:
            if e["version"] == version:
                e["generation"] = gen_result
                e["embedding"] = embed_result
                break
        save_history(history)

        # 4. Check convergence
        if prev_p1 is not None:
            improvement = current_p1 - prev_p1
            print(f"\n  P@1 change: {prev_p1:.1%} -> {current_p1:.1%} ({improvement:+.1%})")
            if improvement < convergence_threshold and version > start_version:
                print(f"\n  Convergence: improvement ({improvement:.3f}) below threshold ({convergence_threshold}). Stopping.")
                break
        prev_p1 = current_p1

        # 5. Analyze failures → next prompt
        print(f"\n--- Step 4: Analyze failures ---")
        iter_detail_path = ITERATIONS_DIR / f"iter_{version}.json"
        iter_data = json.loads(iter_detail_path.read_text())
        results = iter_data["results"]

        analysis = analyze_failures(db, version, results)

        # Update iteration detail with analysis
        iter_data["analysis"] = analysis
        save_iteration_detail(version, iter_data)

        # Update history with patterns
        history = load_history()
        for e in history["iterations"]:
            if e["version"] == version:
                e["patterns_found"] = [p.get("pattern", "") for p in analysis.get("patterns", [])]
                break
        save_history(history)

        if not analysis.get("revised_prompt"):
            print("No revised prompt generated. Stopping.")
            break

    print(f"\nAuto-iteration complete. Run --history to see results.")


def show_history() -> None:
    """Display iteration history."""
    history = load_history()
    if not history["iterations"]:
        print("No iterations recorded yet.")
        return

    print(f"\n{'Ver':<5} {'P@1':>7} {'P@3':>7} {'MRR':>7} {'Found':>7}  {'Prompt'}")
    print("-" * 70)
    for entry in history["iterations"]:
        m = entry["metrics"]["overall"]
        prompt = entry.get("prompt", "?")
        if len(prompt) > 40:
            prompt = "..." + prompt[-37:]
        print(f"v{entry['version']:<4} {m['p_at_1']:>7.1%} {m['p_at_3']:>7.1%} {m['mrr']:>7.3f} {m['found_rate']:>7.1%}  {prompt}")

    # Band breakdown for each version
    print(f"\n{'Ver':<5}", end="")
    for band in BAND_ORDER:
        print(f" {band:>10}", end="")
    print()
    print("-" * 50)
    for entry in history["iterations"]:
        bands = entry["metrics"].get("bands", {})
        print(f"v{entry['version']:<4}", end="")
        for band in BAND_ORDER:
            if band in bands:
                print(f" {bands[band]['p_at_1']:>10.1%}", end="")
            else:
                print(f" {'—':>10}", end="")
        print()


def compare_versions(v1: int, v2: int) -> None:
    """Compare metrics between two versions."""
    history = load_history()
    entries = {e["version"]: e for e in history["iterations"]}

    if v1 not in entries or v2 not in entries:
        available = sorted(entries.keys())
        print(f"ERROR: Need both versions recorded. Available: {available}", file=sys.stderr)
        sys.exit(1)

    e1, e2 = entries[v1], entries[v2]
    m1, m2 = e1["metrics"]["overall"], e2["metrics"]["overall"]

    print(f"\n{'Metric':<12} {'v' + str(v1):>8} {'v' + str(v2):>8} {'Delta':>8}")
    print("-" * 40)
    for key, label in [("p_at_1", "P@1"), ("p_at_3", "P@3"), ("p_at_5", "P@5"), ("mrr", "MRR"), ("found_rate", "Found")]:
        val1, val2 = m1[key], m2[key]
        delta = val2 - val1
        fmt = ".1%" if key != "mrr" else ".3f"
        print(f"{label:<12} {val1:>8{fmt}} {val2:>8{fmt}} {delta:>+8{fmt}}")

    # Band comparison
    b1, b2 = e1["metrics"].get("bands", {}), e2["metrics"].get("bands", {})
    print(f"\n{'Band':<12} {'v' + str(v1):>8} {'v' + str(v2):>8} {'Delta':>8}")
    print("-" * 40)
    for band in BAND_ORDER:
        if band in b1 and band in b2:
            val1 = b1[band]["p_at_1"]
            val2 = b2[band]["p_at_1"]
            delta = val2 - val1
            print(f"{band:<12} {val1:>8.1%} {val2:>8.1%} {delta:>+8.1%}")

    # Category comparison
    cats = sorted(set(k for k in list(e1["metrics"].keys()) + list(e2["metrics"].keys())
                      if k not in ("overall", "bands")))
    if cats:
        print(f"\n{'Category':<12} {'v' + str(v1):>8} {'v' + str(v2):>8} {'Delta':>8}")
        print("-" * 40)
        for cat in cats:
            c1 = e1["metrics"].get(cat, {})
            c2 = e2["metrics"].get(cat, {})
            if c1 and c2:
                val1, val2 = c1["p_at_1"], c2["p_at_1"]
                delta = val2 - val1
                print(f"{cat:<12} {val1:>8.1%} {val2:>8.1%} {delta:>+8.1%}")


def main():
    parser = argparse.ArgumentParser(description="Search document auto-iteration pipeline")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--baseline", action="store_true", help="Record baseline (v0) metrics")
    group.add_argument("--step", choices=["generate", "embed", "eval", "analyze"],
                       help="Run a single step for a version")
    group.add_argument("--auto", action="store_true", help="Run full auto-iteration loop")
    group.add_argument("--history", action="store_true", help="Show iteration history")
    group.add_argument("--compare", nargs=2, type=int, metavar=("V1", "V2"),
                       help="Compare two versions")

    parser.add_argument("--version", type=int, help="Version number for --step")
    parser.add_argument("--limit", type=int, help="Limit lessons for generate step")
    parser.add_argument("--max-iterations", type=int, default=5, help="Max iterations for --auto")
    parser.add_argument("--start-version", type=int, default=1, help="Starting version for --auto")
    args = parser.parse_args()

    # Commands that don't need DB
    if args.history:
        show_history()
        return

    if args.compare:
        compare_versions(args.compare[0], args.compare[1])
        return

    # Everything else needs DB
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment", file=sys.stderr)
        sys.exit(1)

    db = Database(database_url)
    db.initialize()

    try:
        if args.baseline:
            record_baseline(db)

        elif args.step:
            if args.version is None:
                print("ERROR: --version required with --step", file=sys.stderr)
                sys.exit(1)
            v = args.version

            if args.step == "generate":
                dry_run = args.limit is not None and args.limit <= 5
                generate_all_search_docs(db, v, limit=args.limit, dry_run=dry_run)

            elif args.step == "embed":
                embed_search_docs(db, v)

            elif args.step == "eval":
                record_eval(db, v)

            elif args.step == "analyze":
                iter_path = ITERATIONS_DIR / f"iter_{v}.json"
                if not iter_path.exists():
                    print(f"ERROR: No eval results for v{v}. Run --step eval --version {v} first.", file=sys.stderr)
                    sys.exit(1)
                iter_data = json.loads(iter_path.read_text())
                analysis = analyze_failures(db, v, iter_data["results"])
                iter_data["analysis"] = analysis
                save_iteration_detail(v, iter_data)

        elif args.auto:
            run_auto_loop(db, start_version=args.start_version, max_iterations=args.max_iterations)

    finally:
        db.close()


if __name__ == "__main__":
    main()
