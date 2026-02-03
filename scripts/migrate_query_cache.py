#!/usr/bin/env python3
"""One-time migration script to copy QueryAgent cache from files to database.

Migrates ~300 JSON files from data/cache/query_agent/ to the database cache table
using namespaced keys: query_agent:{hash}

Usage:
    uv run python scripts/migrate_query_cache.py
    uv run python scripts/migrate_query_cache.py --dry-run
"""

import json
import os
import sys
from pathlib import Path

# Add src/kb to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kb"))

from dotenv import load_dotenv

load_dotenv()

from database import Database


def migrate_query_cache(dry_run: bool = False):
    """Migrate QueryAgent file cache to database."""
    cache_dir = Path(__file__).parent.parent / "data" / "cache" / "query_agent"

    if not cache_dir.exists():
        print(f"Cache directory not found: {cache_dir}")
        return

    # Get all JSON files
    cache_files = list(cache_dir.glob("*.json"))
    print(f"Found {len(cache_files)} cache files to migrate")

    if not cache_files:
        return

    if dry_run:
        print("\n[DRY RUN] Would migrate:")
        for f in cache_files[:10]:
            hash_key = f.stem
            print(f"  query_agent:{hash_key}")
        if len(cache_files) > 10:
            print(f"  ... and {len(cache_files) - 10} more")
        return

    # Connect to database
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment")
        sys.exit(1)

    db = Database(database_url)

    # Check existing keys to avoid duplicates
    existing_keys = db.get_cache_keys()
    query_agent_keys = {k for k in existing_keys if k.startswith("query_agent:")}
    print(f"Found {len(query_agent_keys)} existing query_agent cache entries in database")

    migrated = 0
    skipped = 0
    errors = 0

    for cache_file in cache_files:
        hash_key = cache_file.stem
        db_key = f"query_agent:{hash_key}"

        # Skip if already in database
        if db_key in query_agent_keys:
            skipped += 1
            continue

        try:
            data = json.loads(cache_file.read_text())
            db.set_cache(db_key, data)
            migrated += 1
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ERROR: {cache_file.name}: {e}")
            errors += 1

    print(f"\nMigration complete:")
    print(f"  Migrated: {migrated}")
    print(f"  Skipped (already exists): {skipped}")
    print(f"  Errors: {errors}")

    db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate QueryAgent cache to database")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be migrated without making changes")
    args = parser.parse_args()

    migrate_query_cache(dry_run=args.dry_run)
