"""
Build spatial concept index from cached pass_0b_spatial results.

Reads spatial configuration results from cache, converts them to concept rows,
and upserts into the lesson_concept table using category-scoped delete
(spatial_config, contact_point, constraint, working_position).

Usage:
    .venv/bin/python src/kb/search/build_spatial_index.py --dry-run
    .venv/bin/python src/kb/search/build_spatial_index.py --apply
"""

import argparse
import os
import sys
from pathlib import Path

# Add src/kb to path for infrastructure imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from database import Database
from cache_helpers import get_cache_key


def build_spatial_concept_rows(spatial_result: dict) -> list[dict]:
    """Convert spatial extraction groups into concept rows for lesson_concept table.

    Categories:
    - spatial_config: full config_signature string per group
    - contact_point: individual hold relationships (part_a holds part_b)
    - constraint: movement restrictions (part: rule)
    - working_position: position + qualifier from contacts
    """
    rows = []
    seen = set()

    for group in spatial_result.get("groups", []):
        # config_signature -> spatial_config
        sig = group.get("config_signature", "")
        if sig:
            key = ("spatial_config", sig)
            if key not in seen:
                rows.append({"category": "spatial_config", "value": sig})
                seen.add(key)

        # holds -> contact_point
        for hold in group.get("holds", []):
            part_a = hold.get("part_a", "")
            part_b = hold.get("part_b", "")
            if part_a and part_b:
                value = f"{part_a} hold {part_b}"
                key = ("contact_point", value)
                if key not in seen:
                    rows.append({"category": "contact_point", "value": value})
                    seen.add(key)

        # constraints -> constraint
        for c in group.get("constraints", []):
            part = c.get("part", "")
            rule = c.get("rule", "")
            if part and rule:
                value = f"{part}: {rule}"
                key = ("constraint", value)
                if key not in seen:
                    rows.append({"category": "constraint", "value": value})
                    seen.add(key)

        # position + contacts qualifier -> working_position
        position = group.get("position", "")
        if position:
            # Build qualifier from contacts
            contact_qualifiers = []
            for contact in group.get("contacts", []):
                qualifier = contact.get("qualifier", "")
                if qualifier:
                    contact_qualifiers.append(qualifier)
                else:
                    part = contact.get("part", "")
                    surface = contact.get("surface", "")
                    if part and surface and surface != "floor":
                        contact_qualifiers.append(f"{part} on {surface}")

            if contact_qualifiers:
                value = f"{position} with {', '.join(contact_qualifiers)}"
            else:
                value = position

            key = ("working_position", value)
            if key not in seen:
                rows.append({"category": "working_position", "value": value})
                seen.add(key)

    return rows


def find_docs_with_spatial(db: Database) -> list[dict]:
    """Find all documents that have pass_0b_spatial cached."""
    public_join = "\nJOIN document_source ds ON ds.id = d.source_id" if db.public_only else ""
    public_filter = " AND ds.public = TRUE" if db.public_only else ""

    with db.get_cursor(commit=False) as cursor:
        cursor.execute(f"""
            SELECT a.document_id::text as doc_id,
                   d.title,
                   a.data->>'text' as text
            FROM analysis a
            JOIN document d ON d.id = a.document_id
            JOIN analysis_type at ON at.id = a.type_id AND at.name = 'TRANSCRIPTION'{public_join}
            WHERE a.data->>'text' IS NOT NULL{public_filter}
        """)
        docs = [dict(row) for row in cursor.fetchall()]

    results = []
    cache_suffix = ":spatial_v2"

    for doc in docs:
        text = doc["text"].strip()
        if not text:
            continue
        cache_key = get_cache_key(text + cache_suffix)
        cached = db.get_cache(cache_key)
        if cached and cached.get("groups"):
            results.append({
                "doc_id": doc["doc_id"],
                "title": doc["title"],
                "spatial_data": cached,
            })

    return results


def main():
    parser = argparse.ArgumentParser(description="Build spatial concept index from cached extractions")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be indexed")
    group.add_argument("--apply", action="store_true", help="Insert concept rows")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N lessons")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    db = Database(database_url)
    db.initialize()

    docs = find_docs_with_spatial(db)
    print(f"Found {len(docs)} documents with spatial extraction")

    if args.limit:
        docs = docs[:args.limit]

    if not docs:
        print("Nothing to do.")
        db.close()
        return

    updated = 0

    for doc in docs:
        concept_rows = build_spatial_concept_rows(doc["spatial_data"])

        if args.dry_run:
            print(f"\n  [{doc['title']}] ({doc['doc_id']})")
            print(f"    {len(concept_rows)} concept rows:")
            by_cat = {}
            for r in concept_rows:
                by_cat.setdefault(r["category"], []).append(r["value"])
            for cat, vals in sorted(by_cat.items()):
                for v in vals:
                    print(f"      {cat}: {v}")
            continue

        db.upsert_spatial_concepts(doc["doc_id"], concept_rows)
        updated += 1
        n_groups = len(doc["spatial_data"].get("groups", []))
        print(f"  [{doc['title']}] {len(concept_rows)} concept rows from {n_groups} groups")

    if args.dry_run:
        print(f"\nDRY RUN: {len(docs)} lessons would be indexed")
    else:
        print(f"\nDone: {updated} lessons updated with spatial concepts")

    db.close()


if __name__ == "__main__":
    main()
