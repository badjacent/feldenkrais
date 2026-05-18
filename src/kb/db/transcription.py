"""Transcription job operations."""

from typing import Optional
from uuid import uuid4


class TranscriptionMixin:
    """Transcription job CRUD and retry operations."""

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

    def get_documents_for_sync_fallback(self, min_retries: int, max_retries: int) -> list[dict]:
        """
        Get docs eligible for synchronous transcription fallback.

        These are docs whose async retries have crossed the threshold but haven't
        yet exhausted the overall retry cap, and that don't already have a
        completed transcription.
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT d.id, d.title, d.s3_key,
                       COALESCE(d.transcription_retries, 0) as transcription_retries
                FROM document d
                WHERE d.type_id IN (2, 3)
                AND NOT EXISTS (
                    SELECT 1 FROM analysis a
                    WHERE a.document_id = d.id AND a.type_id = 1
                )
                AND COALESCE(d.transcription_retries, 0) >= %s
                AND COALESCE(d.transcription_retries, 0) < %s
                ORDER BY d.transcription_retries DESC, d.created_at
                """,
                (min_retries, max_retries),
            )
            return [dict(row) for row in cursor.fetchall()]

    def increment_document_retries(self, document_id: str) -> int:
        """Increment transcription_retries on a document. Returns the new value."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE document
                SET transcription_retries = COALESCE(transcription_retries, 0) + 1,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING transcription_retries
                """,
                (document_id,),
            )
            row = cursor.fetchone()
            return row["transcription_retries"] if row else 0

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

    def resubmit_document(self, document_id: str) -> dict:
        """
        Clear all pipeline state for a document so it can be reprocessed.

        Deletes: transcription jobs, transcription analysis, embeddings,
        condensed/pass_0b cache entries, and permanent_failure log entries.
        Resets transcription_retries to 0.
        """
        import hashlib

        cleared = {}

        # Compute cache keys to clear BEFORE deleting the transcript
        cache_keys_to_delete = []
        transcript = self.get_document_transcription(document_id)
        if transcript and transcript.strip():
            text = transcript.strip()
            for suffix in (":somatic_v2", ":language_v1", ":language_patterns_v3",
                           ":concept_tags_v2", ":spatial_v2",
                           ":condense_v4"):
                cache_keys_to_delete.append(
                    hashlib.sha256((text + suffix).encode()).hexdigest()[:16]
                )

        with self.get_cursor() as cursor:
            # Delete transcription jobs
            cursor.execute("DELETE FROM transcription_job WHERE document_id = %s", (document_id,))
            cleared["transcription_jobs"] = cursor.rowcount

            # Delete transcription (analysis type_id=1)
            cursor.execute("DELETE FROM analysis WHERE document_id = %s AND type_id = 1", (document_id,))
            cleared["transcription"] = cursor.rowcount

            # Delete embeddings
            cursor.execute("DELETE FROM lesson_embedding WHERE document_id = %s", (document_id,))
            cleared["embeddings"] = cursor.rowcount

            # Reset retry counter
            cursor.execute(
                "UPDATE document SET transcription_retries = 0, updated_at = NOW() WHERE id = %s",
                (document_id,),
            )

            # Delete permanent_failure log entries for this doc
            cursor.execute(
                "DELETE FROM pipeline_log WHERE document_id = %s AND event = 'permanent_failure'",
                (document_id,),
            )
            cleared["failure_logs"] = cursor.rowcount

            # Delete pass_0b cache entries
            if cache_keys_to_delete:
                cursor.execute(
                    "DELETE FROM cache WHERE key = ANY(%s)",
                    (cache_keys_to_delete,),
                )
                cleared["cache_entries"] = cursor.rowcount

        return cleared
