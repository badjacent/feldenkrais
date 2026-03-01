"""
Run the pass0b condensed pipeline on multiple lessons in parallel,
then build embeddings from the condensed summaries.

Usage:
    .venv/bin/python src/kb/run_pipeline_batch.py --dry-run --limit 50
    .venv/bin/python src/kb/run_pipeline_batch.py --apply --limit 50
    .venv/bin/python src/kb/run_pipeline_batch.py --apply --limit 50 --workers 3
"""

import argparse
import functools
import hashlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Force unbuffered output
print = functools.partial(print, flush=True)

from dotenv import load_dotenv

load_dotenv()

from database import Database
from llm import get_embeddings

# ── Initialize extracted modules (replaces `import web`) ──────────────────
import cache_helpers
import llm_config
import analysis

DATABASE_URL = os.environ.get("DATABASE_URL")
DATA_DIR = Path(__file__).parent.parent.parent / "data"

_db = Database(DATABASE_URL) if DATABASE_URL else None
if _db:
    _db.initialize()

cache_helpers.init(_db)
llm_config.init(_db)
analysis.init(_db, DATA_DIR, llm_config.get_llm)

from cache_helpers import get_cache_key, build_summary_from_condensed
from analysis import run_pass_0b, run_pass_0b_lang, run_pass_0b_condensed


def pick_lessons(db: Database, limit: int) -> list[dict]:
    """Pick lessons that don't have condensed cards cached yet.

    Prioritizes ATM lessons from FM (source_id=3), then other sources.
    Returns list of {doc_id, title, transcript}.
    """
    text_hashes = db.get_all_transcription_hashes()

    # Find which ones already have condensed cache
    candidates = []
    already_condensed = 0

    for doc_id, text_hash in text_hashes.items():
        transcript = db.get_document_transcription(doc_id)
        if not transcript or not transcript.strip():
            continue

        condensed_key = get_cache_key(transcript.strip() + ":pass_0b_condensed_v4")
        cached = db.get_cache(condensed_key)
        if cached and cached.get("segments"):
            already_condensed += 1
            continue

        doc = db.get_document(doc_id)
        if not doc:
            continue

        # Priority: source_id=3 (FM) ATM lessons first
        metadata = doc.get("metadata") or {}
        if isinstance(metadata, str):
            import json
            metadata = json.loads(metadata)
        lesson_type = metadata.get("lesson_type", "")
        is_atm = lesson_type == "ATM"
        is_fm = doc.get("source_id") == 3

        candidates.append({
            "doc_id": str(doc["id"]),
            "title": doc["title"],
            "transcript": transcript.strip(),
            "priority": (0 if (is_fm and is_atm) else 1 if is_fm else 2),
        })

    print(f"Already condensed: {already_condensed}, candidates: {len(candidates)}")

    # Sort by priority, take limit
    candidates.sort(key=lambda x: x["priority"])
    return candidates[:limit]


def run_one_lesson(doc_id: str, title: str, transcript: str) -> dict:
    """Run the full pipeline for one lesson: somatic + lang -> condensed.

    Returns {doc_id, title, success, cached_stages, elapsed, error}.
    """
    t0 = time.time()
    result = {
        "doc_id": doc_id,
        "title": title,
        "success": False,
        "cached_stages": [],
        "elapsed": 0,
        "error": None,
    }

    try:
        # Phase 1: somatic + lang (these share the same Pass A cache)
        somatic_result = run_pass_0b(transcript)
        if somatic_result.get("cached"):
            result["cached_stages"].append("somatic")

        lang_result = run_pass_0b_lang(transcript)
        if lang_result.get("cached"):
            result["cached_stages"].append("lang")

        # Phase 2: condensed synthesis
        condensed = run_pass_0b_condensed(somatic_result, lang_result, transcript)
        if condensed.get("cached"):
            result["cached_stages"].append("condensed")

        seg_count = len(condensed.get("segments", []))
        result["success"] = True
        result["elapsed"] = round(time.time() - t0, 1)
        result["seg_count"] = seg_count

    except Exception as e:
        result["error"] = str(e)
        result["elapsed"] = round(time.time() - t0, 1)

    return result


def main():
    parser = argparse.ArgumentParser(description="Run pass0b pipeline + embed in batch")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show which lessons would be processed")
    group.add_argument("--apply", action="store_true", help="Actually run pipeline and embed")
    parser.add_argument("--limit", type=int, default=50, help="Number of lessons to process (default: 50)")
    parser.add_argument("--workers", type=int, default=3, help="Parallel workers for pipeline (default: 3)")
    parser.add_argument("--skip-pipeline", action="store_true", help="Skip pipeline, only embed already-condensed lessons")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    db = _db

    # Enable LLM kill switch for batch run
    if args.apply and not args.skip_pipeline:
        settings = db.get_cache("settings:llm_enabled")
        if not settings or not settings.get("enabled", False):
            print("Enabling LLM kill switch for batch run...")
            db.set_cache("settings:llm_enabled", {"enabled": True})

    lessons = pick_lessons(db, args.limit)
    print(f"Selected {len(lessons)} lessons to process")

    if not lessons:
        print("Nothing to do.")
        db.close()
        return

    if args.dry_run:
        for i, lesson in enumerate(lessons):
            pri = ["FM ATM", "FM other", "Other"][lesson["priority"]]
            chars = len(lesson["transcript"])
            print(f"  [{i+1}] ({pri}, {chars:,} chars) {lesson['title']}")
        print(f"\nDRY RUN: {len(lessons)} lessons would be processed with {args.workers} workers")
        db.close()
        return

    # --- Phase 1: Run pipeline ---
    if not args.skip_pipeline:
        print(f"\n=== Phase 1: Running pipeline ({args.workers} workers) ===")
        t0_pipeline = time.time()
        succeeded = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(run_one_lesson, l["doc_id"], l["title"], l["transcript"]): l
                for l in lessons
            }

            for future in as_completed(futures):
                result = future.result()
                elapsed = result["elapsed"]
                if result["success"]:
                    succeeded += 1
                    cached = ", ".join(result["cached_stages"]) if result["cached_stages"] else "none"
                    print(f"  OK [{succeeded}/{len(lessons)}] {result['title']} "
                          f"({elapsed}s, {result.get('seg_count', '?')} segs, cached: {cached})")
                else:
                    failed += 1
                    print(f"  FAIL {result['title']}: {result['error']}")

        pipeline_elapsed = time.time() - t0_pipeline
        print(f"\nPipeline done: {succeeded} ok, {failed} failed in {pipeline_elapsed:.0f}s")

    # --- Phase 2: Embed condensed summaries ---
    print(f"\n=== Phase 2: Building embeddings ===")
    to_embed = []
    existing_hashes = db.get_embedding_hashes()

    for lesson in lessons:
        transcript = lesson["transcript"]
        condensed_key = get_cache_key(transcript + ":pass_0b_condensed_v4")
        condensed = db.get_cache(condensed_key)

        if not condensed or not condensed.get("segments"):
            continue

        summary = build_summary_from_condensed(condensed, lesson["title"])
        summary_hash = hashlib.sha256(summary.encode()).hexdigest()[:16]

        if existing_hashes.get(lesson["doc_id"]) == summary_hash:
            continue

        to_embed.append((lesson["doc_id"], summary, summary_hash, lesson["title"]))

    print(f"Lessons with condensed data ready to embed: {len(to_embed)}")

    if not to_embed:
        print("No new embeddings to create.")
        db.close()
        return

    texts = [summary for _, summary, _, _ in to_embed]
    total_chars = sum(len(t) for t in texts)
    print(f"Embedding {len(to_embed)} lessons ({total_chars:,} chars)...", end=" ", flush=True)

    try:
        embeddings, usage = get_embeddings(texts)
        billable = usage.get("billable_characters", total_chars)
    except Exception as e:
        print(f"\nERROR embedding: {e}", file=sys.stderr)
        db.close()
        sys.exit(1)

    embedded = 0
    for (doc_id, summary, summary_hash, title), embedding in zip(to_embed, embeddings):
        try:
            db.upsert_embedding(doc_id, embedding, summary, summary_hash)
            embedded += 1
        except Exception as e:
            print(f"\n  ERROR upserting {title}: {e}", file=sys.stderr)

    print(f"done")
    print(f"\nEmbedded: {embedded}, billable chars: {billable:,}")
    db.close()


if __name__ == "__main__":
    main()
