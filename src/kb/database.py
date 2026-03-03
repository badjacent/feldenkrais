"""PostgreSQL database operations for the knowledge base."""

import json
from contextlib import contextmanager
from typing import Optional
from uuid import uuid4

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

# Schema for initialization (PostgreSQL)
SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document_source (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category_name TEXT,
    visible BOOLEAN NOT NULL DEFAULT TRUE,
    public BOOLEAN NOT NULL DEFAULT TRUE
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
    category_name TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    tags JSONB,
    metadata JSONB,
    lesson_date DATE,
    lesson_index TEXT
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

CREATE TABLE IF NOT EXISTS pipeline_log (
    id SERIAL PRIMARY KEY,
    event TEXT NOT NULL,
    document_id UUID,
    title TEXT,
    detail JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pipeline_node_state (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    input_hash TEXT NOT NULL DEFAULT '',
    output_hash TEXT NOT NULL DEFAULT '',
    last_run_at TIMESTAMPTZ,
    duration_seconds REAL,
    error_message TEXT,
    metadata JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lesson_embedding (
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    method TEXT NOT NULL DEFAULT 'default',
    embedding vector(1536),
    summary_text TEXT,
    summary_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (document_id, method)
);

CREATE TABLE IF NOT EXISTS lesson_concept (
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (document_id, category, value)
);

CREATE TABLE IF NOT EXISTS search_query_log (
    id SERIAL PRIMARY KEY,
    query TEXT NOT NULL,
    result_count INTEGER,
    specificity_band TEXT,
    specificity_gap REAL,
    top_results JSONB,
    user_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    login TEXT NOT NULL,
    name TEXT,
    email TEXT,
    avatar_url TEXT,
    tier TEXT NOT NULL DEFAULT 'user',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (provider, provider_id)
);

CREATE TABLE IF NOT EXISTS allowed_logins (
    login TEXT PRIMARY KEY,
    added_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_source_access (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES document_source(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, source_id)
);

CREATE TABLE IF NOT EXISTS user_beta_access (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feature TEXT NOT NULL,
    PRIMARY KEY (user_id, feature)
);
"""

INDEXES = """
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
CREATE INDEX IF NOT EXISTS idx_document_lesson_date ON document(lesson_date);
CREATE INDEX IF NOT EXISTS idx_pipeline_log_event ON pipeline_log(event);
CREATE INDEX IF NOT EXISTS idx_pipeline_log_created ON pipeline_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_log_document ON pipeline_log(document_id);
CREATE INDEX IF NOT EXISTS idx_lesson_embedding_hnsw ON lesson_embedding USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_lesson_embedding_method ON lesson_embedding (method);
CREATE INDEX IF NOT EXISTS idx_lesson_concept_value ON lesson_concept (value);
CREATE INDEX IF NOT EXISTS idx_lesson_concept_category ON lesson_concept (category, value);
"""

SEED_DATA = """
INSERT INTO document_source (id, name) VALUES (1, 'OTHER'), (2, 'YOUTUBE'), (3, 'FELDENKRAIS_MANHATTAN'), (4, 'FELDENKRAIS_PROJECT') ON CONFLICT DO NOTHING;
INSERT INTO document_type (id, name) VALUES (1, 'TEXT'), (2, 'AUDIO'), (3, 'VIDEO') ON CONFLICT DO NOTHING;
INSERT INTO analysis_type (id, name) VALUES (1, 'TRANSCRIPTION'), (2, 'ANNOTATION') ON CONFLICT DO NOTHING;
"""


class Database:
    """PostgreSQL database wrapper for the knowledge base."""

    def __init__(self, database_url: str, min_connections: int = 1, max_connections: int = 10):
        self.database_url = database_url
        self._pool: Optional[pool.ThreadedConnectionPool] = None
        self._min_connections = min_connections
        self._max_connections = max_connections
        self.public_only = False

    def _get_pool(self) -> pool.ThreadedConnectionPool:
        """Get or create the connection pool."""
        if self._pool is None:
            self._pool = pool.ThreadedConnectionPool(
                self._min_connections,
                self._max_connections,
                self.database_url,
            )
        return self._pool

    @contextmanager
    def get_cursor(self, commit: bool = True):
        """
        Context manager for database operations.

        Yields a cursor and handles connection management.
        Commits by default; set commit=False for read-only operations.
        """
        conn = self._get_pool().getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                yield cursor
                if commit:
                    conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._get_pool().putconn(conn)

    def close(self):
        """Close all database connections."""
        if self._pool:
            self._pool.closeall()

    # Advisory lock IDs for Lambda concurrency control
    LOCK_LOADER = 1
    LOCK_TRANSCRIPTION = 2
    LOCK_YOUTUBE = 3
    LOCK_SCRAPER = 4

    def try_acquire_lock(self, lock_id: int) -> bool:
        """
        Try to acquire a PostgreSQL advisory lock (non-blocking).

        Returns True if lock acquired, False if already held by another process.
        Lock is automatically released when connection closes.
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
            result = cursor.fetchone()
            return result["pg_try_advisory_lock"] if result else False

    def release_lock(self, lock_id: int) -> bool:
        """Release a PostgreSQL advisory lock."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
            result = cursor.fetchone()
            return result["pg_advisory_unlock"] if result else False
            self._pool = None

    def initialize(self):
        """Initialize the database schema."""
        with self.get_cursor() as cursor:
            # Create tables
            cursor.execute(SCHEMA)
            # Create indexes (each separately to handle IF NOT EXISTS properly)
            for statement in INDEXES.strip().split(';'):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
            # Migrations: add columns if missing (safe for concurrent execution)
            for table, column, col_type in [
                ("document_source", "category_name", "TEXT"),
                ("document_source", "visible", "BOOLEAN NOT NULL DEFAULT TRUE"),
                ("document_source", "public", "BOOLEAN NOT NULL DEFAULT TRUE"),
                ("document", "category_name", "TEXT"),
                ("document", "transcription_retries", "INTEGER DEFAULT 0"),
            ]:
                cursor.execute(
                    """SELECT 1 FROM information_schema.columns
                       WHERE table_name = %s AND column_name = %s""",
                    (table, column),
                )
                if not cursor.fetchone():
                    cursor.execute(f"""
                        DO $$ BEGIN
                            ALTER TABLE {table} ADD COLUMN {column} {col_type};
                        EXCEPTION WHEN duplicate_column THEN NULL;
                        END $$;
                    """)
            # Seed enum values
            for statement in SEED_DATA.strip().split(';'):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)

    def create_document(
        self,
        title: str,
        s3_key: str,
        source_id: int,
        type_id: int,
        source_url: Optional[str] = None,
        tags: Optional[str] = None,
        metadata: Optional[str] = None,
        lesson_date: Optional[str] = None,
        lesson_index: Optional[str] = None,
    ) -> str:
        """Create a new document record. Returns the document ID."""
        doc_id = str(uuid4())
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document (id, title, source_url, s3_key, source_id, type_id, tags, metadata, lesson_date, lesson_index)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                """,
                (doc_id, title, source_url, s3_key, source_id, type_id, tags, metadata, lesson_date, lesson_index),
            )
        return doc_id

    def get_document(self, doc_id: str) -> Optional[dict]:
        """Get a document by ID."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM document WHERE id = %s",
                (doc_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def document_exists(self, title: str, source_url: Optional[str] = None) -> bool:
        """Check if a document with the same title and source_url already exists."""
        with self.get_cursor(commit=False) as cursor:
            if source_url:
                cursor.execute(
                    "SELECT 1 FROM document WHERE title = %s AND source_url = %s LIMIT 1",
                    (title, source_url),
                )
            else:
                cursor.execute(
                    "SELECT 1 FROM document WHERE title = %s AND source_url IS NULL LIMIT 1",
                    (title,),
                )
            return cursor.fetchone() is not None

    def claim_document(self, doc_id: str, from_status: str, to_status: str) -> bool:
        """
        Atomically claim a document by updating its status.

        Returns True if the document was claimed (status was from_status).
        Returns False if another worker already claimed it.
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE document
                SET status = %s, updated_at = NOW()
                WHERE id = %s AND status = %s
                """,
                (to_status, doc_id, from_status),
            )
            return cursor.rowcount > 0

    def update_document_status(self, doc_id: str, status: str):
        """Update document status unconditionally."""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE document SET status = %s, updated_at = NOW() WHERE id = %s",
                (status, doc_id),
            )

    def get_documents_by_status(self, status: str, limit: int = 100) -> list[dict]:
        """Get documents with a specific status."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM document WHERE status = %s ORDER BY created_at LIMIT %s",
                (status, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_documents(self) -> list[dict]:
        """List all documents."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT * FROM document ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_untranscribed_documents(self, max_retries: int = 5) -> list[dict]:
        """
        Get audio/video documents that need transcription.

        Returns documents that:
        - Are audio (type_id=2) or video (type_id=3)
        - Have no successful transcription analysis
        - Are not currently pending or failed (auto-retry handles failed docs separately)
        - Have not exceeded the document-level retry limit
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT d.* FROM document d
                WHERE d.type_id IN (2, 3)
                -- No successful transcription yet
                AND NOT EXISTS (
                    SELECT 1 FROM analysis a
                    WHERE a.document_id = d.id AND a.type_id = 1
                )
                -- No pending or failed jobs (auto-retry cleans up failed jobs first)
                AND NOT EXISTS (
                    SELECT 1 FROM transcription_job j
                    WHERE j.document_id = d.id AND j.status IN ('pending', 'failed')
                )
                -- Under document-level retry limit
                AND COALESCE(d.transcription_retries, 0) < %s
                ORDER BY d.created_at
                """,
                (max_retries,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def create_transcription_job(
        self,
        document_id: str,
        batch_id: Optional[str] = None,
        chunk_index: Optional[int] = None,
        chunk_offset_seconds: Optional[float] = None,
        total_chunks: Optional[int] = None,
    ) -> str:
        """Create a new transcription job. Returns the job ID."""
        job_id = str(uuid4())
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO transcription_job
                (id, document_id, batch_id, status, chunk_index, chunk_offset_seconds, total_chunks)
                VALUES (%s, %s, %s, 'pending', %s, %s, %s)
                """,
                (job_id, document_id, batch_id, chunk_index, chunk_offset_seconds, total_chunks),
            )
        return job_id

    def get_transcription_job(self, job_id: str) -> Optional[dict]:
        """Get a transcription job by ID."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM transcription_job WHERE id = %s",
                (job_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_transcription_jobs_by_batch(self, batch_id: str) -> list[dict]:
        """Get all transcription jobs for a batch."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM transcription_job WHERE batch_id = %s ORDER BY chunk_index",
                (batch_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_pending_batches(self) -> list[str]:
        """Get unique batch IDs that have pending jobs."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT DISTINCT batch_id FROM transcription_job
                WHERE status = 'pending' AND batch_id IS NOT NULL
                """
            )
            return [row["batch_id"] for row in cursor.fetchall()]

    def update_transcription_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        batch_id: Optional[str] = None,
        error: Optional[str] = None,
        result_data: Optional[str] = None,
        increment_attempts: bool = False,
        chunk_index: Optional[int] = None,
        chunk_offset_seconds: Optional[float] = None,
        total_chunks: Optional[int] = None,
    ):
        """Update a transcription job."""
        updates = ["updated_at = NOW()"]
        params = []

        if status is not None:
            updates.append("status = %s")
            params.append(status)
        if batch_id is not None:
            updates.append("batch_id = %s")
            params.append(batch_id)
        if error is not None:
            updates.append("error = %s")
            params.append(error)
        if result_data is not None:
            updates.append("result_data = %s::jsonb")
            params.append(result_data)
        if increment_attempts:
            updates.append("attempts = attempts + 1")
        if chunk_index is not None:
            updates.append("chunk_index = %s")
            params.append(chunk_index)
        if chunk_offset_seconds is not None:
            updates.append("chunk_offset_seconds = %s")
            params.append(chunk_offset_seconds)
        if total_chunks is not None:
            updates.append("total_chunks = %s")
            params.append(total_chunks)

        params.append(job_id)
        with self.get_cursor() as cursor:
            cursor.execute(
                f"UPDATE transcription_job SET {', '.join(updates)} WHERE id = %s",
                params,
            )

    def delete_transcription_job(self, job_id: str):
        """Delete a transcription job."""
        with self.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM transcription_job WHERE id = %s",
                (job_id,),
            )

    def reset_failed_jobs_for_retry(self, max_attempts: int = 3) -> list[dict]:
        """
        Reset failed jobs to pending for retry.

        Only resets jobs under max_attempts. Returns list of reset jobs.
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE transcription_job
                SET status = 'pending', batch_id = NULL, error = NULL
                WHERE status = 'failed' AND attempts < %s
                RETURNING id, document_id, chunk_index, attempts
                """,
                (max_attempts,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_failed_transcription_job(self, document_id: str) -> Optional[dict]:
        """Get the most recent failed transcription job for a document."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT * FROM transcription_job
                WHERE document_id = %s AND status = 'failed'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (document_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_failed_documents_for_retry(self, max_retries: int = 5) -> list[dict]:
        """
        Get documents whose transcription failed and are eligible for retry.

        Returns documents that:
        - Have at least one failed transcription job
        - Have no pending jobs (not currently in progress)
        - Have no successful transcription
        - Have not exceeded the document-level retry limit
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT DISTINCT d.id, d.title,
                       COALESCE(d.transcription_retries, 0) as transcription_retries
                FROM document d
                JOIN transcription_job j ON j.document_id = d.id
                WHERE j.status = 'failed'
                -- No pending jobs
                AND NOT EXISTS (
                    SELECT 1 FROM transcription_job j2
                    WHERE j2.document_id = d.id AND j2.status = 'pending'
                )
                -- No successful transcription
                AND NOT EXISTS (
                    SELECT 1 FROM analysis a
                    WHERE a.document_id = d.id AND a.type_id = 1
                )
                -- Under retry limit
                AND COALESCE(d.transcription_retries, 0) < %s
                ORDER BY d.title
                """,
                (max_retries,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def expire_stale_pending_jobs(self, max_age_hours: int = 24) -> int:
        """
        Mark pending transcription jobs older than max_age_hours as failed.

        Prevents jobs from being stuck in 'pending' forever when Groq batches
        become unreachable or expire without being polled.

        Returns the number of jobs expired.
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE transcription_job
                SET status = 'failed',
                    error = 'Expired: pending longer than ' || %s || ' hours',
                    updated_at = NOW()
                WHERE status = 'pending'
                AND created_at < NOW() - INTERVAL '1 hour' * %s
                """,
                (max_age_hours, max_age_hours),
            )
            return cursor.rowcount

    def restage_document_for_retry(self, document_id: str) -> int:
        """
        Delete all transcription jobs for a document and increment its retry counter.

        Returns the number of jobs deleted.
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM transcription_job WHERE document_id = %s",
                (document_id,),
            )
            deleted = cursor.rowcount
            cursor.execute(
                """
                UPDATE document
                SET transcription_retries = COALESCE(transcription_retries, 0) + 1,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (document_id,),
            )
            return deleted

    def create_analysis(
        self,
        document_id: str,
        type_id: int,
        data: str,
    ) -> str:
        """Create a new analysis record. Returns the analysis ID."""
        analysis_id = str(uuid4())
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO analysis (id, document_id, type_id, data)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (analysis_id, document_id, type_id, data),
            )
        return analysis_id

    def get_analysis(self, document_id: str, type_id: int) -> Optional[dict]:
        """Get an analysis by document ID and type."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM analysis WHERE document_id = %s AND type_id = %s",
                (document_id, type_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # YouTube Queue methods

    def add_youtube_url(self, url: str) -> Optional[str]:
        """
        Add a YouTube URL to the queue.
        Returns the queue ID, or None if URL already exists.
        """
        queue_id = str(uuid4())
        try:
            with self.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO youtube_queue (id, url) VALUES (%s, %s)",
                    (queue_id, url),
                )
            return queue_id
        except psycopg2.errors.UniqueViolation:
            # URL already exists
            return None

    def get_pending_youtube_urls(self, limit: int = 10) -> list[dict]:
        """Get pending YouTube URLs to process."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM youtube_queue WHERE status = 'pending' ORDER BY created_at LIMIT %s",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def claim_youtube_url(self, queue_id: str) -> bool:
        """Claim a YouTube URL for processing. Returns True if claimed."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE youtube_queue
                SET status = 'processing', updated_at = NOW()
                WHERE id = %s AND status = 'pending'
                """,
                (queue_id,),
            )
            return cursor.rowcount > 0

    def update_youtube_queue(
        self,
        queue_id: str,
        status: Optional[str] = None,
        video_id: Optional[str] = None,
        video_title: Optional[str] = None,
        channel_name: Optional[str] = None,
        channel_id: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        error: Optional[str] = None,
    ):
        """Update a YouTube queue entry."""
        updates = ["updated_at = NOW()"]
        params = []

        if status is not None:
            updates.append("status = %s")
            params.append(status)
        if video_id is not None:
            updates.append("video_id = %s")
            params.append(video_id)
        if video_title is not None:
            updates.append("video_title = %s")
            params.append(video_title)
        if channel_name is not None:
            updates.append("channel_name = %s")
            params.append(channel_name)
        if channel_id is not None:
            updates.append("channel_id = %s")
            params.append(channel_id)
        if duration_seconds is not None:
            updates.append("duration_seconds = %s")
            params.append(duration_seconds)
        if error is not None:
            updates.append("error = %s")
            params.append(error)

        params.append(queue_id)
        with self.get_cursor() as cursor:
            cursor.execute(
                f"UPDATE youtube_queue SET {', '.join(updates)} WHERE id = %s",
                params,
            )

    def get_youtube_queue_stats(self) -> dict:
        """Get statistics about the YouTube queue."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT status, COUNT(*) as count
                FROM youtube_queue
                GROUP BY status
                """
            )
            stats = {row["status"]: row["count"] for row in cursor.fetchall()}
            return stats

    def list_youtube_queue(self, status: Optional[str] = None) -> list[dict]:
        """List YouTube queue entries, optionally filtered by status."""
        with self.get_cursor(commit=False) as cursor:
            if status:
                cursor.execute(
                    "SELECT * FROM youtube_queue WHERE status = %s ORDER BY created_at",
                    (status,),
                )
            else:
                cursor.execute(
                    "SELECT * FROM youtube_queue ORDER BY created_at"
                )
            return [dict(row) for row in cursor.fetchall()]

    # Feldenkrais Manhattan lesson tracking

    def get_fm_lesson_status(self, lesson_url: str) -> Optional[dict]:
        """Get the status of a Feldenkrais Manhattan lesson."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM fm_lesson_status WHERE lesson_url = %s",
                (lesson_url,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def is_fm_lesson_complete(self, lesson_url: str) -> bool:
        """Check if a lesson has been fully processed."""
        status = self.get_fm_lesson_status(lesson_url)
        return status is not None and status.get('status') == 'completed'

    def get_pending_fm_lessons(self) -> list[str]:
        """Get lesson URLs that haven't been completed."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT lesson_url FROM fm_lesson_status WHERE status != 'completed'"
            )
            return [row['lesson_url'] for row in cursor.fetchall()]

    def get_completed_fm_lessons(self) -> set[str]:
        """Get set of completed lesson URLs for fast lookup."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT lesson_url FROM fm_lesson_status WHERE status = 'completed'"
            )
            return {row['lesson_url'] for row in cursor.fetchall()}

    def upsert_fm_lesson(
        self,
        lesson_url: str,
        lesson_title: str,
        youtube_id: Optional[str] = None,
        video_count: int = 1,
        status: str = 'pending',
        videos_staged: int = 0,
    ):
        """Insert or update a Feldenkrais Manhattan lesson."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fm_lesson_status (lesson_url, lesson_title, youtube_id, video_count, status, videos_staged)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(lesson_url) DO UPDATE SET
                    lesson_title = EXCLUDED.lesson_title,
                    youtube_id = COALESCE(EXCLUDED.youtube_id, fm_lesson_status.youtube_id),
                    video_count = EXCLUDED.video_count,
                    status = EXCLUDED.status,
                    videos_staged = EXCLUDED.videos_staged,
                    updated_at = NOW()
                """,
                (lesson_url, lesson_title, youtube_id, video_count, status, videos_staged),
            )

    def get_fm_lesson_youtube_ids(self) -> dict[str, str]:
        """Get mapping of lesson_url to youtube_id for all known lessons."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT lesson_url, youtube_id FROM fm_lesson_status WHERE youtube_id IS NOT NULL"
            )
            return {row['lesson_url']: row['youtube_id'] for row in cursor.fetchall()}

    def get_all_fm_lessons(self) -> list[dict]:
        """Get all FM lessons with their cached data."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT * FROM fm_lesson_status")
            return [dict(row) for row in cursor.fetchall()]

    def mark_fm_lesson_complete(self, lesson_url: str):
        """Mark a lesson as fully processed."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE fm_lesson_status
                SET status = 'completed', updated_at = NOW()
                WHERE lesson_url = %s
                """,
                (lesson_url,),
            )

    def update_document_lesson_fields(
        self,
        doc_id: str,
        lesson_date: Optional[str] = None,
        lesson_index: Optional[str] = None,
    ):
        """Update lesson_date and lesson_index for a document."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE document
                SET lesson_date = %s, lesson_index = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (lesson_date, lesson_index, doc_id),
            )

    # Lesson browsing methods (for web UI)

    def get_lesson_categories(self) -> list[dict]:
        """Get document sources that have at least one transcribed document.

        Returns list of dicts with: source_id, source_name, category_name, lesson_count.
        """
        public_filter = " AND ds.public = TRUE" if self.public_only else ""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                SELECT ds.id as source_id, ds.name as source_name,
                       ds.category_name as source_category_name,
                       COUNT(DISTINCT d.id) as lesson_count
                FROM document_source ds
                JOIN document d ON d.source_id = ds.id
                JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
                WHERE ds.visible = TRUE{public_filter}
                GROUP BY ds.id, ds.name, ds.category_name
                ORDER BY ds.id
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_source_lessons(self, source_id: int) -> list[dict]:
        """Get transcribed documents for a source.

        Returns list of dicts with: id, title, category_name, created_at.
        Uses tiered override: document.category_name > source.category_name > source.name.
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT d.id, d.title, d.category_name,
                       d.lesson_date, d.lesson_index, d.created_at,
                       d.metadata->'scraper'->>'instructor' as instructor,
                       d.metadata->>'lesson_type' as lesson_type,
                       d.source_url
                FROM document d
                JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
                WHERE d.source_id = %s
                ORDER BY d.lesson_date NULLS LAST, d.lesson_index NULLS LAST, d.title
            """, (source_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_document_transcription(self, document_id: str) -> Optional[str]:
        """Get the transcription text for a document."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT a.data->>'text' as text
                FROM analysis a
                WHERE a.document_id = %s AND a.type_id = 1
            """, (document_id,))
            row = cursor.fetchone()
            return row["text"] if row else None

    def get_document_whisper_segments(self, document_id: str) -> Optional[list]:
        """Get whisper segments for a document from its transcription analysis."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT a.data->'segments' as segments
                FROM analysis a
                WHERE a.document_id = %s AND a.type_id = 1
            """, (document_id,))
            row = cursor.fetchone()
            if row and row["segments"]:
                return row["segments"] if isinstance(row["segments"], list) else json.loads(row["segments"])
            return None

    def get_document_whisper_words(self, document_id: str) -> Optional[list]:
        """Get word-level timestamps for a document from its transcription analysis."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT a.data->'words' as words
                FROM analysis a
                WHERE a.document_id = %s AND a.type_id = 1
            """, (document_id,))
            row = cursor.fetchone()
            if row and row["words"]:
                return row["words"] if isinstance(row["words"], list) else json.loads(row["words"])
            return None

    def get_lesson_info(self, document_id: str) -> Optional[dict]:
        """Get lesson info for the detail page."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT d.id::text as document_id, d.title,
                       ds.name as source_name, d.source_url,
                       a.data->>'text' as text,
                       (d.s3_key IS NOT NULL) as has_audio
                FROM document d
                JOIN document_source ds ON ds.id = d.source_id
                LEFT JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
                WHERE d.id = %s
            """, (document_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_source_category_name(self, source_id: int, category_name: str):
        """Set the display category name for a document source."""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE document_source SET category_name = %s WHERE id = %s",
                (category_name, source_id),
            )

    # Pipeline logging

    def log_search_query(self, query: str, result_count: int = None,
                         specificity_band: str = None, specificity_gap: float = None,
                         top_results: list = None, user_id: int = None):
        """Log a search query for usage analysis."""
        top_json = json.dumps(top_results) if top_results else None
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO search_query_log
                    (query, result_count, specificity_band, specificity_gap, top_results, user_id)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                """,
                (query, result_count, specificity_band, specificity_gap, top_json, user_id),
            )

    # User management

    def upsert_user(self, provider: str, provider_id: str, login: str,
                    name: str = None, email: str = None,
                    avatar_url: str = None) -> dict:
        """Insert or update an OAuth user. Returns the user dict."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (provider, provider_id, login, name, email, avatar_url)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (provider, provider_id) DO UPDATE SET
                    login = EXCLUDED.login,
                    name = EXCLUDED.name,
                    email = EXCLUDED.email,
                    avatar_url = EXCLUDED.avatar_url,
                    last_login_at = NOW()
                RETURNING id, provider, provider_id, login, name, email,
                          avatar_url, tier, created_at, last_login_at
                """,
                (provider, provider_id, login, name, email, avatar_url),
            )
            return dict(cursor.fetchone())

    def get_user(self, user_id: int) -> dict | None:
        """Get a user by id. Returns dict or None."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def is_login_allowed(self, login: str) -> bool:
        """Check if a GitHub login is on the allowlist."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT 1 FROM allowed_logins WHERE login = %s", (login,))
            return cursor.fetchone() is not None

    def get_user_source_ids(self, user_id: int) -> list[int]:
        """Return source_ids this user has been granted access to."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT source_id FROM user_source_access WHERE user_id = %s",
                (user_id,),
            )
            return [row["source_id"] for row in cursor.fetchall()]

    def get_user_beta_features(self, user_id: int) -> list[str]:
        """Return beta feature names this user has access to."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT feature FROM user_beta_access WHERE user_id = %s",
                (user_id,),
            )
            return [row["feature"] for row in cursor.fetchall()]

    def log_event(self, event: str, title: str = None,
                  document_id: str = None, detail: dict = None):
        """Append an event to the pipeline log."""
        detail_json = json.dumps(detail) if detail else None
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_log (event, document_id, title, detail)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (event, document_id, title, detail_json),
            )

    def get_pipeline_log(self, limit: int = 50, event: str = None) -> list[dict]:
        """Get recent pipeline events, optionally filtered by event type."""
        with self.get_cursor(commit=False) as cursor:
            if event:
                cursor.execute(
                    """
                    SELECT * FROM pipeline_log
                    WHERE event = %s
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (event, limit),
                )
            else:
                cursor.execute(
                    "SELECT * FROM pipeline_log ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
            return [dict(row) for row in cursor.fetchall()]

    def count_events_for_filename(self, event: str, filename: str) -> int:
        """Count pipeline_log events matching event type and filename in detail."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM pipeline_log
                WHERE event = %s AND detail->>'filename' = %s
                """,
                (event, filename),
            )
            return cursor.fetchone()["count"]

    def get_permanently_failed_documents(self, max_retries: int = 5) -> list[dict]:
        """Get documents that exceeded transcription retry limit with no transcription."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT d.id, d.title, COALESCE(d.transcription_retries, 0) as retry_count,
                       d.created_at
                FROM document d
                WHERE d.type_id IN (2, 3)
                AND NOT EXISTS (
                    SELECT 1 FROM analysis a
                    WHERE a.document_id = d.id AND a.type_id = 1
                )
                AND COALESCE(d.transcription_retries, 0) >= %s
                ORDER BY d.created_at DESC
                """,
                (max_retries,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_pipeline_summary(self) -> dict:
        """Get pipeline status counts."""
        with self.get_cursor(commit=False) as cursor:
            if self.public_only:
                # Document counts by status (public sources only)
                cursor.execute("""
                    SELECT d.status, COUNT(*) as count
                    FROM document d
                    JOIN document_source ds ON ds.id = d.source_id
                    WHERE ds.public = TRUE
                    GROUP BY d.status
                """)
            else:
                cursor.execute("""
                    SELECT status, COUNT(*) as count
                    FROM document GROUP BY status
                """)
            doc_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

            # Transcription job counts by status
            cursor.execute("""
                SELECT status, COUNT(*) as count
                FROM transcription_job GROUP BY status
            """)
            job_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

            # Total documents
            if self.public_only:
                cursor.execute("""
                    SELECT COUNT(*) as count FROM document d
                    JOIN document_source ds ON ds.id = d.source_id
                    WHERE ds.public = TRUE
                """)
            else:
                cursor.execute("SELECT COUNT(*) as count FROM document")
            doc_total = cursor.fetchone()["count"]

            # Total with transcriptions
            if self.public_only:
                cursor.execute("""
                    SELECT COUNT(DISTINCT a.document_id) as count
                    FROM analysis a
                    JOIN document d ON d.id = a.document_id
                    JOIN document_source ds ON ds.id = d.source_id
                    WHERE a.type_id = 1 AND ds.public = TRUE
                """)
            else:
                cursor.execute("""
                    SELECT COUNT(DISTINCT document_id) as count
                    FROM analysis WHERE type_id = 1
                """)
            transcribed_count = cursor.fetchone()["count"]

            return {
                "documents": {"total": doc_total, **doc_counts},
                "transcription_jobs": job_counts,
                "transcribed": transcribed_count,
            }

    def get_document_pipeline_view(self, limit: int = 50) -> list[dict]:
        """Get documents with their pipeline status for the dashboard."""
        public_filter = "\nWHERE ds.public = TRUE" if self.public_only else ""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                SELECT d.id, d.title, d.status, d.created_at, d.updated_at,
                       ds.name as source_name,
                       ds.category_name as source_category,
                       COALESCE(d.category_name, ds.category_name, ds.name) as display_category,
                       (SELECT COUNT(*) FROM transcription_job j
                        WHERE j.document_id = d.id) as job_count,
                       (SELECT j.status FROM transcription_job j
                        WHERE j.document_id = d.id
                        ORDER BY j.updated_at DESC LIMIT 1) as latest_job_status,
                       (SELECT j.error FROM transcription_job j
                        WHERE j.document_id = d.id AND j.status = 'failed'
                        ORDER BY j.updated_at DESC LIMIT 1) as latest_error,
                       EXISTS(SELECT 1 FROM analysis a
                              WHERE a.document_id = d.id AND a.type_id = 1) as has_transcription,
                       EXISTS(SELECT 1 FROM lesson_embedding le
                              WHERE le.document_id = d.id) as has_embedding,
                       COALESCE(d.transcription_retries, 0) as retry_count
                FROM document d
                JOIN document_source ds ON ds.id = d.source_id{public_filter}
                ORDER BY d.updated_at DESC
                LIMIT %s
            """, (limit,))
            rows = [dict(row) for row in cursor.fetchall()]

        # Check which docs have condensed analysis cached
        condensed_doc_ids = set()
        try:
            for entry in self.get_docs_with_condensed():
                condensed_doc_ids.add(entry["doc_id"])
        except Exception:
            pass

        # Compute pipeline_stage for each document
        for row in rows:
            doc_id = str(row["id"])
            has_tx = row.get("has_transcription", False)
            has_embedding = row.get("has_embedding", False)
            has_condensed = doc_id in condensed_doc_ids
            latest_job = row.get("latest_job_status", "")
            retry_count = row.get("retry_count", 0)

            if has_embedding:
                stage = "embedded"
            elif has_condensed:
                stage = "analyzed"
            elif has_tx:
                stage = "transcribed"
            elif latest_job == "pending":
                stage = "transcribing"
            elif latest_job == "failed" or retry_count >= 5:
                stage = "failed"
            elif row.get("job_count", 0) > 0:
                stage = "transcribing"
            else:
                stage = "awaiting_transcription"

            row["has_condensed"] = has_condensed
            row["pipeline_stage"] = stage

        return rows

    # Cache operations

    def get_cache(self, key: str) -> dict | None:
        """Load a cached value by key."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT data FROM cache WHERE key = %s", (key,))
            row = cursor.fetchone()
            return dict(row["data"]) if row else None

    def set_cache(self, key: str, data: dict) -> None:
        """Store a value in the cache (upsert)."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cache (key, data, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (key) DO UPDATE
                SET data = EXCLUDED.data, updated_at = NOW()
                """,
                (key, json.dumps(data)),
            )

    def clear_cache(self, prefix: str | None = None) -> int:
        """Delete cache entries. If prefix given, only delete matching keys."""
        with self.get_cursor() as cursor:
            if prefix:
                cursor.execute(
                    "DELETE FROM cache WHERE key LIKE %s",
                    (prefix + "%",),
                )
            else:
                cursor.execute("DELETE FROM cache")
            return cursor.rowcount

    def list_cache(self) -> list[dict]:
        """List all cache entries with key and data size."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT key, length(data::text) as size_bytes, created_at, updated_at
                FROM cache ORDER BY key
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_cache_keys(self) -> set[str]:
        """Return the set of all cache keys (fast, no data loaded)."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT key FROM cache")
            return {row["key"] for row in cursor.fetchall()}

    # Pipeline node state operations

    def ensure_pipeline_node_state_table(self):
        """Create pipeline_node_state table if it doesn't exist."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_node_state (
                    name TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending',
                    input_hash TEXT NOT NULL DEFAULT '',
                    output_hash TEXT NOT NULL DEFAULT '',
                    last_run_at TIMESTAMPTZ,
                    duration_seconds REAL,
                    error_message TEXT,
                    metadata JSONB,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

    def get_all_transcription_hashes(self) -> dict[str, str]:
        """Return {document_id: cache_key} for all documents with transcriptions.

        Computes SHA256 hashes in Python to match get_cache_key() in web.py.
        Single DB round-trip instead of one per document.
        """
        import hashlib
        public_join = """
                JOIN document d ON d.id = a.document_id
                JOIN document_source ds ON ds.id = d.source_id""" if self.public_only else ""
        public_filter = " AND ds.public = TRUE" if self.public_only else ""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                SELECT a.document_id::text as doc_id, a.data->>'text' as text
                FROM analysis a{public_join}
                WHERE a.type_id = 1 AND a.data->>'text' IS NOT NULL{public_filter}
            """)
            result = {}
            for row in cursor.fetchall():
                text = row["text"].strip()
                if text:
                    result[row["doc_id"]] = hashlib.sha256(text.encode()).hexdigest()[:16]
            return result

    # Lesson embedding operations

    def upsert_embedding(self, document_id: str, embedding: list[float],
                         summary_text: str, summary_hash: str, method: str = "default"):
        """Insert or update a lesson embedding."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO lesson_embedding (document_id, method, embedding, summary_text, summary_hash, updated_at)
                VALUES (%s, %s, %s::vector, %s, %s, NOW())
                ON CONFLICT (document_id, method) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    summary_text = EXCLUDED.summary_text,
                    summary_hash = EXCLUDED.summary_hash,
                    updated_at = NOW()
                """,
                (document_id, method, str(embedding), summary_text, summary_hash),
            )

    def search_lessons(self, query_embedding: list[float], limit: int = 10, method: str = "default") -> list[dict]:
        """Search lessons by cosine similarity. Returns list of {document_id, title, similarity, summary_text, source_id, category_name}."""
        public_join = "\nJOIN document_source ds ON ds.id = d.source_id" if self.public_only else ""
        public_filter = " AND ds.public = TRUE" if self.public_only else ""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                f"""
                SELECT le.document_id::text, d.title,
                       1 - (le.embedding <=> %s::vector) as similarity,
                       le.summary_text,
                       d.source_id,
                       d.category_name
                FROM lesson_embedding le
                JOIN document d ON d.id = le.document_id{public_join}
                WHERE le.method = %s{public_filter}
                ORDER BY le.embedding <=> %s::vector
                LIMIT %s
                """,
                (str(query_embedding), method, str(query_embedding), limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_embedding_hashes(self, method: str = "default") -> dict[str, str]:
        """Return {document_id: summary_hash} for all embedded lessons."""
        if self.public_only:
            query = """
                SELECT le.document_id::text, le.summary_hash
                FROM lesson_embedding le
                JOIN document d ON d.id = le.document_id
                JOIN document_source ds ON ds.id = d.source_id
                WHERE le.method = %s AND ds.public = TRUE
            """
        else:
            query = "SELECT document_id::text, summary_hash FROM lesson_embedding WHERE method = %s"
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(query, (method,))
            return {row["document_id"]: row["summary_hash"] for row in cursor.fetchall()}

    # Lesson concept operations

    def upsert_concepts(self, document_id: str, concepts: list[dict]):
        """Replace all concepts for a lesson. concepts: [{"category": ..., "value": ...}, ...]"""
        with self.get_cursor() as cursor:
            cursor.execute("DELETE FROM lesson_concept WHERE document_id = %s", (document_id,))
            if concepts:
                from psycopg2.extras import execute_values
                execute_values(
                    cursor,
                    "INSERT INTO lesson_concept (document_id, category, value) VALUES %s",
                    [(document_id, c["category"], c["value"]) for c in concepts],
                )

    def search_by_concepts(self, terms: list[str], limit: int = 10) -> list[dict]:
        """Search lessons by matching concept values. Ranks by number of matching concepts.

        Returns list of {document_id, title, match_count, matched_concepts, source_id, category_name}.
        """
        if not terms:
            return []
        # Build ILIKE conditions for each search term
        conditions = []
        params = []
        for term in terms:
            conditions.append("lc.value ILIKE %s")
            params.append(f"%{term}%")

        where_clause = " OR ".join(conditions)
        public_join = "\nJOIN document_source ds ON ds.id = d.source_id" if self.public_only else ""
        public_filter = " AND ds.public = TRUE" if self.public_only else ""

        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                f"""
                SELECT lc.document_id::text, d.title,
                       COUNT(*) as match_count,
                       ARRAY_AGG(DISTINCT lc.category || ':' || lc.value) as matched_concepts,
                       d.source_id,
                       d.category_name
                FROM lesson_concept lc
                JOIN document d ON d.id = lc.document_id{public_join}
                WHERE ({where_clause}){public_filter}
                GROUP BY lc.document_id, d.title, d.source_id, d.category_name
                ORDER BY COUNT(*) DESC
                LIMIT %s
                """,
                params + [limit],
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_lesson_concepts(self, document_id: str) -> list[dict]:
        """Return concepts for a lesson as [{category, value}, ...]."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT category, value FROM lesson_concept WHERE document_id = %s ORDER BY category, value",
                (document_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_docs_with_condensed(self, cache_suffix: str = ":pass_0b_condensed_v4") -> list[dict]:
        """Find docs that have condensed analysis cached.

        Single SQL query to get all transcripts + titles, compute cache keys in
        Python, then bulk-check against cache keys. Returns list of
        {doc_id, title, cache_key} for docs with condensed data.
        """
        import hashlib

        public_join = "\nJOIN document_source ds ON ds.id = d.source_id" if self.public_only else ""
        public_filter = " AND ds.public = TRUE" if self.public_only else ""

        # One query: get all transcript texts and doc titles
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                SELECT a.document_id::text as doc_id,
                       d.title,
                       a.data->>'text' as text
                FROM analysis a
                JOIN document d ON d.id = a.document_id{public_join}
                WHERE a.type_id = 1 AND a.data->>'text' IS NOT NULL{public_filter}
            """)
            doc_keys = {}
            for row in cursor.fetchall():
                text = (row["text"] or "").strip()
                if text:
                    cache_key = hashlib.sha256(
                        (text + cache_suffix).encode()
                    ).hexdigest()[:16]
                    doc_keys[cache_key] = {
                        "doc_id": row["doc_id"],
                        "title": row["title"],
                    }

        # One query: get all cache keys
        all_keys = self.get_cache_keys()
        condensed_keys = set(doc_keys.keys()) & all_keys

        return [
            {**doc_keys[key], "cache_key": key}
            for key in condensed_keys
        ]
