"""Load Bouffes du Nord lessons (Feldenkrais, 1978) into the database.

8 ATM lessons taught by Moshe Feldenkrais in French at the Théâtre des Bouffes du Nord
in Paris, May 1978. English translation by Ben Parsons. Published by IFF, January 2019.
"""

import json
import os
import sys

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.env"))

from database import Database

IFF_ARCHIVE_SOURCE_ID = 7
TEXT_TYPE_ID = 1
TRANSCRIPTION_TYPE_ID = 1

LESSONS = [
    {
        "title": "Bouffes du Nord #1: Pendulum Movements of the Head",
        "lesson_index": "Lesson 1",
        "metadata": {
            "instructor": "Moshe Feldenkrais",
            "location": "Théâtre des Bouffes du Nord, Paris",
            "year": 1978,
            "month": 5,
            "duration_minutes": 45,
            "original_language": "French",
            "translator": "Ben Parsons",
            "collection": "Bouffes du Nord",
            "publisher": "International Feldenkrais Federation",
            "published_year": 2019,
        },
    },
    {
        "title": "Bouffes du Nord #2: On All Fours, Side Movements",
        "lesson_index": "Lesson 2",
        "metadata": {
            "instructor": "Moshe Feldenkrais",
            "location": "Théâtre des Bouffes du Nord, Paris",
            "year": 1978,
            "month": 5,
            "duration_minutes": 49,
            "original_language": "French",
            "translator": "Ben Parsons",
            "collection": "Bouffes du Nord",
            "publisher": "International Feldenkrais Federation",
            "published_year": 2019,
        },
    },
    {
        "title": "Bouffes du Nord #3: Movements on Four Points",
        "lesson_index": "Lesson 3",
        "metadata": {
            "instructor": "Moshe Feldenkrais",
            "location": "Théâtre des Bouffes du Nord, Paris",
            "year": 1978,
            "month": 5,
            "duration_minutes": 38,
            "original_language": "French",
            "translator": "Ben Parsons",
            "collection": "Bouffes du Nord",
            "publisher": "International Feldenkrais Federation",
            "published_year": 2019,
        },
    },
    {
        "title": "Bouffes du Nord #4: Holding the Chin",
        "lesson_index": "Lesson 4",
        "metadata": {
            "instructor": "Moshe Feldenkrais",
            "location": "Théâtre des Bouffes du Nord, Paris",
            "year": 1978,
            "month": 5,
            "duration_minutes": 34,
            "original_language": "French",
            "translator": "Ben Parsons",
            "collection": "Bouffes du Nord",
            "publisher": "International Feldenkrais Federation",
            "published_year": 2019,
        },
    },
    {
        "title": "Bouffes du Nord #5: The Foot and the Toes",
        "lesson_index": "Lesson 5",
        "metadata": {
            "instructor": "Moshe Feldenkrais",
            "location": "Théâtre des Bouffes du Nord, Paris",
            "year": 1978,
            "month": 5,
            "duration_minutes": 45,
            "original_language": "French",
            "translator": "Ben Parsons",
            "collection": "Bouffes du Nord",
            "publisher": "International Feldenkrais Federation",
            "published_year": 2019,
        },
    },
    {
        "title": "Bouffes du Nord #6: Holding the Foot with the Hand",
        "lesson_index": "Lesson 6",
        "metadata": {
            "instructor": "Moshe Feldenkrais",
            "location": "Théâtre des Bouffes du Nord, Paris",
            "year": 1978,
            "month": 5,
            "duration_minutes": 40,
            "original_language": "French",
            "translator": "Ben Parsons",
            "collection": "Bouffes du Nord",
            "publisher": "International Feldenkrais Federation",
            "published_year": 2019,
        },
    },
    {
        "title": "Bouffes du Nord #7: On the Side, Differentiation, Twisting and Zenith",
        "lesson_index": "Lesson 7",
        "metadata": {
            "instructor": "Moshe Feldenkrais",
            "location": "Théâtre des Bouffes du Nord, Paris",
            "year": 1978,
            "month": 5,
            "duration_minutes": 90,
            "original_language": "French",
            "translator": "Ben Parsons",
            "collection": "Bouffes du Nord",
            "publisher": "International Feldenkrais Federation",
            "published_year": 2019,
        },
    },
    {
        "title": "Bouffes du Nord #8: Clock in Three Positions",
        "lesson_index": "Lesson 8",
        "metadata": {
            "instructor": "Moshe Feldenkrais",
            "location": "Théâtre des Bouffes du Nord, Paris",
            "year": 1978,
            "month": 5,
            "duration_minutes": 45,
            "original_language": "French",
            "translator": "Ben Parsons",
            "collection": "Bouffes du Nord",
            "publisher": "International Feldenkrais Federation",
            "published_year": 2019,
        },
    },
]


def load_lessons(transcript_dir: str, dry_run: bool = False):
    """Load all Bouffes du Nord lessons from text files in transcript_dir.

    Expects files named lesson_1.txt through lesson_8.txt.
    """
    db = Database(os.environ["DATABASE_URL"])

    for i, lesson in enumerate(LESSONS, 1):
        filepath = os.path.join(transcript_dir, f"lesson_{i}.txt")
        if not os.path.exists(filepath):
            print(f"SKIP: {filepath} not found")
            continue

        with open(filepath, "r") as f:
            text = f.read().strip()

        if not text:
            print(f"SKIP: {filepath} is empty")
            continue

        print(f"Loading: {lesson['title']} ({len(text)} chars)")

        if dry_run:
            print(f"  DRY RUN: would create document + transcription")
            continue

        # Create document record
        doc_id = db.create_document(
            title=lesson["title"],
            s3_key="",  # no S3 file for text-only lessons
            source_id=IFF_ARCHIVE_SOURCE_ID,
            type_id=TEXT_TYPE_ID,
            metadata=json.dumps(lesson["metadata"]),
            lesson_index=lesson["lesson_index"],
            lesson_date="1978-05-01",
        )

        # Mark as completed (no transcription job needed — text already available)
        db.update_document_status(doc_id, "completed")

        # Create transcription analysis record
        db.create_analysis(
            document_id=doc_id,
            type_id=TRANSCRIPTION_TYPE_ID,
            data=json.dumps({"text": text}),
        )

        print(f"  Created document {doc_id}")

    print("Done.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Load Bouffes du Nord lessons")
    parser.add_argument(
        "transcript_dir",
        help="Directory containing lesson_1.txt through lesson_8.txt",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without inserting"
    )
    args = parser.parse_args()
    load_lessons(args.transcript_dir, dry_run=args.dry_run)
