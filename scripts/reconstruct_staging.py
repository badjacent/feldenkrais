#!/usr/bin/env python3
"""
Reconstruct staging files from archive + database metadata.

This script reads documents from the database and reconstructs the staging/
files with their sidecar JSONs, allowing the loader workflow to reprocess them.

Usage:
    # Dry run - show what would be reconstructed
    python reconstruct_staging.py --database-url "postgresql://..." --bucket feldenkrais-prod --dry-run

    # Reconstruct all documents
    python reconstruct_staging.py --database-url "postgresql://..." --bucket feldenkrais-prod

    # Reconstruct only specific source (1=OTHER, 2=YOUTUBE, 3=FELDENKRAIS_MANHATTAN)
    python reconstruct_staging.py --database-url "postgresql://..." --bucket feldenkrais-prod --source-id 3

    # Clear existing analyses/transcriptions before reconstruct (forces full reprocess)
    python reconstruct_staging.py --database-url "postgresql://..." --bucket feldenkrais-prod --clear-analyses
"""

import argparse
import json
import logging
import sys
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


def get_db_connection(database_url: str):
    """Connect to PostgreSQL database."""
    return psycopg2.connect(database_url)


def get_s3_client():
    """Get S3 client."""
    return boto3.client('s3')


def get_documents(conn, source_id: Optional[int] = None) -> list[dict]:
    """Get all documents from database."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if source_id:
            cur.execute(
                "SELECT * FROM document WHERE source_id = %s ORDER BY created_at",
                (source_id,)
            )
        else:
            cur.execute("SELECT * FROM document ORDER BY created_at")
        return [dict(row) for row in cur.fetchall()]


def get_source_name(conn, source_id: int) -> str:
    """Get source name from ID."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT name FROM document_source WHERE id = %s", (source_id,))
        row = cur.fetchone()
        return row['name'] if row else 'UNKNOWN'


def get_type_name(conn, type_id: int) -> str:
    """Get type name from ID."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT name FROM document_type WHERE id = %s", (type_id,))
        row = cur.fetchone()
        return row['name'] if row else 'UNKNOWN'


def check_archive_exists(s3, bucket: str, s3_key: str) -> bool:
    """Check if archive file exists in S3."""
    try:
        s3.head_object(Bucket=bucket, Key=s3_key)
        return True
    except s3.exceptions.ClientError:
        return False


def reconstruct_sidecar(doc: dict) -> dict:
    """Reconstruct sidecar JSON from document metadata."""
    # Parse stored metadata
    metadata = doc.get('metadata') or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    tags = doc.get('tags') or []
    if isinstance(tags, str):
        tags = json.loads(tags)

    sidecar = {
        "title": doc['title'],
        "source_url": doc.get('source_url'),
        "source_id": doc['source_id'],
        "type_id": doc['type_id'],
        "tags": tags,
    }

    # Restore nested metadata
    if metadata.get('youtube'):
        sidecar['youtube_metadata'] = metadata['youtube']
    if metadata.get('chunk'):
        sidecar['chunk_metadata'] = metadata['chunk']
    if metadata.get('scraper'):
        sidecar['scraper_metadata'] = metadata['scraper']

    return sidecar


def generate_staging_filename(doc: dict, s3_key: str) -> str:
    """Generate a staging filename from document info."""
    extension = Path(s3_key).suffix or '.mp3'

    # Try to create a meaningful filename
    title = doc.get('title', '')

    # Sanitize title for filename
    safe_title = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in title)
    safe_title = '_'.join(safe_title.split())  # Normalize whitespace

    if len(safe_title) > 80:
        safe_title = safe_title[:80]

    if not safe_title:
        safe_title = str(doc['id'])

    return f"{safe_title}{extension}"


def clear_document_analyses(conn, doc_id: str, dry_run: bool):
    """Clear analyses and transcription jobs for a document."""
    if dry_run:
        return

    with conn.cursor() as cur:
        # Delete analyses
        cur.execute("DELETE FROM analysis WHERE document_id = %s", (str(doc_id),))
        # Delete transcription jobs
        cur.execute("DELETE FROM transcription_job WHERE document_id = %s", (str(doc_id),))
    conn.commit()


def reset_document_status(conn, doc_id: str, dry_run: bool):
    """Reset document status to pending."""
    if dry_run:
        return

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE document SET status = 'pending', updated_at = NOW() WHERE id = %s",
            (str(doc_id),)
        )
    conn.commit()


def delete_document(conn, doc_id: str, dry_run: bool):
    """Delete document and related records (for full reconstruct)."""
    if dry_run:
        return

    with conn.cursor() as cur:
        # CASCADE will handle analysis and transcription_job
        cur.execute("DELETE FROM document WHERE id = %s", (str(doc_id),))
    conn.commit()


def copy_to_staging(s3, bucket: str, archive_key: str, staging_key: str, dry_run: bool):
    """Copy file from archive to staging."""
    if dry_run:
        return

    s3.copy_object(
        Bucket=bucket,
        CopySource={'Bucket': bucket, 'Key': archive_key},
        Key=staging_key
    )


def upload_sidecar(s3, bucket: str, sidecar_key: str, sidecar: dict, dry_run: bool):
    """Upload sidecar JSON to S3."""
    if dry_run:
        return

    s3.put_object(
        Bucket=bucket,
        Key=sidecar_key,
        Body=json.dumps(sidecar, indent=2).encode(),
        ContentType='application/json'
    )


def main():
    parser = argparse.ArgumentParser(
        description="Reconstruct staging files from archive + database"
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
        "--source-id",
        type=int,
        help="Only reconstruct documents from this source (1=OTHER, 2=YOUTUBE, 3=FM)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--clear-analyses",
        action="store_true",
        help="Clear existing analyses and transcription jobs (forces full reprocess)"
    )
    parser.add_argument(
        "--delete-documents",
        action="store_true",
        help="Delete document records too (loader will recreate them)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of documents to process"
    )

    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN MODE - No changes will be made ===\n")

    # Connect
    logger.info("Connecting to database...")
    conn = get_db_connection(args.database_url)

    logger.info("Connecting to S3...")
    s3 = get_s3_client()

    try:
        # Get documents
        documents = get_documents(conn, args.source_id)

        if args.limit:
            documents = documents[:args.limit]

        logger.info(f"Found {len(documents)} documents to process")

        # Track results
        results = {
            'total': len(documents),
            'reconstructed': 0,
            'missing_archive': 0,
            'errors': 0,
        }
        missing_files = []

        for doc in documents:
            doc_id = str(doc['id'])
            s3_key = doc.get('s3_key', '')
            title = doc.get('title', 'Unknown')

            logger.info(f"\nProcessing: {title[:60]}...")
            logger.info(f"  ID: {doc_id}")
            logger.info(f"  Archive: {s3_key}")

            # Check if archive file exists
            if not s3_key or not check_archive_exists(s3, args.bucket, s3_key):
                logger.warning(f"  MISSING: Archive file not found!")
                results['missing_archive'] += 1
                missing_files.append({
                    'id': doc_id,
                    'title': title,
                    's3_key': s3_key,
                    'source_id': doc['source_id'],
                    'source_url': doc.get('source_url'),
                })
                continue

            try:
                # Generate staging paths
                staging_filename = generate_staging_filename(doc, s3_key)
                staging_key = f"staging/{staging_filename}"
                sidecar_key = f"{staging_key}.json"

                logger.info(f"  Staging: {staging_key}")

                # Reconstruct sidecar
                sidecar = reconstruct_sidecar(doc)

                if args.dry_run:
                    logger.info(f"  [DRY RUN] Would copy {s3_key} -> {staging_key}")
                    logger.info(f"  [DRY RUN] Would create sidecar: {sidecar_key}")
                    if args.clear_analyses:
                        logger.info(f"  [DRY RUN] Would clear analyses")
                    if args.delete_documents:
                        logger.info(f"  [DRY RUN] Would delete document record")
                else:
                    # Clear analyses if requested
                    if args.clear_analyses:
                        clear_document_analyses(conn, doc_id, args.dry_run)
                        logger.info(f"  Cleared analyses")

                    # Copy archive to staging
                    copy_to_staging(s3, args.bucket, s3_key, staging_key, args.dry_run)
                    logger.info(f"  Copied to staging")

                    # Upload sidecar
                    upload_sidecar(s3, args.bucket, sidecar_key, sidecar, args.dry_run)
                    logger.info(f"  Created sidecar")

                    # Delete document record if requested (loader will recreate)
                    if args.delete_documents:
                        delete_document(conn, doc_id, args.dry_run)
                        logger.info(f"  Deleted document record")

                results['reconstructed'] += 1

            except Exception as e:
                logger.error(f"  ERROR: {e}")
                results['errors'] += 1

        # Summary
        logger.info("\n" + "="*60)
        logger.info("SUMMARY")
        logger.info("="*60)
        logger.info(f"Total documents:     {results['total']}")
        logger.info(f"Reconstructed:       {results['reconstructed']}")
        logger.info(f"Missing archive:     {results['missing_archive']}")
        logger.info(f"Errors:              {results['errors']}")

        # Report missing files
        if missing_files:
            logger.info("\n" + "="*60)
            logger.info("MISSING ARCHIVE FILES")
            logger.info("="*60)

            # Group by source
            by_source = {}
            for f in missing_files:
                src = f['source_id']
                if src not in by_source:
                    by_source[src] = []
                by_source[src].append(f)

            for source_id, files in by_source.items():
                source_name = get_source_name(conn, source_id)
                logger.info(f"\n{source_name} ({len(files)} files):")
                for f in files:
                    logger.info(f"  - {f['title'][:50]}")
                    if f.get('source_url'):
                        logger.info(f"    URL: {f['source_url']}")

            # Write missing files to JSON for further processing
            missing_file_path = 'missing_archive_files.json'
            with open(missing_file_path, 'w') as f:
                json.dump(missing_files, f, indent=2, default=str)
            logger.info(f"\nMissing files written to: {missing_file_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
