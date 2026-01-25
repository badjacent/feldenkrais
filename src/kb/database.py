"""SQLite database operations for the knowledge base."""

import sqlite3
from pathlib import Path
from typing import Optional
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS document_source (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS document_type (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS document (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_url TEXT,
    s3_key TEXT,
    source_id INTEGER NOT NULL REFERENCES document_source(id),
    type_id INTEGER NOT NULL REFERENCES document_type(id),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tags TEXT,
    metadata JSON
);

CREATE TABLE IF NOT EXISTS analysis_type (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS analysis (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    type_id INTEGER NOT NULL REFERENCES analysis_type(id),
    data JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transcription_job (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    batch_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER DEFAULT 1,
    error TEXT,
    chunk_index INTEGER,
    chunk_offset_seconds REAL,
    total_chunks INTEGER,
    result_data JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_document_source ON document(source_id);
CREATE INDEX IF NOT EXISTS idx_document_type ON document(type_id);
CREATE INDEX IF NOT EXISTS idx_document_status ON document(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_document_unique ON document(title, source_url);
CREATE INDEX IF NOT EXISTS idx_analysis_document ON analysis(document_id);
CREATE INDEX IF NOT EXISTS idx_analysis_type ON analysis(type_id);
CREATE INDEX IF NOT EXISTS idx_transcription_job_document ON transcription_job(document_id);
CREATE INDEX IF NOT EXISTS idx_transcription_job_status ON transcription_job(status);
CREATE INDEX IF NOT EXISTS idx_transcription_job_batch ON transcription_job(batch_id);

CREATE TABLE IF NOT EXISTS youtube_queue (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    video_id TEXT,
    video_title TEXT,
    channel_name TEXT,
    channel_id TEXT,
    duration_seconds INTEGER,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_youtube_queue_status ON youtube_queue(status);

CREATE TABLE IF NOT EXISTS fm_lesson_status (
    lesson_url TEXT PRIMARY KEY,
    lesson_title TEXT,
    youtube_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    video_count INTEGER DEFAULT 1,
    videos_staged INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fm_lesson_status ON fm_lesson_status(status);
CREATE INDEX IF NOT EXISTS idx_fm_lesson_youtube ON fm_lesson_status(youtube_id);

-- Seed default enum values
INSERT OR IGNORE INTO document_source (id, name) VALUES (1, 'OTHER');
INSERT OR IGNORE INTO document_source (id, name) VALUES (2, 'YOUTUBE');
INSERT OR IGNORE INTO document_source (id, name) VALUES (3, 'FELDENKRAIS_MANHATTAN');
INSERT OR IGNORE INTO document_type (id, name) VALUES (1, 'TEXT');
INSERT OR IGNORE INTO document_type (id, name) VALUES (2, 'AUDIO');
INSERT OR IGNORE INTO document_type (id, name) VALUES (3, 'VIDEO');
INSERT OR IGNORE INTO analysis_type (id, name) VALUES (1, 'TRANSCRIPTION');
INSERT OR IGNORE INTO analysis_type (id, name) VALUES (2, 'ANNOTATION');
"""


class Database:
    """SQLite database wrapper for the knowledge base."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        """Connect to the database, creating it if needed."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def initialize(self):
        """Initialize the database schema."""
        conn = self.connect()

        # Run migrations first (for existing databases)
        self._run_migrations(conn)

        # Then create/update schema
        conn.executescript(SCHEMA)
        conn.commit()

    def _run_migrations(self, conn):
        """Apply any needed migrations to existing databases."""
        # Check if fm_lesson_status table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fm_lesson_status'"
        )
        if not cursor.fetchone():
            return  # Table doesn't exist yet, schema will create it

        # Add youtube_id column to fm_lesson_status if missing
        cursor = conn.execute("PRAGMA table_info(fm_lesson_status)")
        columns = {row[1] for row in cursor.fetchall()}

        if 'youtube_id' not in columns:
            conn.execute("ALTER TABLE fm_lesson_status ADD COLUMN youtube_id TEXT")
            conn.commit()

    def create_document(
        self,
        title: str,
        s3_key: str,
        source_id: int,
        type_id: int,
        source_url: Optional[str] = None,
        tags: Optional[str] = None,
        metadata: Optional[str] = None,
    ) -> str:
        """Create a new document record. Returns the document ID."""
        doc_id = str(uuid4())
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO document (id, title, source_url, s3_key, source_id, type_id, tags, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, title, source_url, s3_key, source_id, type_id, tags, metadata),
        )
        conn.commit()
        return doc_id

    def get_document(self, doc_id: str) -> Optional[dict]:
        """Get a document by ID."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT * FROM document WHERE id = ?",
            (doc_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def document_exists(self, title: str, source_url: Optional[str] = None) -> bool:
        """Check if a document with the same title and source_url already exists."""
        conn = self.connect()
        if source_url:
            cursor = conn.execute(
                "SELECT 1 FROM document WHERE title = ? AND source_url = ? LIMIT 1",
                (title, source_url),
            )
        else:
            cursor = conn.execute(
                "SELECT 1 FROM document WHERE title = ? AND source_url IS NULL LIMIT 1",
                (title,),
            )
        return cursor.fetchone() is not None

    def claim_document(self, doc_id: str, from_status: str, to_status: str) -> bool:
        """
        Atomically claim a document by updating its status.

        Returns True if the document was claimed (status was from_status).
        Returns False if another worker already claimed it.
        """
        conn = self.connect()
        cursor = conn.execute(
            """
            UPDATE document
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = ?
            """,
            (to_status, doc_id, from_status),
        )
        conn.commit()
        return cursor.rowcount > 0

    def update_document_status(self, doc_id: str, status: str):
        """Update document status unconditionally."""
        conn = self.connect()
        conn.execute(
            "UPDATE document SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, doc_id),
        )
        conn.commit()

    def get_documents_by_status(self, status: str, limit: int = 100) -> list[dict]:
        """Get documents with a specific status."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT * FROM document WHERE status = ? ORDER BY created_at LIMIT ?",
            (status, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_documents(self) -> list[dict]:
        """List all documents."""
        conn = self.connect()
        cursor = conn.execute("SELECT * FROM document ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def get_untranscribed_documents(self, max_retries: int = 3) -> list[dict]:
        """
        Get audio/video documents that need transcription.

        Returns documents that:
        - Are audio (type_id=2) or video (type_id=3)
        - Have no successful transcription analysis
        - Are not currently pending
        - Have not permanently failed (< max_retries attempts)
        """
        conn = self.connect()
        cursor = conn.execute(
            """
            SELECT d.* FROM document d
            WHERE d.type_id IN (2, 3)
            -- No successful transcription yet
            AND NOT EXISTS (
                SELECT 1 FROM analysis a
                WHERE a.document_id = d.id AND a.type_id = 1
            )
            -- Not currently pending
            AND NOT EXISTS (
                SELECT 1 FROM transcription_job j
                WHERE j.document_id = d.id AND j.status = 'pending'
            )
            -- Not permanently failed (under max retries)
            AND NOT EXISTS (
                SELECT 1 FROM transcription_job j
                WHERE j.document_id = d.id
                AND j.status = 'failed'
                AND j.attempts >= ?
            )
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
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO transcription_job
            (id, document_id, batch_id, status, chunk_index, chunk_offset_seconds, total_chunks)
            VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            (job_id, document_id, batch_id, chunk_index, chunk_offset_seconds, total_chunks),
        )
        conn.commit()
        return job_id

    def get_transcription_job(self, job_id: str) -> Optional[dict]:
        """Get a transcription job by ID."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT * FROM transcription_job WHERE id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_transcription_jobs_by_batch(self, batch_id: str) -> list[dict]:
        """Get all transcription jobs for a batch."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT * FROM transcription_job WHERE batch_id = ? ORDER BY chunk_index",
            (batch_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_pending_batches(self) -> list[str]:
        """Get unique batch IDs that have pending jobs."""
        conn = self.connect()
        cursor = conn.execute(
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
    ):
        """Update a transcription job."""
        conn = self.connect()
        updates = ["updated_at = CURRENT_TIMESTAMP"]
        params = []

        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if batch_id is not None:
            updates.append("batch_id = ?")
            params.append(batch_id)
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        if result_data is not None:
            updates.append("result_data = ?")
            params.append(result_data)
        if increment_attempts:
            updates.append("attempts = attempts + 1")

        params.append(job_id)
        conn.execute(
            f"UPDATE transcription_job SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()

    def get_failed_transcription_job(self, document_id: str) -> Optional[dict]:
        """Get the most recent failed transcription job for a document."""
        conn = self.connect()
        cursor = conn.execute(
            """
            SELECT * FROM transcription_job
            WHERE document_id = ? AND status = 'failed'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (document_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_analysis(
        self,
        document_id: str,
        type_id: int,
        data: str,
    ) -> str:
        """Create a new analysis record. Returns the analysis ID."""
        analysis_id = str(uuid4())
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO analysis (id, document_id, type_id, data)
            VALUES (?, ?, ?, ?)
            """,
            (analysis_id, document_id, type_id, data),
        )
        conn.commit()
        return analysis_id

    def get_analysis(self, document_id: str, type_id: int) -> Optional[dict]:
        """Get an analysis by document ID and type."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT * FROM analysis WHERE document_id = ? AND type_id = ?",
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
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO youtube_queue (id, url) VALUES (?, ?)",
                (queue_id, url),
            )
            conn.commit()
            return queue_id
        except sqlite3.IntegrityError:
            # URL already exists
            return None

    def get_pending_youtube_urls(self, limit: int = 10) -> list[dict]:
        """Get pending YouTube URLs to process."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT * FROM youtube_queue WHERE status = 'pending' ORDER BY created_at LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def claim_youtube_url(self, queue_id: str) -> bool:
        """Claim a YouTube URL for processing. Returns True if claimed."""
        conn = self.connect()
        cursor = conn.execute(
            """
            UPDATE youtube_queue
            SET status = 'processing', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (queue_id,),
        )
        conn.commit()
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
        conn = self.connect()
        updates = ["updated_at = CURRENT_TIMESTAMP"]
        params = []

        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if video_id is not None:
            updates.append("video_id = ?")
            params.append(video_id)
        if video_title is not None:
            updates.append("video_title = ?")
            params.append(video_title)
        if channel_name is not None:
            updates.append("channel_name = ?")
            params.append(channel_name)
        if channel_id is not None:
            updates.append("channel_id = ?")
            params.append(channel_id)
        if duration_seconds is not None:
            updates.append("duration_seconds = ?")
            params.append(duration_seconds)
        if error is not None:
            updates.append("error = ?")
            params.append(error)

        params.append(queue_id)
        conn.execute(
            f"UPDATE youtube_queue SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()

    def get_youtube_queue_stats(self) -> dict:
        """Get statistics about the YouTube queue."""
        conn = self.connect()
        cursor = conn.execute(
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
        conn = self.connect()
        if status:
            cursor = conn.execute(
                "SELECT * FROM youtube_queue WHERE status = ? ORDER BY created_at",
                (status,),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM youtube_queue ORDER BY created_at"
            )
        return [dict(row) for row in cursor.fetchall()]

    # Feldenkrais Manhattan lesson tracking

    def get_fm_lesson_status(self, lesson_url: str) -> Optional[dict]:
        """Get the status of a Feldenkrais Manhattan lesson."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT * FROM fm_lesson_status WHERE lesson_url = ?",
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
        conn = self.connect()
        cursor = conn.execute(
            "SELECT lesson_url FROM fm_lesson_status WHERE status != 'completed'"
        )
        return [row['lesson_url'] for row in cursor.fetchall()]

    def get_completed_fm_lessons(self) -> set[str]:
        """Get set of completed lesson URLs for fast lookup."""
        conn = self.connect()
        cursor = conn.execute(
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
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO fm_lesson_status (lesson_url, lesson_title, youtube_id, video_count, status, videos_staged)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(lesson_url) DO UPDATE SET
                lesson_title = excluded.lesson_title,
                youtube_id = COALESCE(excluded.youtube_id, fm_lesson_status.youtube_id),
                video_count = excluded.video_count,
                status = excluded.status,
                videos_staged = excluded.videos_staged,
                updated_at = CURRENT_TIMESTAMP
            """,
            (lesson_url, lesson_title, youtube_id, video_count, status, videos_staged),
        )
        conn.commit()

    def get_fm_lesson_youtube_ids(self) -> dict[str, str]:
        """Get mapping of lesson_url to youtube_id for all known lessons."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT lesson_url, youtube_id FROM fm_lesson_status WHERE youtube_id IS NOT NULL"
        )
        return {row['lesson_url']: row['youtube_id'] for row in cursor.fetchall()}

    def get_all_fm_lessons(self) -> list[dict]:
        """Get all FM lessons with their cached data."""
        conn = self.connect()
        cursor = conn.execute("SELECT * FROM fm_lesson_status")
        return [dict(row) for row in cursor.fetchall()]

    def mark_fm_lesson_complete(self, lesson_url: str):
        """Mark a lesson as fully processed."""
        conn = self.connect()
        conn.execute(
            """
            UPDATE fm_lesson_status
            SET status = 'completed', updated_at = CURRENT_TIMESTAMP
            WHERE lesson_url = ?
            """,
            (lesson_url,),
        )
        conn.commit()
