"""
Run pass_0b_concepts extraction on lessons that don't have v2 cached yet.

Usage:
    .venv/bin/python src/kb/search/run_concept_extraction.py --dry-run
    .venv/bin/python src/kb/search/run_concept_extraction.py --apply
    .venv/bin/python src/kb/search/run_concept_extraction.py --apply --limit 5
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Add src/kb to path for infrastructure imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from database import Database
import cache_helpers
import llm_config
import analysis
from cache_helpers import get_cache_key


def find_lessons_needing_extraction(db: Database, source_id: int | None = None) -> list[dict]:
    """Find lessons with transcripts that don't have v2 concept extraction cached."""
    source_filter = f" AND d.source_id = {source_id}" if source_id else ""

    with db.get_cursor(commit=False) as cursor:
        cursor.execute(f"""
            SELECT a.document_id::text as doc_id,
                   d.title,
                   a.data->>'text' as text
            FROM analysis a
            JOIN document d ON d.id = a.document_id
            JOIN analysis_type at ON at.id = a.type_id AND at.name = 'TRANSCRIPTION'
            WHERE a.data->>'text' IS NOT NULL{source_filter}
        """)
        docs = [dict(row) for row in cursor.fetchall()]

    cache_suffix = ":pass_0b_concepts_v2"
    needs_extraction = []
    already_cached = 0

    for doc in docs:
        text = doc["text"].strip()
        if not text:
            continue
        cache_key = get_cache_key(text + cache_suffix)
        cached = db.get_cache(cache_key)
        if cached and cached.get("concepts"):
            already_cached += 1
            continue
        needs_extraction.append({
            "doc_id": doc["doc_id"],
            "title": doc["title"],
            "transcript": text,
        })

    print(f"Already cached (v2): {already_cached}, needs extraction: {len(needs_extraction)}")
    return needs_extraction


def main():
    parser = argparse.ArgumentParser(description="Run concept extraction on lessons")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be extracted")
    group.add_argument("--apply", action="store_true", help="Run extraction")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N lessons")
    parser.add_argument("--source", type=int, default=None, help="Only process lessons from this source_id")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    db = Database(database_url)
    db.initialize()

    # Initialize modules
    data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data"
    cache_helpers.init(db)
    llm_config.init(db)
    analysis.init(db, data_dir, llm_config.get_llm)

    lessons = find_lessons_needing_extraction(db, source_id=args.source)

    if args.limit:
        lessons = lessons[:args.limit]

    if not lessons:
        print("Nothing to do.")
        db.close()
        return

    if args.dry_run:
        for lesson in lessons:
            print(f"  Would extract: {lesson['title']} ({lesson['doc_id']})")
        print(f"\nDRY RUN: {len(lessons)} lessons would be extracted")
        db.close()
        return

    # Run extraction
    successes = 0
    errors = 0

    for i, lesson in enumerate(lessons, 1):
        print(f"[{i}/{len(lessons)}] {lesson['title']}...", end=" ", flush=True)
        t0 = time.time()
        try:
            result = analysis.run_pass_0b_concepts(lesson["transcript"])
            elapsed = round(time.time() - t0, 1)
            cached = result.get("cached", False)
            summary_len = len(result.get("distinctive_summary", ""))
            n_concepts = len(result.get("concepts", []))
            print(f"{'(cached)' if cached else f'{elapsed}s'} — {n_concepts} concepts, {summary_len} char summary")
            successes += 1
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            print(f"ERROR ({elapsed}s): {e}")
            errors += 1

    print(f"\nDone: {successes} extracted, {errors} errors")
    db.close()


if __name__ == "__main__":
    main()
