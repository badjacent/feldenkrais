"""Pipeline logging and document pipeline view."""

import json


class LoggingMixin:
    """Pipeline event logging, summaries, and document pipeline views."""

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
        filtering = self.allowed_source_ids is not None
        if filtering:
            ids = list(self.allowed_source_ids)
        with self.get_cursor(commit=False) as cursor:
            if filtering:
                cursor.execute("""
                    SELECT d.status, COUNT(*) as count
                    FROM document d
                    JOIN document_source ds ON ds.id = d.source_id
                    WHERE ds.id = ANY(%s)
                    GROUP BY d.status
                """, (ids,))
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
            if filtering:
                cursor.execute("""
                    SELECT COUNT(*) as count FROM document d
                    JOIN document_source ds ON ds.id = d.source_id
                    WHERE ds.id = ANY(%s)
                """, (ids,))
            else:
                cursor.execute("SELECT COUNT(*) as count FROM document")
            doc_total = cursor.fetchone()["count"]

            # Total with transcriptions
            if filtering:
                cursor.execute("""
                    SELECT COUNT(DISTINCT a.document_id) as count
                    FROM analysis a
                    JOIN document d ON d.id = a.document_id
                    JOIN document_source ds ON ds.id = d.source_id
                    WHERE a.type_id = 1 AND ds.id = ANY(%s)
                """, (ids,))
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
        if self.allowed_source_ids is None:
            source_filter = ""
            params: list = [limit]
        else:
            source_filter = "\nWHERE ds.id = ANY(%s)"
            params = [list(self.allowed_source_ids), limit]
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
                       COALESCE(d.transcription_retries, 0) as retry_count
                FROM document d
                JOIN document_source ds ON ds.id = d.source_id{source_filter}
                ORDER BY d.updated_at DESC
                LIMIT %s
            """, params)
            rows = [dict(row) for row in cursor.fetchall()]

        # Compute pipeline_stage for each document (ingestion only: ends at transcribed)
        for row in rows:
            has_tx = row.get("has_transcription", False)
            latest_job = row.get("latest_job_status", "")
            retry_count = row.get("retry_count", 0)

            if has_tx:
                stage = "transcribed"
            elif latest_job == "pending":
                stage = "transcribing"
            elif latest_job == "failed" or retry_count >= 5:
                stage = "failed"
            elif row.get("job_count", 0) > 0:
                stage = "transcribing"
            else:
                stage = "awaiting_transcription"

            row["pipeline_stage"] = stage

        return rows
