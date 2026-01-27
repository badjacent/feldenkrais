#!/usr/bin/env python3
"""
Backfill script to set lesson_date and lesson_index for existing FMTT documents.

This is a one-time migration script. Delete after use.
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Add src/kb to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kb"))

from database import Database


def parse_lesson_date_and_index(title: str) -> tuple[str | None, str | None]:
    """
    Extract lesson_date and lesson_index from a lesson title.

    Handles two date formats:
    - "Day 1: Jan 17, Chapter 1" (month name, assume 2025)
    - "Day 1 2/7/25 Chapter 1" (M/D/YY format)

    Returns (lesson_date as YYYY-MM-DD, lesson_index as "Chapter N")
    """
    lesson_date = None
    lesson_index = None

    # Extract chapter for lesson_index
    chapter_match = re.search(r'Chapter\s*(\d+)', title, re.IGNORECASE)
    if chapter_match:
        lesson_index = f"Chapter {chapter_match.group(1)}"

    # Try to extract date - format 1: M/D/YY (e.g., "2/7/25")
    date_match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2})', title)
    if date_match:
        month, day, year = date_match.groups()
        full_year = 2000 + int(year)
        try:
            parsed_date = datetime(full_year, int(month), int(day))
            lesson_date = parsed_date.strftime('%Y-%m-%d')
        except ValueError:
            pass
    else:
        # Try format 2: "Jan 17" (month name + day, assume 2025)
        month_match = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2})', title, re.IGNORECASE)
        if month_match:
            month_name, day_num = month_match.groups()
            month_map = {
                'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
            }
            month_num = month_map.get(month_name.lower())
            if month_num:
                try:
                    parsed_date = datetime(2025, month_num, int(day_num))
                    lesson_date = parsed_date.strftime('%Y-%m-%d')
                except ValueError:
                    pass

    return lesson_date, lesson_index


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backfill lesson_date and lesson_index for FMTT documents")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL connection URL")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without making changes")
    args = parser.parse_args()

    database_url = args.database_url
    if not database_url:
        print("DATABASE_URL required (use --database-url or DATABASE_URL env var)")
        sys.exit(1)

    db = Database(database_url)

    # Query FMTT documents (source_id = 3)
    FM_SOURCE_ID = 3
    with db.get_cursor(commit=False) as cursor:
        cursor.execute(
            "SELECT id, title, lesson_date, lesson_index FROM document WHERE source_id = %s ORDER BY title",
            (FM_SOURCE_ID,)
        )
        documents = [dict(row) for row in cursor.fetchall()]

    print(f"Found {len(documents)} FMTT documents")

    updated = 0
    skipped = 0
    no_data = 0

    for doc in documents:
        doc_id = str(doc['id'])
        title = doc['title']
        existing_date = doc['lesson_date']
        existing_index = doc['lesson_index']

        # Skip if already has both fields
        if existing_date and existing_index:
            skipped += 1
            continue

        lesson_date, lesson_index = parse_lesson_date_and_index(title)

        # Use existing values if we can't parse new ones
        if not lesson_date:
            lesson_date = existing_date
        if not lesson_index:
            lesson_index = existing_index

        if not lesson_date and not lesson_index:
            print(f"  SKIP (no data found): {title}")
            no_data += 1
            continue

        if args.dry_run:
            print(f"  Would update: {title}")
            print(f"    lesson_date: {lesson_date}")
            print(f"    lesson_index: {lesson_index}")
        else:
            db.update_document_lesson_fields(doc_id, lesson_date, lesson_index)
            print(f"  Updated: {title} -> date={lesson_date}, index={lesson_index}")

        updated += 1

    print()
    print(f"Summary:")
    print(f"  Total: {len(documents)}")
    print(f"  Updated: {updated}")
    print(f"  Skipped (already set): {skipped}")
    print(f"  No data found: {no_data}")

    if args.dry_run:
        print("\n(Dry run - no changes made)")

    db.close()


if __name__ == "__main__":
    main()
