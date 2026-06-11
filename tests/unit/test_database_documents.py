"""
Tests for database document CRUD operations.

Phase 1 refactor target: database.py -> db/documents.py
"""

import pytest
from unittest.mock import patch, MagicMock, call
from uuid import uuid4


@pytest.fixture
def mock_db():
    """Create a Database instance with mocked connection pool."""
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


class TestCreateDocument:
    def test_create_document_returns_uuid(self, mock_db):
        """create_document should return a UUID string."""
        with patch("kb.db.documents.uuid4") as mock_uuid:
            mock_uuid.return_value = "test-uuid-1234"
            result = mock_db.create_document(
                title="Test Lesson",
                s3_key="lessons/test.mp3",
                source_id=1,
                type_id=2,
            )
        assert result == "test-uuid-1234"
        # Verify INSERT was called
        call_args = mock_db._mock_cursor.execute.call_args
        assert "INSERT INTO document" in call_args[0][0]

    def test_create_document_with_metadata(self, mock_db):
        """Metadata dict should be stored as JSONB."""
        with patch("kb.db.documents.uuid4") as mock_uuid:
            mock_uuid.return_value = "test-uuid"
            mock_db.create_document(
                title="Test", s3_key="test.mp3", source_id=1, type_id=2,
                metadata='{"key": "value"}'
            )
        call_args = mock_db._mock_cursor.execute.call_args
        params = call_args[0][1]
        # metadata is the 8th param (index 7)
        assert params[7] == '{"key": "value"}'
        assert "%s::jsonb" in call_args[0][0]

    def test_create_document_with_tags(self, mock_db):
        """Tags list should be stored as JSONB array."""
        with patch("kb.db.documents.uuid4") as mock_uuid:
            mock_uuid.return_value = "test-uuid"
            mock_db.create_document(
                title="Test", s3_key="test.mp3", source_id=1, type_id=2,
                tags='["tag1", "tag2"]'
            )
        call_args = mock_db._mock_cursor.execute.call_args
        params = call_args[0][1]
        # tags is the 7th param (index 6)
        assert params[6] == '["tag1", "tag2"]'

    def test_create_document_default_status_pending(self, mock_db):
        """Default status should be 'pending' (handled by DB default, not in INSERT)."""
        with patch("kb.db.documents.uuid4") as mock_uuid:
            mock_uuid.return_value = "test-uuid"
            mock_db.create_document(
                title="Test", s3_key="test.mp3", source_id=1, type_id=2,
            )
        # The INSERT does not specify status — the DB default 'pending' applies
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "status" not in sql.lower().split("values")[0].split("(")[1]

    def test_create_document_with_structured_columns(self, mock_db):
        """origin/presenter/category_name should be inserted into their columns."""
        with patch("kb.db.documents.uuid4") as mock_uuid:
            mock_uuid.return_value = "test-uuid"
            mock_db.create_document(
                title="Test", s3_key="test.mp3", source_id=3, type_id=2,
                origin="Amherst 1981",
                presenter="Sheryl Field",
                category_name="Segment 13: June 5-June 8",
            )
        call_args = mock_db._mock_cursor.execute.call_args
        sql, params = call_args[0][0], call_args[0][1]
        for col in ("origin", "presenter", "category_name"):
            assert col in sql
        # appended after lesson_index → last three params, in declared order
        assert params[-3:] == ("Amherst 1981", "Sheryl Field", "Segment 13: June 5-June 8")

    def test_create_document_structured_columns_default_none(self, mock_db):
        """Omitting the new columns passes NULL (no regression for other sources)."""
        with patch("kb.db.documents.uuid4") as mock_uuid:
            mock_uuid.return_value = "test-uuid"
            mock_db.create_document(
                title="Test", s3_key="test.mp3", source_id=1, type_id=2,
            )
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert params[-3:] == (None, None, None)


class TestGetDocument:
    def test_get_existing_document(self, mock_db):
        """Should return dict with all document fields."""
        mock_db._mock_cursor.fetchone.return_value = {
            "id": "abc-123", "title": "Test", "status": "pending"
        }
        result = mock_db.get_document("abc-123")
        assert result == {"id": "abc-123", "title": "Test", "status": "pending"}
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "SELECT * FROM document WHERE id = %s" in sql

    def test_get_nonexistent_document_returns_none(self, mock_db):
        """Should return None for unknown UUID."""
        mock_db._mock_cursor.fetchone.return_value = None
        result = mock_db.get_document("nonexistent-id")
        assert result is None

    def test_get_document_includes_metadata(self, mock_db):
        """Returned dict should include parsed metadata JSONB."""
        mock_db._mock_cursor.fetchone.return_value = {
            "id": "abc", "title": "Test", "metadata": {"key": "val"}
        }
        result = mock_db.get_document("abc")
        assert result["metadata"] == {"key": "val"}


class TestDocumentExists:
    def test_exists_returns_true(self, mock_db):
        mock_db._mock_cursor.fetchone.return_value = {"?column?": 1}
        assert mock_db.document_exists("Test Lesson") is True

    def test_exists_returns_false(self, mock_db):
        mock_db._mock_cursor.fetchone.return_value = None
        assert mock_db.document_exists("Nonexistent") is False


class TestUpdateDocumentStatus:
    def test_status_update(self, mock_db):
        """Should UPDATE status and updated_at."""
        mock_db.update_document_status("doc-123", "completed")
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert "UPDATE document SET status" in sql
        assert "updated_at = NOW()" in sql
        assert params == ("completed", "doc-123")

    def test_status_update_nonexistent_doc(self, mock_db):
        """Should handle missing document gracefully (no error, just 0 rows affected)."""
        mock_db._mock_cursor.rowcount = 0
        # Should not raise
        mock_db.update_document_status("nonexistent", "completed")


class TestListDocuments:
    def test_list_with_source_filter(self, mock_db):
        """list_documents returns all documents ordered by created_at DESC."""
        mock_db._mock_cursor.fetchall.return_value = [
            {"id": "1", "title": "A"}, {"id": "2", "title": "B"}
        ]
        result = mock_db.list_documents()
        assert len(result) == 2
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "ORDER BY created_at DESC" in sql

    def test_list_with_status_filter(self, mock_db):
        """get_documents_by_status should filter by status."""
        mock_db._mock_cursor.fetchall.return_value = [{"id": "1", "title": "A"}]
        result = mock_db.get_documents_by_status("pending")
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "WHERE status = %s" in sql

    def test_list_respects_access_control(self, mock_db):
        """get_lesson_categories should filter by allowed_source_ids when set."""
        mock_db.allowed_source_ids = {1, 2}
        mock_db._mock_cursor.fetchall.return_value = []
        mock_db.get_lesson_categories()
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "ds.id = ANY(%s)" in sql
