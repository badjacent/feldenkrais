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

CREATE INDEX IF NOT EXISTS idx_document_source ON document(source_id);
CREATE INDEX IF NOT EXISTS idx_document_type ON document(type_id);
CREATE INDEX IF NOT EXISTS idx_analysis_document ON analysis(document_id);
CREATE INDEX IF NOT EXISTS idx_analysis_type ON analysis(type_id);

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
