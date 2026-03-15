"""
Backfill document_analysis_gate from existing cache entries.

For each document with a transcription, checks which analysis steps have
cached results and creates gate entries with completed_version set.

Usage:
    .venv/bin/python scripts/backfill_gates.py --dry-run
    .venv/bin/python scripts/backfill_gates.py --apply
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kb"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from database import Database


def backfill(db: Database, dry_run: bool = True):
    # Get all analysis steps with cache suffixes
    steps = db.get_analysis_steps()
    cached_steps = [s for s in steps if s.get("cache_suffix")]

    # Get all transcripts
    with db.get_cursor(commit=False) as cursor:
        cursor.execute("""
            SELECT a.document_id::text as doc_id, a.data->>'text' as text
            FROM analysis a
            WHERE a.type_id = 1 AND a.data->>'text' IS NOT NULL
        """)
        docs = cursor.fetchall()

    print(f"Found {len(docs)} transcribed documents")
    print(f"Checking {len(cached_steps)} cached steps: {[s['name'] for s in cached_steps]}")

    # Get all cache keys in one query
    all_keys = db.get_cache_keys()
    print(f"Found {len(all_keys)} cache keys")

    # Also check for embeddings and concept/spatial index entries
    with db.get_cursor(commit=False) as cursor:
        cursor.execute("SELECT DISTINCT document_id::text FROM lesson_embedding WHERE method = 'default'")
        embedded_docs = {r["document_id"] for r in cursor.fetchall()}
        cursor.execute("SELECT DISTINCT document_id::text FROM lesson_embedding WHERE method = 'concepts'")
        concept_embedded_docs = {r["document_id"] for r in cursor.fetchall()}
        cursor.execute("SELECT DISTINCT document_id::text FROM lesson_concept WHERE category = 'spatial_config'")
        spatial_indexed_docs = {r["document_id"] for r in cursor.fetchall()}
        cursor.execute("SELECT DISTINCT document_id::text FROM lesson_concept WHERE category NOT IN ('spatial_config', 'contact_point', 'constraint', 'working_position')")
        concept_indexed_docs = {r["document_id"] for r in cursor.fetchall()}

    total = 0
    by_step = {}

    for doc in docs:
        text = (doc["text"] or "").strip()
        if not text:
            continue
        doc_id = doc["doc_id"]

        for step in cached_steps:
            cache_key = hashlib.sha256(
                (text + step["cache_suffix"]).encode()
            ).hexdigest()[:16]

            if cache_key in all_keys:
                if not dry_run:
                    # Create gate entry as completed
                    with db.get_cursor() as cursor:
                        cursor.execute("""
                            INSERT INTO document_analysis_gate
                                (document_id, step_name, permitted, completed_version,
                                 completed_at, stale, updated_at)
                            VALUES (%s, %s, TRUE, %s, NOW(), FALSE, NOW())
                            ON CONFLICT (document_id, step_name) DO UPDATE SET
                                completed_version = EXCLUDED.completed_version,
                                completed_at = EXCLUDED.completed_at,
                                stale = FALSE,
                                permitted = TRUE
                        """, (doc_id, step["name"], step["current_version"]))

                total += 1
                by_step[step["name"]] = by_step.get(step["name"], 0) + 1

        # Check non-cache steps: embed, index_concepts, index_spatial
        for step_name, doc_set in [
            ("embed", embedded_docs),
            ("index_concepts", concept_indexed_docs),
            ("index_spatial", spatial_indexed_docs),
        ]:
            if doc_id in doc_set:
                step = next((s for s in steps if s["name"] == step_name), None)
                if not step:
                    continue
                if not dry_run:
                    with db.get_cursor() as cursor:
                        cursor.execute("""
                            INSERT INTO document_analysis_gate
                                (document_id, step_name, permitted, completed_version,
                                 completed_at, stale, updated_at)
                            VALUES (%s, %s, TRUE, %s, NOW(), FALSE, NOW())
                            ON CONFLICT (document_id, step_name) DO UPDATE SET
                                completed_version = EXCLUDED.completed_version,
                                completed_at = EXCLUDED.completed_at,
                                stale = FALSE,
                                permitted = TRUE
                        """, (doc_id, step_name, step["current_version"]))

                total += 1
                by_step[step_name] = by_step.get(step_name, 0) + 1

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Backfilled {total} gate entries:")
    for step_name, count in sorted(by_step.items(), key=lambda x: -x[1]):
        print(f"  {step_name:20s}: {count}")


def main():
    parser = argparse.ArgumentParser(description="Backfill analysis gates from cache")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    db = Database(db_url)
    db.initialize()
    backfill(db, dry_run=not args.apply)
    db.close()


if __name__ == "__main__":
    main()
