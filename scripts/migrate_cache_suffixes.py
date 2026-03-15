"""
One-time migration: rename cache suffixes from old pass_0b_* names to new names.

For each old suffix, finds all cache entries that match, computes the new key
with the new suffix, and copies the data. Old keys are left in place (can be
cleaned up later with --delete-old).

Usage:
    .venv/bin/python scripts/migrate_cache_suffixes.py --dry-run
    .venv/bin/python scripts/migrate_cache_suffixes.py --apply
    .venv/bin/python scripts/migrate_cache_suffixes.py --apply --delete-old
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

SUFFIX_RENAMES = [
    (":pass_a_v3", ":decompose_v3"),
    (":pass_0b_v2", ":somatic_v2"),
    (":pass_0b_lang_v1", ":language_v1"),
    (":pass_0b_word_lang_v3", ":language_patterns_v3"),
    (":pass_0b_concepts_v2", ":concept_tags_v2"),
    (":pass_0b_spatial_v2", ":spatial_v2"),
    (":pass_0b_condensed_v4", ":condense_v4"),
    (":extraction_v2", ":extract_steps_v2"),
    (":mapping_v3", ":map_body_parts_v3"),
]


def migrate(db: Database, dry_run: bool = True, delete_old: bool = False):
    # Get all transcripts
    with db.get_cursor(commit=False) as cursor:
        cursor.execute("""
            SELECT a.document_id::text as doc_id, d.title, a.data->>'text' as text
            FROM analysis a
            JOIN document d ON d.id = a.document_id
            WHERE a.type_id = 1 AND a.data->>'text' IS NOT NULL
        """)
        docs = cursor.fetchall()

    print(f"Found {len(docs)} transcribed documents")

    # Get all existing cache keys
    all_keys = db.get_cache_keys()
    print(f"Found {len(all_keys)} cache keys")

    migrated = 0
    skipped_exists = 0
    skipped_missing = 0
    old_keys_to_delete = []

    for old_suffix, new_suffix in SUFFIX_RENAMES:
        count = 0
        for doc in docs:
            text = (doc["text"] or "").strip()
            if not text:
                continue

            old_key = hashlib.sha256((text + old_suffix).encode()).hexdigest()[:16]
            new_key = hashlib.sha256((text + new_suffix).encode()).hexdigest()[:16]

            if old_key == new_key:
                continue  # same hash (unlikely but possible)

            if old_key not in all_keys:
                skipped_missing += 1
                continue

            if new_key in all_keys:
                skipped_exists += 1
                continue

            # Copy data from old key to new key
            if not dry_run:
                data = db.get_cache(old_key)
                if data:
                    db.set_cache(new_key, data)
                    if delete_old:
                        old_keys_to_delete.append(old_key)

            count += 1
            migrated += 1

        if count > 0:
            print(f"  {old_suffix} → {new_suffix}: {count} entries {'would be ' if dry_run else ''}migrated")

    # Delete old keys if requested
    if old_keys_to_delete and not dry_run:
        with db.get_cursor() as cursor:
            for key in old_keys_to_delete:
                cursor.execute("DELETE FROM cache WHERE key = %s", (key,))
        print(f"  Deleted {len(old_keys_to_delete)} old keys")

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Summary:")
    print(f"  Migrated: {migrated}")
    print(f"  Skipped (new key exists): {skipped_exists}")
    print(f"  Skipped (old key missing): {skipped_missing}")


def main():
    parser = argparse.ArgumentParser(description="Migrate cache suffixes")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--delete-old", action="store_true", help="Delete old cache keys after migration")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    db = Database(db_url)
    db.initialize()

    # Also re-seed analysis_step with new names (drop old rows first)
    if args.apply:
        with db.get_cursor() as cursor:
            # Delete old step names + any gate rows referencing them
            old_names = [
                "pass_a", "pass_0b", "pass_0b_lang", "pass_0b_word_lang",
                "pass_0b_concepts", "pass_0b_spatial", "pass_0b_condensed",
                "embeddings", "concept_index", "spatial_index",
                "extraction", "mapping",
            ]
            cursor.execute(
                "DELETE FROM document_analysis_gate WHERE step_name = ANY(%s)",
                (old_names,),
            )
            gate_deleted = cursor.rowcount
            if gate_deleted:
                print(f"Deleted {gate_deleted} old gate rows")
            cursor.execute(
                "DELETE FROM analysis_step WHERE name = ANY(%s)",
                (old_names,),
            )
            deleted = cursor.rowcount
            if deleted:
                print(f"Deleted {deleted} old analysis_step rows")

        # Re-initialize to insert new seed data
        db.initialize()
        print("Re-seeded analysis_step with new names")

    migrate(db, dry_run=not args.apply, delete_old=args.delete_old)
    db.close()


if __name__ == "__main__":
    main()
