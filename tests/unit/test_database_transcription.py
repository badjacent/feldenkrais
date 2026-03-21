"""
Tests for database transcription job operations.

Phase 1 refactor target: database.py → db_transcription.py
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


class TestCreateTranscriptionJob:
    def test_creates_job_with_document_id(self, mock_db):
        pytest.skip("TODO")

    def test_creates_chunked_job(self, mock_db):
        """Should set chunk_index, chunk_offset_seconds, total_chunks."""
        pytest.skip("TODO")


class TestUpdateTranscriptionJob:
    def test_update_status_completed(self, mock_db):
        pytest.skip("TODO")

    def test_update_status_failed_with_error(self, mock_db):
        """Should set error text and increment attempts."""
        pytest.skip("TODO")


class TestResetFailedJobs:
    def test_resets_to_pending(self, mock_db):
        """Should set status='pending' for failed jobs."""
        pytest.skip("TODO")

    def test_only_resets_recent_failures(self, mock_db):
        """Should respect time window parameter."""
        pytest.skip("TODO")


class TestExpireStalePendingJobs:
    def test_expires_old_pending(self, mock_db):
        """Should mark as failed if pending too long."""
        pytest.skip("TODO")


class TestResubmitDocument:
    def test_resubmit_creates_new_job(self, mock_db):
        pytest.skip("TODO")

    def test_resubmit_resets_document_status(self, mock_db):
        pytest.skip("TODO")
