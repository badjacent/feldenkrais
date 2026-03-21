"""
Tests for database document CRUD operations.

Phase 1 refactor target: database.py → db_documents.py
"""

import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4


@pytest.fixture
def mock_db():
    """Create a Database instance with mocked connection pool."""
    with patch("kb.database.psycopg2") as mock_pg:
        from kb.database import Database

        mock_pool = MagicMock()
        mock_pg.pool.SimpleConnectionPool.return_value = mock_pool
        db = Database.__new__(Database)
        db._pool = mock_pool
        db.public_only = False
        yield db


class TestCreateDocument:
    def test_create_document_returns_uuid(self, mock_db):
        """create_document should return a UUID string."""
        pytest.skip("TODO: mock cursor, verify INSERT SQL and returned id")

    def test_create_document_with_metadata(self, mock_db):
        """Metadata dict should be stored as JSONB."""
        pytest.skip("TODO: verify metadata serialized correctly")

    def test_create_document_with_tags(self, mock_db):
        """Tags list should be stored as JSONB array."""
        pytest.skip("TODO: verify tags serialized correctly")

    def test_create_document_default_status_pending(self, mock_db):
        """Default status should be 'pending'."""
        pytest.skip("TODO: verify default status in INSERT")


class TestGetDocument:
    def test_get_existing_document(self, mock_db):
        """Should return dict with all document fields."""
        pytest.skip("TODO: mock cursor with row, verify returned dict")

    def test_get_nonexistent_document_returns_none(self, mock_db):
        """Should return None for unknown UUID."""
        pytest.skip("TODO: mock cursor with no rows")

    def test_get_document_includes_metadata(self, mock_db):
        """Returned dict should include parsed metadata JSONB."""
        pytest.skip("TODO: verify metadata deserialization")


class TestDocumentExists:
    def test_exists_returns_true(self, mock_db):
        pytest.skip("TODO")

    def test_exists_returns_false(self, mock_db):
        pytest.skip("TODO")


class TestUpdateDocumentStatus:
    def test_status_update(self, mock_db):
        """Should UPDATE status and updated_at."""
        pytest.skip("TODO")

    def test_status_update_nonexistent_doc(self, mock_db):
        """Should handle missing document gracefully."""
        pytest.skip("TODO")


class TestListDocuments:
    def test_list_with_source_filter(self, mock_db):
        """Should filter by source_id when provided."""
        pytest.skip("TODO")

    def test_list_with_status_filter(self, mock_db):
        """Should filter by status when provided."""
        pytest.skip("TODO")

    def test_list_respects_access_control(self, mock_db):
        """Should only return documents from allowed sources."""
        pytest.skip("TODO: verify _source_filter is applied")
