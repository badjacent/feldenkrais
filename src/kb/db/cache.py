"""Cache operations."""

import json


class CacheMixin:
    """Key-value cache backed by PostgreSQL."""

    def get_cache(self, key: str) -> dict | None:
        """Load a cached value by key."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT data FROM cache WHERE key = %s", (key,))
            row = cursor.fetchone()
            return dict(row["data"]) if row else None

    def set_cache(self, key: str, data: dict) -> None:
        """Store a value in the cache (upsert)."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cache (key, data, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (key) DO UPDATE
                SET data = EXCLUDED.data, updated_at = NOW()
                """,
                (key, json.dumps(data)),
            )

    def clear_cache(self, prefix: str | None = None) -> int:
        """Delete cache entries. If prefix given, only delete matching keys."""
        with self.get_cursor() as cursor:
            if prefix:
                cursor.execute(
                    "DELETE FROM cache WHERE key LIKE %s",
                    (prefix + "%",),
                )
            else:
                cursor.execute("DELETE FROM cache")
            return cursor.rowcount

    def list_cache(self) -> list[dict]:
        """List all cache entries with key and data size."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT key, length(data::text) as size_bytes, created_at, updated_at
                FROM cache ORDER BY key
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_cache_keys(self) -> set[str]:
        """Return the set of all cache keys (fast, no data loaded)."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT key FROM cache")
            return {row["key"] for row in cursor.fetchall()}
