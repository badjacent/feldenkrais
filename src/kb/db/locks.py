"""PostgreSQL advisory lock operations."""


class LocksMixin:
    """Advisory locks for Lambda concurrency control."""

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
