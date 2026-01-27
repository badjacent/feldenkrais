#!/usr/bin/env python3
"""
Re-fetch missing Feldenkrais Manhattan files.

This script identifies FM documents that are missing their archive files
and re-downloads them from YouTube using the cached youtube_id.

Usage:
    # Show what's missing
    python refetch_missing_fm.py --database-url "postgresql://..." --bucket feldenkrais-prod --dry-run

    # Re-download missing files
    python refetch_missing_fm.py --database-url "postgresql://..." --bucket feldenkrais-prod

    # Use credentials for FM site (to refresh lesson data)
    python refetch_missing_fm.py --database-url "postgresql://..." --bucket feldenkrais-prod \
        --fm-email user@example.com --fm-password secret --refresh-lessons
"""

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
import boto3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Source IDs
FM_SOURCE_ID = 3
AUDIO_TYPE_ID = 2


def get_db_connection(database_url: str):
    """Connect to PostgreSQL database."""
    return psycopg2.connect(database_url)


def get_s3_client():
    """Get S3 client."""
    return boto3.client('s3')


def check_archive_exists(s3, bucket: str, s3_key: str) -> bool:
    """Check if archive file exists in S3."""
    if not s3_key:
        return False
    try:
        s3.head_object(Bucket=bucket, Key=s3_key)
        return True
    except:
        return False


def get_fm_documents(conn) -> list[dict]:
    """Get all FM documents."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM document WHERE source_id = %s ORDER BY created_at",
            (FM_SOURCE_ID,)
        )
        return [dict(row) for row in cur.fetchall()]


def get_fm_lessons(conn) -> list[dict]:
    """Get all FM lessons from cache."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM fm_lesson_status WHERE youtube_id IS NOT NULL")
        return [dict(row) for row in cur.fetchall()]


def extract_youtube_id_from_metadata(doc: dict) -> Optional[str]:
    """Extract YouTube ID from document metadata."""
    metadata = doc.get('metadata') or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    youtube_meta = metadata.get('youtube', {})
    return youtube_meta.get('video_id')


def download_youtube_audio(youtube_id: str, output_dir: str) -> Optional[str]:
    """Download audio from YouTube video."""
    try:
        import yt_dlp

        output_template = str(Path(output_dir) / "%(id)s.%(ext)s")
        url = f"https://www.youtube.com/watch?v={youtube_id}"

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '0',
            }],
            'quiet': True,
            'no_warnings': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the downloaded file
        mp3_path = Path(output_dir) / f"{youtube_id}.mp3"
        if mp3_path.exists():
            return str(mp3_path)

        for ext in ['m4a', 'opus', 'webm']:
            path = Path(output_dir) / f"{youtube_id}.{ext}"
            if path.exists():
                return str(path)

        return None

    except Exception as e:
        logger.error(f"Failed to download {youtube_id}: {e}")
        return None


def upload_to_archive(s3, bucket: str, local_path: str, s3_key: str):
    """Upload file to S3 archive."""
    with open(local_path, 'rb') as f:
        s3.put_object(Bucket=bucket, Key=s3_key, Body=f.read())


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    sanitized = re.sub(r'\s+', '_', sanitized)
    sanitized = sanitized.strip('._')
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
    return sanitized


def stage_file(s3, bucket: str, local_path: str, doc: dict, youtube_id: str):
    """Stage a downloaded file with sidecar."""
    # Parse metadata
    metadata = doc.get('metadata') or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    tags = doc.get('tags') or []
    if isinstance(tags, str):
        tags = json.loads(tags)

    # Generate staging filename
    title = doc.get('title', youtube_id)
    filename = f"{sanitize_filename(title)}.mp3"
    staging_key = f"staging/{filename}"
    sidecar_key = f"{staging_key}.json"

    # Build sidecar
    sidecar = {
        "title": title,
        "source_url": doc.get('source_url'),
        "source_id": FM_SOURCE_ID,
        "type_id": AUDIO_TYPE_ID,
        "tags": tags,
    }

    if metadata.get('youtube'):
        sidecar['youtube_metadata'] = metadata['youtube']
    if metadata.get('lesson'):
        sidecar['lesson_metadata'] = metadata['lesson']

    # Upload
    with open(local_path, 'rb') as f:
        s3.put_object(Bucket=bucket, Key=staging_key, Body=f.read())

    s3.put_object(
        Bucket=bucket,
        Key=sidecar_key,
        Body=json.dumps(sidecar, indent=2).encode(),
        ContentType='application/json'
    )

    return staging_key


def delete_document(conn, doc_id: str):
    """Delete document record (so loader can recreate)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM document WHERE id = %s", (str(doc_id),))
    conn.commit()


def main():
    parser = argparse.ArgumentParser(
        description="Re-fetch missing FM files from YouTube"
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="PostgreSQL connection URL"
    )
    parser.add_argument(
        "--bucket",
        required=True,
        help="S3 bucket name"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of files to download"
    )
    parser.add_argument(
        "--fm-email",
        help="FM site email (for refreshing lesson data)"
    )
    parser.add_argument(
        "--fm-password",
        help="FM site password"
    )
    parser.add_argument(
        "--refresh-lessons",
        action="store_true",
        help="Refresh lesson data from FM site before processing"
    )

    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN MODE ===\n")

    # Connect
    conn = get_db_connection(args.database_url)
    s3 = get_s3_client()

    try:
        # Get FM documents
        documents = get_fm_documents(conn)
        logger.info(f"Found {len(documents)} FM documents in database")

        # Check which are missing
        missing = []
        for doc in documents:
            s3_key = doc.get('s3_key', '')
            if not check_archive_exists(s3, args.bucket, s3_key):
                youtube_id = extract_youtube_id_from_metadata(doc)
                if youtube_id:
                    missing.append({
                        'doc': doc,
                        'youtube_id': youtube_id,
                    })
                else:
                    logger.warning(f"Missing file with no YouTube ID: {doc['title']}")

        logger.info(f"Found {len(missing)} documents with missing archive files")

        if not missing:
            logger.info("Nothing to do!")
            return

        # Optionally refresh FM lesson data
        if args.refresh_lessons and args.fm_email and args.fm_password:
            logger.info("\nRefreshing FM lesson data...")
            # Import fm_loader functions
            sys.path.insert(0, str(Path(__file__).parent.parent / 'src' / 'kb'))
            from fm_loader import scrape_and_update, Database as FMDatabase

            fm_db = FMDatabase(args.database_url)
            fm_db.initialize()
            try:
                scrape_and_update(args.fm_email, args.fm_password, fm_db)
            finally:
                fm_db.close()
            # Reconnect after fm_loader operations
            conn = get_db_connection(args.database_url)

        # Apply limit
        if args.limit:
            missing = missing[:args.limit]

        # Process missing files
        results = {'downloaded': 0, 'failed': 0}

        for item in missing:
            doc = item['doc']
            youtube_id = item['youtube_id']
            title = doc['title']

            logger.info(f"\nProcessing: {title[:60]}...")
            logger.info(f"  YouTube ID: {youtube_id}")

            if args.dry_run:
                logger.info(f"  [DRY RUN] Would download and stage")
                results['downloaded'] += 1
                continue

            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    # Download
                    logger.info(f"  Downloading...")
                    local_path = download_youtube_audio(youtube_id, temp_dir)

                    if not local_path:
                        logger.error(f"  Failed to download")
                        results['failed'] += 1
                        continue

                    # Stage (not directly to archive - let loader process it)
                    logger.info(f"  Staging...")
                    staging_key = stage_file(s3, args.bucket, local_path, doc, youtube_id)
                    logger.info(f"  Staged: {staging_key}")

                    # Delete old document record (loader will recreate)
                    logger.info(f"  Deleting old document record...")
                    delete_document(conn, doc['id'])

                    results['downloaded'] += 1
                    logger.info(f"  Done!")

            except Exception as e:
                logger.error(f"  Error: {e}")
                results['failed'] += 1

        # Summary
        logger.info("\n" + "="*60)
        logger.info("SUMMARY")
        logger.info("="*60)
        logger.info(f"Missing files found:  {len(missing)}")
        logger.info(f"Downloaded/staged:    {results['downloaded']}")
        logger.info(f"Failed:               {results['failed']}")

        if results['downloaded'] > 0 and not args.dry_run:
            logger.info("\nFiles have been staged. Run the loader to process them.")
            logger.info("The loader will create new document records and archive the files.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
