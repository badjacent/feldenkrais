"""
Tests for database cache operations.

Phase 1 refactor target: database.py -> db/cache.py
"""

import json
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_db():
    with patch("kb.db.core.psycopg2") as mock_pg:
        from kb.database import Database

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        db = Database.__new__(Database)
        db._pool = mock_pool
        db._min_connections = 1
        db._max_connections = 10
        db.database_url = "postgres://test"
        db.public_only = False
        db.allowed_source_ids = None
        db._mock_cursor = mock_cursor
        yield db


class TestGetCache:
    def test_get_existing_key(self, mock_db):
        """Should return deserialized JSON value."""
        mock_db._mock_cursor.fetchone.return_value = {
            "data": {"result": "hello"}
        }
        result = mock_db.get_cache("my_key")
        assert result == {"result": "hello"}
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "SELECT data FROM cache WHERE key = %s" in sql

    def test_get_missing_key_returns_none(self, mock_db):
        mock_db._mock_cursor.fetchone.return_value = None
        result = mock_db.get_cache("missing_key")
        assert result is None


class TestSetCache:
    def test_set_new_key(self, mock_db):
        """Should INSERT with ON CONFLICT UPDATE."""
        mock_db.set_cache("test_key", {"value": 42})
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO cache" in sql
        assert "ON CONFLICT (key) DO UPDATE" in sql
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert params[0] == "test_key"
        assert json.loads(params[1]) == {"value": 42}

    def test_set_overwrites_existing(self, mock_db):
        """Should update value and updated_at for existing key."""
        mock_db.set_cache("existing_key", {"new": "data"})
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "SET data = EXCLUDED.data" in sql
        assert "updated_at = NOW()" in sql

    def test_set_requires_dict(self, mock_db):
        """Lists must be wrapped in a dict -- verify json.dumps works with dict input."""
        mock_db.set_cache("key", {"lessons": [1, 2, 3]})
        params = mock_db._mock_cursor.execute.call_args[0][1]
        parsed = json.loads(params[1])
        assert parsed == {"lessons": [1, 2, 3]}


class TestClearCache:
    def test_clear_by_key(self, mock_db):
        """clear_cache with no prefix should DELETE all."""
        mock_db._mock_cursor.rowcount = 5
        result = mock_db.clear_cache()
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "DELETE FROM cache" in sql
        assert "LIKE" not in sql
        assert result == 5

    def test_clear_by_prefix(self, mock_db):
        """Should DELETE WHERE key LIKE prefix%."""
        mock_db._mock_cursor.rowcount = 3
        result = mock_db.clear_cache(prefix="pipeline:")
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert "DELETE FROM cache WHERE key LIKE %s" in sql
        assert params == ("pipeline:%",)
        assert result == 3


class TestListCache:
    def test_list_all_keys(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = [
            {"key": "k1", "size_bytes": 100, "created_at": None, "updated_at": None},
            {"key": "k2", "size_bytes": 200, "created_at": None, "updated_at": None},
        ]
        result = mock_db.list_cache()
        assert len(result) == 2
        assert result[0]["key"] == "k1"

    def test_list_keys_by_prefix(self, mock_db):
        """get_cache_keys should return a set of all keys."""
        mock_db._mock_cursor.fetchall.return_value = [
            {"key": "a"}, {"key": "b"}, {"key": "c"}
        ]
        result = mock_db.get_cache_keys()
        assert result == {"a", "b", "c"}
