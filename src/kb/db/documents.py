"""Document CRUD operations."""

from typing import Optional
from uuid import uuid4


class DocumentsMixin:
    """Document create, read, update, list operations."""

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
