#!/usr/bin/env python3
"""
Check archive file status against SQLite database.

Dry-run analysis to see which documents have archive files and which are missing.
"""

import json
import sqlite3
from collections import defaultdict

import boto3

DB_PATH = "/Users/michaelhollander/github/feldenkrais/knowledge.db"
BUCKET = "feldenkrais-prod"

# Source names
SOURCES = {1: 'OTHER', 2: 'YOUTUBE', 3: 'FELDENKRAIS_MANHATTAN'}
TYPES = {1: 'TEXT', 2: 'AUDIO', 3: 'VIDEO'}

def main():
    # Connect to SQLite
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Get S3 client
    s3 = boto3.client('s3')

    # Get all documents
    cursor = conn.execute("""
        SELECT id, title, source_url, s3_key, source_id, type_id, status,
               created_at, tags, metadata
        FROM document
        ORDER BY source_id, created_at
    """)
    documents = [dict(row) for row in cursor.fetchall()]

    print(f"Total documents in database: {len(documents)}\n")

    # Check archive files
    results = {
        'found': [],
        'missing': [],
    }

    by_source = defaultdict(lambda: {'found': 0, 'missing': 0, 'missing_docs': []})

    for doc in documents:
        s3_key = doc.get('s3_key', '')
        source_id = doc['source_id']
        source_name = SOURCES.get(source_id, 'UNKNOWN')

        # Check if archive exists
        exists = False
        if s3_key:
            try:
                s3.head_object(Bucket=BUCKET, Key=s3_key)
                exists = True
            except:
                pass

        if exists:
            results['found'].append(doc)
            by_source[source_name]['found'] += 1
        else:
            results['missing'].append(doc)
            by_source[source_name]['missing'] += 1
            by_source[source_name]['missing_docs'].append(doc)

    # Print summary
    print("="*70)
    print("SUMMARY BY SOURCE")
    print("="*70)

    for source_name in ['OTHER', 'YOUTUBE', 'FELDENKRAIS_MANHATTAN']:
        if source_name in by_source:
            stats = by_source[source_name]
            total = stats['found'] + stats['missing']
            print(f"\n{source_name}:")
            print(f"  Found:   {stats['found']:4d}")
            print(f"  Missing: {stats['missing']:4d}")
            print(f"  Total:   {total:4d}")

    print("\n" + "="*70)
    print(f"OVERALL: {len(results['found'])} found, {len(results['missing'])} missing")
    print("="*70)

    # Show missing files details
    if results['missing']:
        print("\n" + "="*70)
        print("MISSING ARCHIVE FILES")
        print("="*70)

        for source_name in ['OTHER', 'YOUTUBE', 'FELDENKRAIS_MANHATTAN']:
            if source_name in by_source and by_source[source_name]['missing_docs']:
                print(f"\n--- {source_name} ({by_source[source_name]['missing']}) ---")
                for doc in by_source[source_name]['missing_docs'][:10]:  # Show first 10
                    title = doc['title'][:50] if doc['title'] else 'No title'
                    print(f"  - {title}")
                    if doc.get('source_url'):
                        print(f"    URL: {doc['source_url'][:60]}")

                    # Try to get YouTube ID from metadata
                    metadata = doc.get('metadata')
                    if metadata:
                        try:
                            meta = json.loads(metadata) if isinstance(metadata, str) else metadata
                            yt_id = meta.get('youtube', {}).get('video_id')
                            if yt_id:
                                print(f"    YouTube ID: {yt_id}")
                        except:
                            pass

                if by_source[source_name]['missing'] > 10:
                    print(f"  ... and {by_source[source_name]['missing'] - 10} more")

    # Check analyses and transcription jobs
    print("\n" + "="*70)
    print("ANALYSES & TRANSCRIPTION JOBS")
    print("="*70)

    cursor = conn.execute("SELECT COUNT(*) FROM analysis")
    analysis_count = cursor.fetchone()[0]

    cursor = conn.execute("SELECT COUNT(*) FROM transcription_job")
    job_count = cursor.fetchone()[0]

    cursor = conn.execute("SELECT COUNT(*) FROM transcription_job WHERE status = 'pending'")
    pending_jobs = cursor.fetchone()[0]

    cursor = conn.execute("SELECT COUNT(*) FROM transcription_job WHERE status = 'completed'")
    completed_jobs = cursor.fetchone()[0]

    cursor = conn.execute("SELECT COUNT(*) FROM transcription_job WHERE status = 'failed'")
    failed_jobs = cursor.fetchone()[0]

    print(f"\nAnalyses:           {analysis_count}")
    print(f"Transcription jobs: {job_count}")
    print(f"  - Pending:        {pending_jobs}")
    print(f"  - Completed:      {completed_jobs}")
    print(f"  - Failed:         {failed_jobs}")

    # Check FM lessons
    print("\n" + "="*70)
    print("FM LESSON STATUS")
    print("="*70)

    cursor = conn.execute("SELECT COUNT(*) FROM fm_lesson_status")
    fm_total = cursor.fetchone()[0]

    cursor = conn.execute("SELECT status, COUNT(*) FROM fm_lesson_status GROUP BY status")
    fm_by_status = {row[0]: row[1] for row in cursor.fetchall()}

    print(f"\nTotal FM lessons:   {fm_total}")
    for status, count in sorted(fm_by_status.items()):
        print(f"  - {status}: {count}")

    # Check YouTube queue
    print("\n" + "="*70)
    print("YOUTUBE QUEUE")
    print("="*70)

    cursor = conn.execute("SELECT COUNT(*) FROM youtube_queue")
    yt_total = cursor.fetchone()[0]

    cursor = conn.execute("SELECT status, COUNT(*) FROM youtube_queue GROUP BY status")
    yt_by_status = {row[0]: row[1] for row in cursor.fetchall()}

    print(f"\nTotal queued:       {yt_total}")
    for status, count in sorted(yt_by_status.items()):
        print(f"  - {status}: {count}")

    conn.close()

    # Write missing files to JSON
    if results['missing']:
        missing_data = []
        for doc in results['missing']:
            metadata = doc.get('metadata')
            youtube_id = None
            if metadata:
                try:
                    meta = json.loads(metadata) if isinstance(metadata, str) else metadata
                    youtube_id = meta.get('youtube', {}).get('video_id')
                except:
                    pass

            missing_data.append({
                'id': doc['id'],
                'title': doc['title'],
                's3_key': doc['s3_key'],
                'source_id': doc['source_id'],
                'source_name': SOURCES.get(doc['source_id'], 'UNKNOWN'),
                'source_url': doc.get('source_url'),
                'youtube_id': youtube_id,
            })

        with open('missing_archive_files.json', 'w') as f:
            json.dump(missing_data, f, indent=2)
        print(f"\n\nMissing files written to: missing_archive_files.json")


if __name__ == "__main__":
    main()
