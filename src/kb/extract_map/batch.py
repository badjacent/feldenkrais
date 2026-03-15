"""Batch runner for extraction+mapping pipeline.

Usage:
    # Run on 20 random lessons:
    ../../.venv/bin/python -m extract_map.batch --limit 20

    # Run on specific document IDs:
    ../../.venv/bin/python -m extract_map.batch --ids id1 id2 id3

    # Dry run (list lessons, don't process):
    ../../.venv/bin/python -m extract_map.batch --limit 20 --dry-run
"""

import argparse
import json
import logging
import os
import sys
import time

from dotenv import load_dotenv

# Add parent to path for kb imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
import llm_config
from extract_map.pipeline import init, run_extraction, run_mapping

logger = logging.getLogger(__name__)

IR_CACHE_PREFIX = "ir:"


def get_lessons(db, limit=None, ids=None, min_length=3000):
    """Get lessons to process."""
    with db.get_cursor() as cur:
        if ids:
            cur.execute("""
                SELECT d.id, d.title, a.data->>'text' as text
                FROM document d
                JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
                WHERE d.id = ANY(%s)
                  AND a.data->>'text' IS NOT NULL
            """, (ids,))
        else:
            cur.execute("""
                SELECT d.id, d.title, a.data->>'text' as text
                FROM document d
                JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
                WHERE a.data->>'text' IS NOT NULL
                  AND length(a.data->>'text') > %s
                ORDER BY random()
                LIMIT %s
            """, (min_length, limit))
        return cur.fetchall()


def store_ir(db, document_id, ir):
    """Store the IR in the cache table."""
    cache_key = f"{IR_CACHE_PREFIX}{document_id}"
    db.set_cache(cache_key, ir)


def load_ir(db, document_id):
    """Load an IR from the cache table."""
    return db.get_cache(f"{IR_CACHE_PREFIX}{document_id}")


def list_stored_irs(db):
    """List all document IDs that have stored IRs."""
    with db.get_cursor() as cur:
        cur.execute("""
            SELECT key FROM cache
            WHERE key LIKE 'ir:%%'
            ORDER BY key
        """)
        return [r['key'].replace(IR_CACHE_PREFIX, '') for r in cur.fetchall()]


def run_batch(db, lessons, skip_existing=True):
    """Run extraction+mapping on a batch of lessons."""
    existing = set(list_stored_irs(db)) if skip_existing else set()

    results = {"processed": [], "skipped": [], "errors": []}

    for i, lesson in enumerate(lessons):
        doc_id = lesson['id']
        title = lesson['title']

        if doc_id in existing:
            logger.info(f"[{i+1}/{len(lessons)}] SKIP (exists) {title[:60]}")
            results["skipped"].append(doc_id)
            continue

        logger.info(f"[{i+1}/{len(lessons)}] Processing: {title[:60]}")
        t0 = time.time()

        try:
            text = lesson['text']
            extraction = run_extraction(text)
            mapping = run_mapping(text, extraction)

            ir = {
                "document_id": doc_id,
                "title": title,
                "steps": extraction.get("steps", []),
                "body_map": mapping.get("body_map", {}),
                "vocabulary_map": mapping.get("vocabulary_map", []),
                "ontology_gaps": mapping.get("ontology_gaps", []),
            }

            store_ir(db, doc_id, ir)

            elapsed = round(time.time() - t0, 1)
            n_steps = len(ir["steps"])
            logger.info(f"  Done: {n_steps} steps, {elapsed}s")
            results["processed"].append({
                "id": doc_id,
                "title": title,
                "steps": n_steps,
                "elapsed": elapsed,
            })

        except Exception as e:
            logger.error(f"  FAILED: {e}")
            results["errors"].append({
                "id": doc_id,
                "title": title,
                "error": str(e),
            })

    return results


def main():
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env'))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Batch extraction+mapping pipeline")
    parser.add_argument("--limit", type=int, default=20, help="Number of random lessons")
    parser.add_argument("--ids", nargs="+", help="Specific document IDs to process")
    parser.add_argument("--dry-run", action="store_true", help="List lessons only")
    parser.add_argument("--list-irs", action="store_true", help="List stored IRs")
    args = parser.parse_args()

    db = Database(os.environ['DATABASE_URL'])
    llm_config.init(db)
    init(db, llm_config.get_llm)

    if args.list_irs:
        ir_ids = list_stored_irs(db)
        print(f"{len(ir_ids)} stored IRs:")
        for doc_id in ir_ids:
            ir = load_ir(db, doc_id)
            print(f"  {doc_id}  {ir.get('title', '?')[:70]}")
        return

    lessons = get_lessons(db, limit=args.limit, ids=args.ids)
    print(f"Found {len(lessons)} lessons")

    if args.dry_run:
        for l in lessons:
            print(f"  {l['id']}  {len(l['text']):>6} chars  {l['title'][:70]}")
        return

    results = run_batch(db, lessons)
    print(f"\nDone: {len(results['processed'])} processed, "
          f"{len(results['skipped'])} skipped, "
          f"{len(results['errors'])} errors")

    if results['errors']:
        print("\nErrors:")
        for e in results['errors']:
            print(f"  {e['title'][:60]}: {e['error']}")


if __name__ == "__main__":
    main()
