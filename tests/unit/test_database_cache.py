"""
Tests for database cache operations.

Phase 1 refactor target: database.py → db_cache.py
"""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_db():
    with patch("kb.database.psycopg2") as mock_pg:
        from kb.database import Database

        mock_pool = MagicMock()
        mock_pg.pool.SimpleConnectionPool.return_value = mock_pool
        db = Database.__new__(Database)
        db._pool = mock_pool
        db.public_only = False
        yield db


class TestGetCache:
    def test_get_existing_key(self, mock_db):
        """Should return deserialized JSON value."""
        pytest.skip("TODO")

    def test_get_missing_key_returns_none(self, mock_db):
        pytest.skip("TODO")


class TestSetCache:
    def test_set_new_key(self, mock_db):
        """Should INSERT with ON CONFLICT UPDATE."""
        pytest.skip("TODO")

    def test_set_overwrites_existing(self, mock_db):
        """Should update value and updated_at for existing key."""
        pytest.skip("TODO")

    def test_set_requires_dict(self, mock_db):
        """Lists must be wrapped in a dict — verify this constraint."""
        pytest.skip("TODO")


class TestClearCache:
    def test_clear_by_key(self, mock_db):
        pytest.skip("TODO")

    def test_clear_by_prefix(self, mock_db):
        """Should DELETE WHERE key LIKE prefix%."""
        pytest.skip("TODO")


class TestListCache:
    def test_list_all_keys(self, mock_db):
        pytest.skip("TODO")

    def test_list_keys_by_prefix(self, mock_db):
        pytest.skip("TODO")
