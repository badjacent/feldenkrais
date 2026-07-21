"""Feldenkrais Manhattan lesson tracking."""

from typing import Optional


class FMLessonsMixin:
    """FM lesson status tracking and YouTube ID mapping."""

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

    def get_fm_no_video_urls(self) -> set[str]:
        """Get lesson URLs previously scraped and found to have no video.

        These pages (photo rosters, whiteboards, never-posted chapters) are
        permanently skipped by the loader; clear their status='no_video' row
        to re-check one.
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT lesson_url FROM fm_lesson_status WHERE status = 'no_video'"
            )
            return {row['lesson_url'] for row in cursor.fetchall()}

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
