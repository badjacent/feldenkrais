#!/usr/bin/env python3
"""
Migration script: SQLite to Neon PostgreSQL.

One-time script to migrate data from SQLite to PostgreSQL.
Downloads SQLite from S3, connects to PostgreSQL, and migrates all data.

Usage:
    # Dry run (no changes)
    python migrate_to_neon.py --sqlite-path ./knowledge.db --pg-url "postgresql://..." --dry-run

    # Actual migration
    python migrate_to_neon.py --sqlite-path ./knowledge.db --pg-url "postgresql://..."

    # Download from S3 first
    aws s3 cp s3://feldenkrais-prod/db/knowledge.db ./knowledge.db
"""

import argparse
import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# PostgreSQL schema (same as database.py)
PG_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS document_source (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS document_type (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS document (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title TEXT NOT NULL,
    source_url TEXT,
    s3_key TEXT,
    source_id INTEGER NOT NULL REFERENCES document_source(id),
    type_id INTEGER NOT NULL REFERENCES document_type(id),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    tags JSONB,
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS analysis_type (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS analysis (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    type_id INTEGER NOT NULL REFERENCES analysis_type(id),
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transcription_job (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    batch_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER DEFAULT 1,
    error TEXT,
    chunk_index INTEGER,
    chunk_offset_seconds REAL,
    total_chunks INTEGER,
    result_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS youtube_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    video_id TEXT,
    video_title TEXT,
    channel_name TEXT,
    channel_id TEXT,
    duration_seconds INTEGER,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fm_lesson_status (
    lesson_url TEXT PRIMARY KEY,
    lesson_title TEXT,
    youtube_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    video_count INTEGER DEFAULT 1,
    videos_staged INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

PG_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_document_source ON document(source_id);
CREATE INDEX IF NOT EXISTS idx_document_type ON document(type_id);
CREATE INDEX IF NOT EXISTS idx_document_status ON document(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_document_unique ON document(title, source_url);
CREATE INDEX IF NOT EXISTS idx_analysis_document ON analysis(document_id);
CREATE INDEX IF NOT EXISTS idx_analysis_type ON analysis(type_id);
CREATE INDEX IF NOT EXISTS idx_transcription_job_document ON transcription_job(document_id);
CREATE INDEX IF NOT EXISTS idx_transcription_job_status ON transcription_job(status);
CREATE INDEX IF NOT EXISTS idx_transcription_job_batch ON transcription_job(batch_id);
CREATE INDEX IF NOT EXISTS idx_youtube_queue_status ON youtube_queue(status);
CREATE INDEX IF NOT EXISTS idx_fm_lesson_status ON fm_lesson_status(status);
CREATE INDEX IF NOT EXISTS idx_fm_lesson_youtube ON fm_lesson_status(youtube_id);
"""


def get_sqlite_connection(db_path: str) -> sqlite3.Connection:
    """Connect to SQLite database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_pg_connection(database_url: str):
    """Connect to PostgreSQL database."""
    return psycopg2.connect(database_url)


def init_pg_schema(pg_conn):
    """Initialize PostgreSQL schema."""
    with pg_conn.cursor() as cur:
        cur.execute(PG_SCHEMA)
        for stmt in PG_INDEXES.strip().split(';'):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
    pg_conn.commit()
    logger.info("PostgreSQL schema initialized")


def migrate_enum_tables(sqlite_conn: sqlite3.Connection, pg_conn, dry_run: bool) -> dict:
    """Migrate enum tables (document_source, document_type, analysis_type)."""
    results = {}

    enum_tables = ['document_source', 'document_type', 'analysis_type']

    for table in enum_tables:
        cursor = sqlite_conn.execute(f"SELECT id, name FROM {table}")
        rows = cursor.fetchall()

        if dry_run:
            logger.info(f"[DRY RUN] Would migrate {len(rows)} rows from {table}")
            results[table] = len(rows)
            continue

        with pg_conn.cursor() as pg_cur:
            for row in rows:
                # Use explicit ID to preserve foreign key relationships
                pg_cur.execute(
                    f"""
                    INSERT INTO {table} (id, name)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                    """,
                    (row['id'], row['name'])
                )
                # Update sequence to be past our explicit IDs
                pg_cur.execute(
                    f"SELECT setval('{table}_id_seq', GREATEST((SELECT MAX(id) FROM {table}), 1))"
                )
        pg_conn.commit()

        logger.info(f"Migrated {len(rows)} rows from {table}")
        results[table] = len(rows)

    return results


def migrate_documents(sqlite_conn: sqlite3.Connection, pg_conn, dry_run: bool) -> int:
    """Migrate document table."""
    cursor = sqlite_conn.execute("""
        SELECT id, title, source_url, s3_key, source_id, type_id, status,
               created_at, updated_at, tags, metadata
        FROM document
    """)
    rows = cursor.fetchall()

    if dry_run:
        logger.info(f"[DRY RUN] Would migrate {len(rows)} documents")
        return len(rows)

    migrated = 0
    with pg_conn.cursor() as pg_cur:
        for row in rows:
            try:
                # SQLite stores tags as JSON string, need to keep as JSON for PostgreSQL
                tags = row['tags']
                metadata = row['metadata']

                pg_cur.execute(
                    """
                    INSERT INTO document (id, title, source_url, s3_key, source_id, type_id,
                                         status, created_at, updated_at, tags, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        row['id'], row['title'], row['source_url'], row['s3_key'],
                        row['source_id'], row['type_id'], row['status'],
                        row['created_at'], row['updated_at'],
                        tags, metadata
                    )
                )
                migrated += 1
            except Exception as e:
                logger.error(f"Error migrating document {row['id']}: {e}")

    pg_conn.commit()
    logger.info(f"Migrated {migrated} documents")
    return migrated


def migrate_analyses(sqlite_conn: sqlite3.Connection, pg_conn, dry_run: bool) -> int:
    """Migrate analysis table."""
    cursor = sqlite_conn.execute("""
        SELECT id, document_id, type_id, data, created_at, updated_at
        FROM analysis
    """)
    rows = cursor.fetchall()

    if dry_run:
        logger.info(f"[DRY RUN] Would migrate {len(rows)} analyses")
        return len(rows)

    migrated = 0
    with pg_conn.cursor() as pg_cur:
        for row in rows:
            try:
                pg_cur.execute(
                    """
                    INSERT INTO analysis (id, document_id, type_id, data, created_at, updated_at)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        row['id'], row['document_id'], row['type_id'],
                        row['data'], row['created_at'], row['updated_at']
                    )
                )
                migrated += 1
            except Exception as e:
                logger.error(f"Error migrating analysis {row['id']}: {e}")

    pg_conn.commit()
    logger.info(f"Migrated {migrated} analyses")
    return migrated


def migrate_transcription_jobs(sqlite_conn: sqlite3.Connection, pg_conn, dry_run: bool) -> int:
    """Migrate transcription_job table."""
    cursor = sqlite_conn.execute("""
        SELECT id, document_id, batch_id, status, attempts, error,
               chunk_index, chunk_offset_seconds, total_chunks, result_data,
               created_at, updated_at
        FROM transcription_job
    """)
    rows = cursor.fetchall()

    if dry_run:
        logger.info(f"[DRY RUN] Would migrate {len(rows)} transcription jobs")
        return len(rows)

    migrated = 0
    with pg_conn.cursor() as pg_cur:
        for row in rows:
            try:
                pg_cur.execute(
                    """
                    INSERT INTO transcription_job
                    (id, document_id, batch_id, status, attempts, error,
                     chunk_index, chunk_offset_seconds, total_chunks, result_data,
                     created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        row['id'], row['document_id'], row['batch_id'],
                        row['status'], row['attempts'], row['error'],
                        row['chunk_index'], row['chunk_offset_seconds'],
                        row['total_chunks'], row['result_data'],
                        row['created_at'], row['updated_at']
                    )
                )
                migrated += 1
            except Exception as e:
                logger.error(f"Error migrating transcription job {row['id']}: {e}")

    pg_conn.commit()
    logger.info(f"Migrated {migrated} transcription jobs")
    return migrated


def migrate_youtube_queue(sqlite_conn: sqlite3.Connection, pg_conn, dry_run: bool) -> int:
    """Migrate youtube_queue table."""
    cursor = sqlite_conn.execute("""
        SELECT id, url, status, video_id, video_title, channel_name, channel_id,
               duration_seconds, error, created_at, updated_at
        FROM youtube_queue
    """)
    rows = cursor.fetchall()

    if dry_run:
        logger.info(f"[DRY RUN] Would migrate {len(rows)} youtube queue entries")
        return len(rows)

    migrated = 0
    with pg_conn.cursor() as pg_cur:
        for row in rows:
            try:
                pg_cur.execute(
                    """
                    INSERT INTO youtube_queue
                    (id, url, status, video_id, video_title, channel_name, channel_id,
                     duration_seconds, error, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        row['id'], row['url'], row['status'],
                        row['video_id'], row['video_title'], row['channel_name'],
                        row['channel_id'], row['duration_seconds'], row['error'],
                        row['created_at'], row['updated_at']
                    )
                )
                migrated += 1
            except Exception as e:
                logger.error(f"Error migrating youtube queue {row['id']}: {e}")

    pg_conn.commit()
    logger.info(f"Migrated {migrated} youtube queue entries")
    return migrated


def migrate_fm_lessons(sqlite_conn: sqlite3.Connection, pg_conn, dry_run: bool) -> int:
    """Migrate fm_lesson_status table."""
    cursor = sqlite_conn.execute("""
        SELECT lesson_url, lesson_title, youtube_id, status, video_count,
               videos_staged, created_at, updated_at
        FROM fm_lesson_status
    """)
    rows = cursor.fetchall()

    if dry_run:
        logger.info(f"[DRY RUN] Would migrate {len(rows)} fm lesson entries")
        return len(rows)

    migrated = 0
    with pg_conn.cursor() as pg_cur:
        for row in rows:
            try:
                pg_cur.execute(
                    """
                    INSERT INTO fm_lesson_status
                    (lesson_url, lesson_title, youtube_id, status, video_count,
                     videos_staged, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (lesson_url) DO NOTHING
                    """,
                    (
                        row['lesson_url'], row['lesson_title'], row['youtube_id'],
                        row['status'], row['video_count'], row['videos_staged'],
                        row['created_at'], row['updated_at']
                    )
                )
                migrated += 1
            except Exception as e:
                logger.error(f"Error migrating fm lesson {row['lesson_url']}: {e}")

    pg_conn.commit()
    logger.info(f"Migrated {migrated} fm lesson entries")
    return migrated


def verify_migration(sqlite_conn: sqlite3.Connection, pg_conn) -> dict:
    """Verify migration by comparing row counts."""
    tables = [
        'document_source', 'document_type', 'analysis_type',
        'document', 'analysis', 'transcription_job',
        'youtube_queue', 'fm_lesson_status'
    ]

    results = {}
    all_match = True

    for table in tables:
        # SQLite count
        try:
            sqlite_cur = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}")
            sqlite_count = sqlite_cur.fetchone()[0]
        except sqlite3.OperationalError:
            sqlite_count = 0

        # PostgreSQL count
        with pg_conn.cursor() as pg_cur:
            pg_cur.execute(f"SELECT COUNT(*) FROM {table}")
            pg_count = pg_cur.fetchone()[0]

        match = sqlite_count == pg_count
        if not match:
            all_match = False

        results[table] = {
            'sqlite': sqlite_count,
            'postgres': pg_count,
            'match': match
        }

        status = "OK" if match else "MISMATCH"
        logger.info(f"{table}: SQLite={sqlite_count}, PostgreSQL={pg_count} [{status}]")

    results['all_match'] = all_match
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Migrate SQLite database to Neon PostgreSQL"
    )
    parser.add_argument(
        "--sqlite-path",
        required=True,
        help="Path to SQLite database file"
    )
    parser.add_argument(
        "--pg-url",
        required=True,
        help="PostgreSQL connection URL"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes"
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Skip schema creation (assume tables exist)"
    )

    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN MODE - No changes will be made ===")

    # Connect to databases
    logger.info(f"Connecting to SQLite: {args.sqlite_path}")
    sqlite_conn = get_sqlite_connection(args.sqlite_path)

    logger.info("Connecting to PostgreSQL...")
    pg_conn = get_pg_connection(args.pg_url)

    try:
        # Initialize schema
        if not args.skip_schema and not args.dry_run:
            init_pg_schema(pg_conn)

        # Migrate tables in order (respecting foreign keys)
        logger.info("\n=== Migrating enum tables ===")
        migrate_enum_tables(sqlite_conn, pg_conn, args.dry_run)

        logger.info("\n=== Migrating documents ===")
        migrate_documents(sqlite_conn, pg_conn, args.dry_run)

        logger.info("\n=== Migrating analyses ===")
        migrate_analyses(sqlite_conn, pg_conn, args.dry_run)

        logger.info("\n=== Migrating transcription jobs ===")
        migrate_transcription_jobs(sqlite_conn, pg_conn, args.dry_run)

        logger.info("\n=== Migrating youtube queue ===")
        migrate_youtube_queue(sqlite_conn, pg_conn, args.dry_run)

        logger.info("\n=== Migrating fm lessons ===")
        migrate_fm_lessons(sqlite_conn, pg_conn, args.dry_run)

        # Verify migration
        if not args.dry_run:
            logger.info("\n=== Verifying migration ===")
            results = verify_migration(sqlite_conn, pg_conn)
            if results['all_match']:
                logger.info("\nMigration verified successfully!")
            else:
                logger.warning("\nMigration verification found mismatches!")

        logger.info("\nMigration complete!")

    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
