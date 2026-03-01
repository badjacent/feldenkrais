"""
Rename FM lessons using LLM-generated descriptive names.

Sends each lesson's transcript to Claude Haiku to generate a short descriptive
name and classify its type, then constructs a title like:
    "Rolling the Pelvis to Sitting (ATM, 10/5/25 Ch 4)"

Usage:
    .venv/bin/python src/kb/name_fm_lessons.py --dry-run --limit 5
    .venv/bin/python src/kb/name_fm_lessons.py --apply --limit 5
    .venv/bin/python src/kb/name_fm_lessons.py --apply
    .venv/bin/python src/kb/name_fm_lessons.py --apply --force   # re-process already renamed
"""

import argparse
import json
import os
import re
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from database import Database
from llm import LLM

NAMING_PROMPT = """\
This is a transcript from a Feldenkrais training video. You must return exactly \
two lines:

Line 1: The lesson type — one of:
  ATM — an Awareness Through Movement lesson (guided movement exploration on the floor/sitting/standing)
  FI Demo — a Functional Integration demonstration (hands-on work with a student)
  Discussion — group discussion, Q&A, or reflective conversation
  Lecture — didactic teaching on anatomy, theory, or Feldenkrais concepts
  Practice — partner or small-group hands-on practice session

Line 2: A short descriptive name (3-8 words) based on the content.
  - For ATM: name the movement exploration (e.g., "Rolling the Pelvis to Sitting")
  - For FI Demo: name what's being worked on (e.g., "Shoulder and Chest Mechanics")
  - For Discussion/Lecture: summarize the topic (e.g., "Learning and Habits")
  - For Practice: describe the focus (e.g., "Side-Lying Observation Skills")
  - If multiple activities happen, classify and name the primary one.

Return ONLY these two lines, nothing else. No quotes, no punctuation at the end."""

VALID_TYPES = {"ATM", "FI Demo", "Discussion", "Lecture", "Practice"}

TRANSCRIPT_CHAR_LIMIT = 10_000


def format_date(lesson_date) -> str:
    """Format a date as M/D/YY."""
    if lesson_date is None:
        return ""
    return f"{lesson_date.month}/{lesson_date.day}/{lesson_date.strftime('%y')}"


def format_chapter(lesson_index: str) -> str:
    """Convert 'Chapter 4' to 'Ch 4', 'Chapter 4a and 4b' to 'Ch 4a+4b'."""
    if not lesson_index:
        return ""
    ch = lesson_index.replace("Chapter ", "").replace("chapter ", "")
    ch = re.sub(r'\s+and\s+', '+', ch)
    return f"Ch {ch}"


def build_title(lesson_date, lesson_index: str, lesson_type: str, llm_name: str) -> str:
    """Construct the final title: 'Pelvic Clock (ATM, 10/5/25 Ch 4)'."""
    parts = []
    if lesson_type:
        parts.append(lesson_type)
    date_str = format_date(lesson_date)
    if date_str:
        parts.append(date_str)
    ch_str = format_chapter(lesson_index)
    if ch_str:
        parts.append(ch_str)
    suffix = ", ".join(parts)
    if suffix:
        return f"{llm_name} ({suffix})"
    return llm_name


def sample_transcript(transcript: str) -> str:
    """Pick the most informative 10K-char window from the transcript.

    Long transcripts often start with logistics/greetings before the actual
    lesson begins. Sending chars 10K-20K catches the core content. For short
    transcripts, send the whole thing.
    """
    if len(transcript) <= TRANSCRIPT_CHAR_LIMIT:
        return transcript
    start = min(TRANSCRIPT_CHAR_LIMIT, len(transcript) - TRANSCRIPT_CHAR_LIMIT)
    return transcript[start:start + TRANSCRIPT_CHAR_LIMIT]


def name_lesson(llm: LLM, transcript: str) -> tuple[str, str]:
    """Send transcript to LLM and get a type and descriptive name."""
    sample = sample_transcript(transcript)
    response = llm.complete(sample, system=NAMING_PROMPT, max_tokens=80)

    lines = [l.strip() for l in response.strip().splitlines() if l.strip()]

    if len(lines) >= 2:
        lesson_type = lines[0].strip().strip('"\'')
        name = lines[1].strip().strip('"\'').rstrip('.').strip()
    else:
        # Fallback: treat whole response as name, no type
        lesson_type = ""
        name = response.strip().strip('"\'').rstrip('.').strip()

    # Validate type
    if lesson_type not in VALID_TYPES:
        lesson_type = ""

    return lesson_type, name


def main():
    parser = argparse.ArgumentParser(description="Rename FM lessons using LLM-generated names")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Print proposed renames without updating DB")
    group.add_argument("--apply", action="store_true", help="Actually update titles in the database")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N lessons")
    parser.add_argument("--source-id", type=int, default=3, help="Source ID (default: 3 = FELDENKRAIS_MANHATTAN)")
    parser.add_argument("--force", action="store_true", help="Re-process already renamed lessons")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    db = Database(database_url)
    llm = LLM(provider="anthropic", model="claude-haiku-4-5-20251001", api_key=api_key)

    # Get all transcribed lessons for this source
    lessons = db.get_source_lessons(args.source_id)
    print(f"Found {len(lessons)} transcribed lessons for source_id={args.source_id}")

    if args.limit:
        lessons = lessons[:args.limit]
        print(f"Processing first {args.limit}")

    renamed = 0
    skipped = 0
    errors = 0

    for i, lesson in enumerate(lessons):
        doc_id = str(lesson["id"])
        old_title = lesson["title"]

        # Check if already renamed (has original_title in metadata)
        doc = db.get_document(doc_id)
        metadata = doc.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        if metadata.get("original_title") and not args.force:
            print(f"  [{i+1}/{len(lessons)}] SKIP (already renamed): {old_title}")
            skipped += 1
            continue

        # Get transcript
        transcript = db.get_document_transcription(doc_id)
        if not transcript:
            print(f"  [{i+1}/{len(lessons)}] SKIP (no transcript): {old_title}")
            skipped += 1
            continue

        # Get LLM type + name
        try:
            lesson_type, llm_name = name_lesson(llm, transcript)
        except Exception as e:
            print(f"  [{i+1}/{len(lessons)}] ERROR: {old_title} — {e}", file=sys.stderr)
            errors += 1
            continue

        new_title = build_title(
            lesson.get("lesson_date"), lesson.get("lesson_index"),
            lesson_type, llm_name,
        )

        if args.dry_run:
            print(f"  [{i+1}/{len(lessons)}] {old_title}")
            print(f"       -> {new_title}")
        else:
            # Preserve the very first original title (don't overwrite on --force re-runs)
            if not metadata.get("original_title"):
                metadata["original_title"] = old_title
            metadata["lesson_type"] = lesson_type
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE document
                    SET title = %s, metadata = %s::jsonb, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (new_title, json.dumps(metadata), doc_id),
                )
            print(f"  [{i+1}/{len(lessons)}] {old_title}")
            print(f"       -> {new_title}")
            renamed += 1

        # Small delay to avoid rate limits
        time.sleep(0.1)

    print()
    if args.dry_run:
        print(f"DRY RUN complete: {len(lessons) - skipped - errors} would be renamed, {skipped} skipped, {errors} errors")
    else:
        print(f"Done: {renamed} renamed, {skipped} skipped, {errors} errors")

    db.close()


if __name__ == "__main__":
    main()
