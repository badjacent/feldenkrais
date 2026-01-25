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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tags TEXT
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
CREATE INDEX IF NOT EXISTS idx_analysis_document ON analysis(document_id);
CREATE INDEX IF NOT EXISTS idx_analysis_type ON analysis(type_id);
CREATE INDEX IF NOT EXISTS idx_transcription_job_document ON transcription_job(document_id);
CREATE INDEX IF NOT EXISTS idx_transcription_job_status ON transcription_job(status);
CREATE INDEX IF NOT EXISTS idx_transcription_job_batch ON transcription_job(batch_id);

-- Seed default enum values
INSERT OR IGNORE INTO document_source (id, name) VALUES (1, 'OTHER');
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
        conn.executescript(SCHEMA)
        conn.commit()

    def create_document(
        self,
        title: str,
        s3_key: str,
        source_id: int,
        type_id: int,
        source_url: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> str:
        """Create a new document record. Returns the document ID."""
        doc_id = str(uuid4())
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO document (id, title, source_url, s3_key, source_id, type_id, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, title, source_url, s3_key, source_id, type_id, tags),
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
