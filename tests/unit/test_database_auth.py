"""
Tests for database user/auth/access operations.

Phase 1 refactor target: database.py -> db/auth.py
"""

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


class TestGetOrCreateUser:
    def test_existing_user_returned(self, mock_db):
        """Should return existing user without creating."""
        mock_db._mock_cursor.fetchone.return_value = {
            "id": 1, "provider": "github", "provider_id": "123",
            "login": "testuser", "name": "Test", "email": "t@t.com",
            "avatar_url": None, "tier": "user", "superuser_eligible": False,
            "created_at": None, "last_login_at": None,
        }
        result = mock_db.upsert_user("github", "123", "testuser")
        assert result["id"] == 1
        assert result["login"] == "testuser"

    def test_new_user_created(self, mock_db):
        """Should INSERT and return new user."""
        mock_db._mock_cursor.fetchone.return_value = {
            "id": 42, "provider": "github", "provider_id": "456",
            "login": "newuser", "name": "New", "email": None,
            "avatar_url": None, "tier": "user", "superuser_eligible": False,
            "created_at": None, "last_login_at": None,
        }
        result = mock_db.upsert_user("github", "456", "newuser", name="New")
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO users" in sql
        assert "ON CONFLICT (provider, login) DO UPDATE" in sql
        assert result["id"] == 42

    def test_updates_last_login(self, mock_db):
        """Should update last_login_at for existing user."""
        mock_db._mock_cursor.fetchone.return_value = {
            "id": 1, "provider": "github", "provider_id": "123",
            "login": "testuser", "name": "Test", "email": None,
            "avatar_url": None, "tier": "user", "superuser_eligible": False,
            "created_at": None, "last_login_at": None,
        }
        mock_db.upsert_user("github", "123", "testuser")
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "last_login_at = NOW()" in sql


class TestGetUserAllowedSources:
    def test_anonymous_gets_public_sources(self, mock_db):
        """user_id=None should return only public+visible sources."""
        mock_db._mock_cursor.fetchall.return_value = [{"id": 1}, {"id": 4}]
        result = mock_db.get_user_allowed_sources(None)
        assert result == {1, 4}
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "public = TRUE" in sql
        assert "visible = TRUE" in sql

    def test_logged_in_gets_public_plus_group(self, mock_db):
        """Should return public sources UNION group-granted sources."""
        # First call: public sources; Second call: group sources
        mock_db._mock_cursor.fetchall.side_effect = [
            [{"id": 1}],       # public sources
            [{"source_id": 3}], # group-granted sources
        ]
        result = mock_db.get_user_allowed_sources(user_id=5)
        assert result == {1, 3}

    def test_source_filter_sql(self, mock_db):
        """_source_filter should produce valid WHERE clause when filtering."""
        mock_db.allowed_source_ids = {1, 2}
        join_clause, where_clause, params = mock_db._source_filter()
        assert "JOIN document_source ds" in join_clause
        assert "ds.id = ANY(%s)" in where_clause
        assert params == [[1, 2]] or set(params[0]) == {1, 2}


class TestIsDocumentAccessible:
    def test_accessible_document(self, mock_db):
        mock_db.allowed_source_ids = {1, 2}
        mock_db._mock_cursor.fetchone.return_value = {"source_id": 1}
        assert mock_db.is_document_accessible("doc-123") is True

    def test_inaccessible_document(self, mock_db):
        mock_db.allowed_source_ids = {1, 2}
        mock_db._mock_cursor.fetchone.return_value = {"source_id": 3}
        assert mock_db.is_document_accessible("doc-456") is False


class TestGroupManagement:
    def test_create_access_group(self, mock_db):
        mock_db._mock_cursor.fetchone.return_value = {
            "id": 10, "name": "beta-testers", "created_at": None
        }
        result = mock_db.create_access_group("beta-testers")
        assert result["id"] == 10
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO access_group" in sql

    def test_add_user_to_group(self, mock_db):
        mock_db.add_user_to_group(user_id=1, group_id=10)
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO user_group" in sql
        assert "ON CONFLICT DO NOTHING" in sql

    def test_add_source_to_group(self, mock_db):
        mock_db.add_source_to_group(source_id=3, group_id=10)
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO group_source_access" in sql
        assert "ON CONFLICT DO NOTHING" in sql

    def test_duplicate_membership_no_error(self, mock_db):
        """ON CONFLICT DO NOTHING should prevent errors."""
        # Should not raise even if called twice
        mock_db.add_user_to_group(user_id=1, group_id=10)
        mock_db.add_user_to_group(user_id=1, group_id=10)
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "ON CONFLICT DO NOTHING" in sql
