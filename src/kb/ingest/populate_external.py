#!/usr/bin/env python3
"""
Populate the external_lesson table from various sources.

Phase 1: AY lessons (561 from ay_lesson table with tags)
Phase 2+: Web scraping from other lesson sources (TODO)

Usage:
    .venv/bin/python src/kb/ingest/populate_external.py ay          # populate from AY lessons
    .venv/bin/python src/kb/ingest/populate_external.py ay --embed  # also generate embeddings
    .venv/bin/python src/kb/ingest/populate_external.py ay --dry-run
    .venv/bin/python src/kb/ingest/populate_external.py stats       # show table stats
"""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

from database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

FELDY_NOTEBOOK_SOURCE_ID = 11  # lesson_source.id for FeldyNotebook.com


def build_embed_text(title: str, description: str | None, tags: list[str] | None) -> str:
    """Concatenate fields into text for embedding."""
    parts = [title]
    if description:
        parts.append(description)
    if tags:
        parts.append(" ".join(tags))
    return " | ".join(parts)


def populate_from_ay(db: Database, dry_run: bool = False, embed: bool = False):
    """Copy AY lessons into external_lesson table."""
    with db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT a.id, a.ay_number, a.title, a.synopsis, a.outline,
                   a.teaching_focus, a.source_url,
                   (SELECT array_agg(t.tag) FROM ay_lesson_tag t WHERE t.lesson_id = a.id) as tags
            FROM ay_lesson a
            ORDER BY a.id
        """)
        lessons = cur.fetchall()

    logger.info(f"Found {len(lessons)} AY lessons to populate")

    if dry_run:
        # Show sample
        for lesson in lessons[:5]:
            desc = _build_ay_description(lesson)
            embed_text = build_embed_text(
                f"AY {lesson['ay_number']}: {lesson['title']}",
                desc,
                lesson["tags"],
            )
            logger.info(f"  AY {lesson['ay_number']}: {lesson['title']}")
            logger.info(f"    description: {desc[:120]}..." if desc and len(desc) > 120 else f"    description: {desc}")
            logger.info(f"    tags: {lesson['tags']}")
            logger.info(f"    embed_text ({len(embed_text)} chars): {embed_text[:150]}...")
        with_desc = sum(1 for l in lessons if _build_ay_description(l))
        with_tags = sum(1 for l in lessons if l["tags"])
        logger.info(f"Summary: {len(lessons)} lessons, {with_desc} with description, {with_tags} with tags")
        return

    inserted = 0
    updated = 0
    for lesson in lessons:
        title = f"AY {lesson['ay_number']}: {lesson['title']}"
        desc = _build_ay_description(lesson)
        tags = lesson["tags"] or []
        # Clean up tags: replace hyphens with spaces for readability
        clean_tags = [t.replace("-", " ") for t in tags]
        embed_text = build_embed_text(title, desc, clean_tags)
        url = lesson["source_url"]

        extras = {"ay_number": lesson["ay_number"]}
        if lesson["outline"]:
            extras["outline"] = lesson["outline"]
        if lesson["teaching_focus"]:
            extras["teaching_focus"] = lesson["teaching_focus"]

        with db.get_cursor() as cur:
            cur.execute("""
                INSERT INTO external_lesson
                    (lesson_source_id, title, url, description, tags, language,
                     access, content_type, embed_text, extras)
                VALUES (%s, %s, %s, %s, %s, 'en', 'free', 'text', %s, %s::jsonb)
                ON CONFLICT (lesson_source_id, title) DO UPDATE SET
                    url = EXCLUDED.url,
                    description = EXCLUDED.description,
                    tags = EXCLUDED.tags,
                    embed_text = EXCLUDED.embed_text,
                    extras = EXCLUDED.extras,
                    updated_at = NOW()
                RETURNING (xmax = 0) as is_insert
            """, (
                FELDY_NOTEBOOK_SOURCE_ID,
                title,
                url,
                desc,
                clean_tags,
                embed_text,
                __import__("json").dumps(extras),
            ))
            row = cur.fetchone()
            if row["is_insert"]:
                inserted += 1
            else:
                updated += 1

    logger.info(f"Done: {inserted} inserted, {updated} updated")

    if embed:
        generate_embeddings(db)


def _build_ay_description(lesson: dict) -> str | None:
    """Build description from synopsis and/or teaching focus."""
    parts = []
    if lesson.get("synopsis"):
        parts.append(lesson["synopsis"])
    if lesson.get("teaching_focus"):
        parts.append(f"Teaching focus: {lesson['teaching_focus']}")
    return " ".join(parts) if parts else None


def generate_embeddings(db: Database):
    """Generate embeddings for external_lesson rows that don't have them yet."""
    import openai

    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    with db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT id, embed_text FROM external_lesson
            WHERE embedding IS NULL AND embed_text IS NOT NULL
            ORDER BY id
        """)
        rows = cur.fetchall()

    if not rows:
        logger.info("No rows need embeddings")
        return

    logger.info(f"Generating embeddings for {len(rows)} rows...")

    # Batch in groups of 100 (OpenAI limit is 2048)
    batch_size = 100
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        texts = [r["embed_text"] for r in batch]

        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=texts,
        )

        with db.get_cursor() as cur:
            for j, emb_data in enumerate(response.data):
                vec = emb_data.embedding
                cur.execute(
                    "UPDATE external_lesson SET embedding = %s WHERE id = %s",
                    (str(vec), batch[j]["id"]),
                )

        total += len(batch)
        logger.info(f"  Embedded {total}/{len(rows)}")

    logger.info(f"Done: {total} embeddings generated")


def show_stats(db: Database):
    """Show external_lesson table statistics."""
    with db.get_cursor(commit=False) as cur:
        cur.execute("SELECT COUNT(*) as total FROM external_lesson")
        total = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) as cnt FROM external_lesson WHERE embedding IS NOT NULL")
        with_embedding = cur.fetchone()["cnt"]

        cur.execute("""
            SELECT ls.name, COUNT(*) as cnt
            FROM external_lesson el
            LEFT JOIN lesson_source ls ON ls.id = el.lesson_source_id
            GROUP BY ls.name
            ORDER BY cnt DESC
        """)
        by_source = cur.fetchall()

        cur.execute("""
            SELECT access, COUNT(*) as cnt
            FROM external_lesson
            GROUP BY access
            ORDER BY cnt DESC
        """)
        by_access = cur.fetchall()

    print(f"\nexternal_lesson table: {total} rows ({with_embedding} with embeddings)")
    if by_source:
        print("\nBy source:")
        for r in by_source:
            print(f"  {r['name'] or '(none)'}: {r['cnt']}")
    if by_access:
        print("\nBy access level:")
        for r in by_access:
            print(f"  {r['access'] or '(none)'}: {r['cnt']}")


def main():
    parser = argparse.ArgumentParser(description="Populate external_lesson table")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ay_parser = subparsers.add_parser("ay", help="Populate from AY lessons")
    ay_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    ay_parser.add_argument("--embed", action="store_true", help="Generate embeddings after insert")

    subparsers.add_parser("stats", help="Show table statistics")

    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    db = Database(db_url)

    if args.command == "ay":
        populate_from_ay(db, dry_run=args.dry_run, embed=args.embed)
    elif args.command == "stats":
        show_stats(db)


if __name__ == "__main__":
    main()
