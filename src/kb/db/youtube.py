"""YouTube queue operations."""

from typing import Optional
from uuid import uuid4

import psycopg2


class YouTubeMixin:
    """YouTube URL queue management."""

    def add_youtube_url(self, url: str, origin: Optional[str] = None) -> Optional[str]:
        """
        Add a YouTube URL to the queue.
        Returns the queue ID, or None if URL already exists.
        """
        queue_id = str(uuid4())
        try:
            with self.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO youtube_queue (id, url, origin) VALUES (%s, %s, %s)",
                    (queue_id, url, origin),
                )
            return queue_id
        except psycopg2.errors.UniqueViolation:
            # URL already exists
            return None

    def get_pending_youtube_urls(self, limit: int = 10) -> list[dict]:
        """Get pending YouTube URLs to process."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """SELECT * FROM youtube_queue
                   WHERE status = 'pending'
                     AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                   ORDER BY created_at LIMIT %s""",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def claim_youtube_url(self, queue_id: str) -> bool:
        """Claim a YouTube URL for processing. Returns True if claimed."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE youtube_queue
                SET status = 'processing', attempts = attempts + 1,
                    next_attempt_at = NULL, updated_at = NOW()
                WHERE id = %s AND status = 'pending'
                """,
                (queue_id,),
            )
            return cursor.rowcount > 0

    def requeue_retryable_youtube_urls(self, max_attempts: int = 3) -> int:
        """Release failed URLs whose bounded retry delay has elapsed."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """UPDATE youtube_queue
                   SET status = 'pending', updated_at = NOW()
                   WHERE status = 'failed'
                     AND attempts < %s
                     AND COALESCE(next_attempt_at, updated_at) <= NOW()""",
                (max_attempts,),
            )
            return cursor.rowcount

    def fail_youtube_url(
        self,
        queue_id: str,
        error: str,
        *,
        retry_delay_minutes: int = 60,
    ) -> None:
        """Persist failure details and the earliest bounded retry time."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """UPDATE youtube_queue
                   SET status = 'failed', error = %s,
                       next_attempt_at = NOW() + (%s * INTERVAL '1 minute'),
                       updated_at = NOW()
                   WHERE id = %s""",
                (error[:500], retry_delay_minutes, queue_id),
            )

    def complete_youtube_url(self, queue_id: str) -> None:
        """Mark an item complete and clear diagnostics from earlier attempts."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """UPDATE youtube_queue
                   SET status = 'completed', error = NULL,
                       next_attempt_at = NULL, updated_at = NOW()
                   WHERE id = %s""",
                (queue_id,),
            )

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
