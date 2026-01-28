#!/usr/bin/env python3
"""
Check ATM book transcription status, export transcripts, and disable triggers.
Run this when you're back online to finalize the ATM book processing.

Usage:
    python scripts/finalize_atm.py --status     # Check status only
    python scripts/finalize_atm.py --export     # Export completed transcripts
    python scripts/finalize_atm.py --disable    # Disable Lambda triggers
    python scripts/finalize_atm.py --all        # Do all of the above
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kb"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from database import Database
from config import Config

LESSONS_DIR = Path(__file__).parent.parent / "data" / "lessons" / "atm_book"


def get_db():
    config = Config.from_env()
    return Database(config.database_url)


def check_status():
    """Check ATM book transcription status."""
    db = get_db()

    print("\n=== ATM Book Transcription Status ===\n")

    with db.get_cursor() as cur:
        # Get job counts by status
        cur.execute("""
            SELECT tj.status, COUNT(*) as cnt
            FROM transcription_job tj
            JOIN document d ON tj.document_id = d.id
            WHERE d.title LIKE '%Feldenkrais Proj%'
               OR d.title LIKE '%(Patrons)%'
            GROUP BY tj.status
            ORDER BY tj.status
        """)
        jobs = cur.fetchall()

        print("Transcription Jobs:")
        total = 0
        completed = 0
        for row in jobs:
            print(f"  {row['status']:12} {row['cnt']}")
            total += row['cnt']
            if row['status'] == 'completed':
                completed = row['cnt']

        print(f"\nTotal: {completed}/{total} completed")

        # Get document details
        cur.execute("""
            SELECT d.title,
                   COALESCE(
                       (SELECT tj.status FROM transcription_job tj
                        WHERE tj.document_id = d.id
                        ORDER BY tj.created_at DESC LIMIT 1),
                       'no job'
                   ) as job_status,
                   EXISTS(
                       SELECT 1 FROM analysis a
                       WHERE a.document_id = d.id AND a.type_id = 1
                   ) as has_transcript
            FROM document d
            WHERE d.title LIKE '%Feldenkrais Proj%'
               OR d.title LIKE '%(Patrons)%'
            ORDER BY d.title
        """)
        docs = cur.fetchall()

        print("\nDocuments:")
        for doc in docs:
            status = "✓" if doc['has_transcript'] else "○"
            title = doc['title'][:50]
            print(f"  {status} {title}")

        all_complete = all(doc['has_transcript'] for doc in docs)
        return all_complete


def export_transcripts():
    """Export completed transcripts to data/lessons/."""
    db = get_db()
    LESSONS_DIR.mkdir(parents=True, exist_ok=True)

    print("\n=== Exporting Transcripts ===\n")

    with db.get_cursor() as cur:
        cur.execute("""
            SELECT d.id, d.title, a.data
            FROM document d
            JOIN analysis a ON a.document_id = d.id
            JOIN analysis_type at ON a.type_id = at.id
            WHERE at.name = 'TRANSCRIPTION'
            AND (d.title LIKE '%Feldenkrais Proj%' OR d.title LIKE '%(Patrons)%')
        """)
        docs = cur.fetchall()

        exported = 0
        for doc in docs:
            title = doc['title']
            data = doc['data']

            if isinstance(data, str):
                data = json.loads(data)

            text = data.get('text', '')
            if not text:
                print(f"  Skip (no text): {title[:40]}")
                continue

            # Clean filename
            filename = title.lower()
            for remove in ['(patrons)', '- feldenkrais project', 'feldenkrais proj']:
                filename = filename.replace(remove, '')
            filename = filename.strip().replace(' ', '_').replace('-', '_')
            filename = ''.join(c for c in filename if c.isalnum() or c == '_')
            filename = f"{filename}.txt"

            filepath = LESSONS_DIR / filename
            filepath.write_text(f"# {title}\n\n{text}")
            print(f"  Exported: {filename}")
            exported += 1

        print(f"\nExported {exported} transcripts to {LESSONS_DIR}")


def disable_triggers():
    """Disable EventBridge triggers."""
    print("\n=== Disabling Lambda Triggers ===\n")

    rules = [
        "feldenkrais-loader-prod",
        "feldenkrais-transcription-prod",
        "feldenkrais-youtube-prod"
    ]

    for rule in rules:
        try:
            subprocess.run(
                ["aws", "events", "disable-rule", "--name", rule],
                check=True,
                capture_output=True
            )
            print(f"  Disabled: {rule}")
        except subprocess.CalledProcessError as e:
            print(f"  Failed to disable {rule}: {e}")

    print("\nTriggers disabled. To re-enable:")
    print("  aws events enable-rule --name feldenkrais-transcription-prod")


def main():
    parser = argparse.ArgumentParser(description="Finalize ATM book processing")
    parser.add_argument("--status", action="store_true", help="Check status only")
    parser.add_argument("--export", action="store_true", help="Export transcripts")
    parser.add_argument("--disable", action="store_true", help="Disable triggers")
    parser.add_argument("--all", action="store_true", help="Do everything")

    args = parser.parse_args()

    if not any([args.status, args.export, args.disable, args.all]):
        args.status = True  # Default to status check

    if args.status or args.all:
        all_complete = check_status()

        if args.all and not all_complete:
            print("\n⚠️  Not all transcriptions complete. Run with --status to monitor.")
            print("   Skipping export and disable.")
            return

    if args.export or args.all:
        export_transcripts()

    if args.disable or args.all:
        disable_triggers()

    print("\nDone!")


if __name__ == "__main__":
    main()
