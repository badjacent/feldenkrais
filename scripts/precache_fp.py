#!/usr/bin/env python3
"""Pre-cache analysis results for all Feldenkrais Project lessons.

Usage:
    python scripts/precache_fp.py
    python scripts/precache_fp.py --dry-run          # just list lessons, don't run
    python scripts/precache_fp.py --pass-b-provider openai   # use a specific provider for Pass B
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "kb"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


def main():
    parser = argparse.ArgumentParser(description="Pre-cache all FP lesson analyses")
    parser.add_argument("--dry-run", action="store_true", help="List lessons without running analysis")
    parser.add_argument("--pass-a-provider", default=None)
    parser.add_argument("--pass-b-provider", default=None)
    parser.add_argument("--split", action="store_true", help="Split Pass B into two queries")
    args = parser.parse_args()

    from web import (
        _run_analysis, get_available_lessons, load_lesson,
        DEFAULT_LLM, LLM_CONFIGS, get_cache_key, load_cached_analysis,
    )

    pa = args.pass_a_provider or DEFAULT_LLM
    pb = args.pass_b_provider or DEFAULT_LLM

    print(f"=== Pre-cache FP Lessons ===")
    print(f"Pass A: {pa} ({LLM_CONFIGS[pa]['model']})")
    print(f"Pass B: {pb} ({LLM_CONFIGS[pb]['model']})")
    print(f"Split: {args.split}")
    print()

    # Gather all FP lessons
    categories, _ = get_available_lessons()
    lessons = []
    for cat, entries in sorted(categories.items()):
        if not cat.startswith("db:4"):
            continue
        for key, name in entries:
            lessons.append((cat, key, name))

    print(f"Found {len(lessons)} FP lessons")
    print()

    if args.dry_run:
        for i, (cat, key, name) in enumerate(lessons, 1):
            text = load_lesson(cat, key)
            text_len = len(text.strip()) if text else 0
            # Check cache
            cache_suffix = ":split" if args.split else ""
            if args.pass_a_provider and args.pass_a_provider != DEFAULT_LLM:
                cache_suffix += f":a={args.pass_a_provider}"
            if args.pass_b_provider and args.pass_b_provider != DEFAULT_LLM:
                cache_suffix += f":b={args.pass_b_provider}"
            cache_key = get_cache_key(text.strip() + cache_suffix) if text else None
            cached = load_cached_analysis(cache_key) if cache_key else None
            status = "CACHED" if cached else ("READY" if text_len > 100 else "SKIP (no text)")
            print(f"  {i:2}. [{status:>8}] {name} ({text_len:,} chars)")
        return

    # Run analyses
    results = []
    total_t0 = time.time()

    for i, (cat, key, name) in enumerate(lessons, 1):
        print(f"[{i}/{len(lessons)}] {name}")

        text = load_lesson(cat, key)
        if not text or len(text.strip()) < 100:
            print(f"  SKIP: no/short text ({len(text.strip()) if text else 0} chars)")
            results.append({"name": name, "status": "skipped"})
            print()
            continue

        text = text.strip()

        # Check cache
        cache_suffix = ":split" if args.split else ""
        if args.pass_a_provider and args.pass_a_provider != DEFAULT_LLM:
            cache_suffix += f":a={args.pass_a_provider}"
        if args.pass_b_provider and args.pass_b_provider != DEFAULT_LLM:
            cache_suffix += f":b={args.pass_b_provider}"
        cache_key = get_cache_key(text + cache_suffix)
        if load_cached_analysis(cache_key):
            print(f"  CACHED")
            results.append({"name": name, "status": "cached"})
            print()
            continue

        t0 = time.time()
        try:
            result = _run_analysis(
                text,
                split=args.split,
                pass_a_provider=args.pass_a_provider,
                pass_b_provider=args.pass_b_provider,
            )
            elapsed = time.time() - t0
            timings = result.get("timings", {})

            pa_result = result.get("pass_a_result", {})
            groups = pa_result.get("groups", [])
            vl_analyses = result.get("vl_analyses", {})
            frameworks = list(vl_analyses.keys())
            errors = [fw for fw, v in vl_analyses.items() if v.get("status") == "error"]

            print(f"  OK: {elapsed:.0f}s | {len(groups)} groups | frameworks: {', '.join(frameworks)}")
            if timings:
                parts = []
                if timings.get("pass_a"):
                    parts.append(f"A={timings['pass_a']}s")
                for k, v in timings.items():
                    if k.startswith("pass_b_"):
                        parts.append(f"B({k[7:]})={v}s")
                print(f"  Timings: {' | '.join(parts)}")
            if errors:
                for fw in errors:
                    err = vl_analyses[fw].get("error", "")[:100]
                    print(f"  ERROR on {fw}: {err}")

            results.append({"name": name, "status": "complete", "time": elapsed, "frameworks": frameworks})

        except Exception as e:
            elapsed = time.time() - t0
            err_str = str(e)[:200]
            print(f"  FAILED ({elapsed:.0f}s): {err_str}")
            results.append({"name": name, "status": "error", "error": err_str})

        print()

    # Summary
    total_elapsed = time.time() - total_t0
    completed = [r for r in results if r["status"] == "complete"]
    cached = [r for r in results if r["status"] == "cached"]
    errors = [r for r in results if r["status"] == "error"]
    skipped = [r for r in results if r["status"] == "skipped"]

    print(f"=== Summary ===")
    print(f"Total time: {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")
    print(f"Complete: {len(completed)}, Cached: {len(cached)}, Errors: {len(errors)}, Skipped: {len(skipped)}")
    if completed:
        avg = sum(r["time"] for r in completed) / len(completed)
        print(f"Avg time per lesson: {avg:.0f}s")
    if errors:
        print(f"\nFailed lessons:")
        for r in errors:
            print(f"  - {r['name']}: {r.get('error', '')}")


if __name__ == "__main__":
    main()
