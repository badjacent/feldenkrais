"""PostgreSQL advisory lock operations."""

from psycopg2.extras import RealDictCursor


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
        held = getattr(self, "_legacy_lock_connections", {})
        if lock_id in held:
            return True
        conn = self._get_pool().getconn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
            result = cursor.fetchone()
            cursor.close()
            acquired = bool(result and result["pg_try_advisory_lock"])
            if acquired:
                if not hasattr(self, "_legacy_lock_connections"):
                    self._legacy_lock_connections = {}
                self._legacy_lock_connections[lock_id] = conn
                return True
            conn.rollback()
            self._get_pool().putconn(conn)
            return False
        except Exception:
            conn.rollback()
            self._get_pool().putconn(conn)
            raise

    def release_lock(self, lock_id: int) -> bool:
        """Release a PostgreSQL advisory lock."""
        conn = getattr(self, "_legacy_lock_connections", {}).pop(lock_id, None)
        if conn is None:
            return False
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
            result = cursor.fetchone()
            cursor.close()
            return bool(result and result["pg_advisory_unlock"])
        finally:
            conn.rollback()
            self._get_pool().putconn(conn)
