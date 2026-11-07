#!/usr/bin/env python3
"""
Load lessons from a text file into external_lesson.

Splits on lesson boundaries, preserves full text, uses LLM for brief summaries only.
Stores full text in extras JSONB, generates embeddings.

Usage:
    .venv/bin/python src/kb/ingest/load_text_lessons.py /path/to/lessons.txt \
        --source-id 139 --dry-run
    .venv/bin/python src/kb/ingest/load_text_lessons.py /path/to/lessons.txt \
        --source-id 139 --embed
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

from database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def split_lessons(text: str, start_after_line: int = 0) -> list[dict]:
    """Split text into lessons based on 'Lesson #N' headings.

    Returns list of dicts with: heading, number, section, full_text, teacher, date.
    Only processes full lesson texts (skips TOC-style short entries).
    """
    lines = text.split("\n")
    # Pattern matches full lesson headings like:
    #   Lesson #1 - Lifting the Head on the Back,
    #   Lesson #8: Simple Pelvic Clock
    #   Lesson #5-Rounding and arching back in sitting
    heading_re = re.compile(r"^Lesson\s*#(\d+)\s*[-–:.]?\s*(.+)", re.IGNORECASE)

    lessons = []
    current = None
    current_section = "I"
    title_continuation = False

    for i, line in enumerate(lines):
        if i < start_after_line:
            continue

        m = heading_re.match(line.strip())
        if m:
            # Save previous lesson
            if current and len(current["lines"]) > 5:
                _finalize_lesson(current)
                lessons.append(current)

            num = m.group(1)
            title = m.group(2).strip().rstrip(",").rstrip(".")

            # Detect section boundary: if we see Lesson #1 again and we already
            # have lessons, we've entered Section II
            if num == "1" and lessons:
                current_section = "II"

            current = {
                "number": num,
                "section": current_section,
                "title": title,
                "lines": [line],
                "teacher": None,
                "date": None,
            }
            # Check if title might continue on next line (no date/teacher marker)
            title_continuation = True
        elif current is not None:
            # Join continuation line into title if it doesn't look like
            # a date/teacher line or instruction
            if (title_continuation
                    and not re.match(r"(Evening class|Taught by|\d+\s+\w+\s+\d{4}|Original Text|\d+\.)", line.strip(), re.IGNORECASE)
                    and not line.strip().startswith(("Lie ", "Sit ", "Stand "))
                    and line.strip()
                    and not re.search(r"taught by", line, re.IGNORECASE)):
                current["title"] += " " + line.strip().rstrip(",").rstrip(".")
            else:
                title_continuation = False
            current["lines"].append(line)

    # Don't forget the last lesson
    if current and len(current["lines"]) > 5:
        _finalize_lesson(current)
        lessons.append(current)

    return lessons


def _finalize_lesson(lesson: dict):
    """Extract metadata from collected lines and build full_text."""
    full_text = "\n".join(lesson["lines"])
    lesson["full_text"] = full_text

    # Try to extract teacher and date from second line
    # e.g. "6 July 1977, taught by Mia Segal" or "Evening class, 13 July 1977, taught by Mia Segal"
    if len(lesson["lines"]) > 1:
        second = lesson["lines"][1].strip()
        teacher_match = re.search(r"taught by\s+(.+?)(?:\s*$)", second, re.IGNORECASE)
        if teacher_match:
            lesson["teacher"] = teacher_match.group(1).strip()
        date_match = re.search(r"(\d{1,2}\s+\w+\s+\d{4})", second)
        if date_match:
            lesson["date"] = date_match.group(1)

    # Build lesson label
    lesson["label"] = f"Section {lesson['section']}, Lesson #{lesson['number']}"

    del lesson["lines"]


def summarize_lessons(lessons: list[dict]) -> list[str]:
    """Use LLM to generate brief summaries for each lesson."""
    import openai
    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "").strip())

    summaries = []
    for lesson in lessons:
        # Truncate to ~3000 chars for the summary prompt
        text_for_summary = lesson["full_text"][:3000]
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"Summarize this Feldenkrais ATM lesson in 1-2 sentences. "
                           f"Focus on what movements are explored and the main theme. "
                           f"Be concrete and specific.\n\n{text_for_summary}"
            }],
            max_tokens=150,
        )
        summary = resp.choices[0].message.content.strip()
        summaries.append(summary)
        logger.info(f"  {lesson['label']}: {summary[:80]}...")

    return summaries


def load_lessons(db: Database, lessons: list[dict], source_id: int,
                 summaries: list[str] | None = None, embed: bool = False,
                 dry_run: bool = False):
    """Insert lessons into external_lesson."""
    if dry_run:
        for i, lesson in enumerate(lessons):
            desc = summaries[i] if summaries else None
            logger.info(f"\n{lesson['label']}: {lesson['title']}")
            if lesson.get("teacher"):
                logger.info(f"  Teacher: {lesson['teacher']}, Date: {lesson.get('date')}")
            logger.info(f"  Text: {len(lesson['full_text'])} chars")
            if desc:
                logger.info(f"  Summary: {desc}")
        logger.info(f"\nTotal: {len(lessons)} lessons")
        return

    inserted = 0
    for i, lesson in enumerate(lessons):
        title = f"{lesson['label']}: {lesson['title']}"
        description = summaries[i] if summaries else None
        extras = {
            "full_text": lesson["full_text"],
            "lesson_number": lesson["number"],
            "section": lesson["section"],
        }
        if lesson.get("teacher"):
            extras["teacher"] = lesson["teacher"]
        if lesson.get("date"):
            extras["date"] = lesson["date"]

        embed_text = lesson["full_text"]

        with db.get_cursor() as cur:
            cur.execute("""
                INSERT INTO external_lesson
                    (lesson_source_id, title, description, language, access,
                     content_type, embed_text, extras)
                VALUES (%s, %s, %s, 'en', 'text', 'text', %s, %s::jsonb)
                ON CONFLICT (lesson_source_id, title) DO UPDATE SET
                    description = EXCLUDED.description,
                    embed_text = EXCLUDED.embed_text,
                    extras = EXCLUDED.extras,
                    updated_at = NOW()
            """, (
                source_id,
                title,
                description,
                embed_text,
                json.dumps(extras),
            ))
        inserted += 1

    logger.info(f"Loaded {inserted} lessons")

    if embed:
        _generate_embeddings(db, source_id)


def _generate_embeddings(db: Database, source_id: int):
    """Generate embeddings for lessons from this source that don't have them."""
    import openai
    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "").strip())

    with db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT id, embed_text FROM external_lesson
            WHERE lesson_source_id = %s AND embedding IS NULL AND embed_text IS NOT NULL
            ORDER BY id
        """, (source_id,))
        rows = cur.fetchall()

    if not rows:
        logger.info("No rows need embeddings")
        return

    logger.info(f"Generating embeddings for {len(rows)} lessons...")

    # Embed one at a time since full texts can be long
    for row in rows:
        # text-embedding-3-small has 8191 token limit; truncate if needed
        text = row["embed_text"][:20000]
        resp = client.embeddings.create(model="text-embedding-3-small", input=[text])
        vec = resp.data[0].embedding
        with db.get_cursor() as cur:
            cur.execute(
                "UPDATE external_lesson SET embedding = %s WHERE id = %s",
                (str(vec), row["id"]),
            )

    logger.info(f"Done: {len(rows)} embeddings generated")


def main():
    parser = argparse.ArgumentParser(description="Load text lessons into external_lesson")
    parser.add_argument("file", help="Path to text file")
    parser.add_argument("--source-id", type=int, required=True, help="lesson_source id")
    parser.add_argument("--start-line", type=int, default=0,
                        help="Skip lines before this (to skip TOC/foreword)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summarize", action="store_true", help="Generate LLM summaries")
    parser.add_argument("--embed", action="store_true", help="Generate embeddings after load")
    args = parser.parse_args()

    text = Path(args.file).read_text()
    lessons = split_lessons(text, start_after_line=args.start_line)
    logger.info(f"Found {len(lessons)} lessons in {args.file}")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    db = Database(db_url)

    summaries = None
    if args.summarize and not args.dry_run:
        logger.info("Generating summaries...")
        summaries = summarize_lessons(lessons)

    load_lessons(db, lessons, args.source_id, summaries=summaries,
                 embed=args.embed, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
