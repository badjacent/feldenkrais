"""
Tests for database transcription job operations.

Phase 1 refactor target: database.py -> db/transcription.py
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


class TestCreateTranscriptionJob:
    def test_creates_job_with_document_id(self, mock_db):
        with patch("kb.db.transcription.uuid4") as mock_uuid:
            mock_uuid.return_value = "job-uuid-1"
            result = mock_db.create_transcription_job(document_id="doc-123")
        assert result == "job-uuid-1"
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO transcription_job" in sql
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert params[1] == "doc-123"

    def test_creates_chunked_job(self, mock_db):
        """Should set chunk_index, chunk_offset_seconds, total_chunks."""
        with patch("kb.db.transcription.uuid4") as mock_uuid:
            mock_uuid.return_value = "job-uuid-2"
            result = mock_db.create_transcription_job(
                document_id="doc-456",
                batch_id="batch-1",
                chunk_index=2,
                chunk_offset_seconds=120.5,
                total_chunks=5,
            )
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert params[2] == "batch-1"  # batch_id
        assert params[3] == 2         # chunk_index
        assert params[4] == 120.5     # chunk_offset_seconds
        assert params[5] == 5         # total_chunks


class TestUpdateTranscriptionJob:
    def test_update_status_completed(self, mock_db):
        mock_db.update_transcription_job("job-1", status="completed")
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "UPDATE transcription_job SET" in sql
        assert "status = %s" in sql
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert params[0] == "completed"
        assert params[-1] == "job-1"  # WHERE id = %s

    def test_update_status_failed_with_error(self, mock_db):
        """Should set error text and increment attempts."""
        mock_db.update_transcription_job(
            "job-2", status="failed", error="Timeout", increment_attempts=True
        )
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "error = %s" in sql
        assert "attempts = attempts + 1" in sql


class TestResetFailedJobs:
    def test_resets_to_pending(self, mock_db):
        """Should set status='pending' for failed jobs."""
        mock_db._mock_cursor.fetchall.return_value = [
            {"id": "j1", "document_id": "d1", "chunk_index": 0, "attempts": 1}
        ]
        result = mock_db.reset_failed_jobs_for_retry(max_attempts=3)
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "SET status = 'pending'" in sql
        assert "WHERE status = 'failed'" in sql
        assert len(result) == 1

    def test_only_resets_recent_failures(self, mock_db):
        """Should respect max_attempts parameter."""
        mock_db._mock_cursor.fetchall.return_value = []
        mock_db.reset_failed_jobs_for_retry(max_attempts=5)
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert params == (5,)


class TestExpireStalePendingJobs:
    def test_expires_old_pending(self, mock_db):
        """Should mark as failed if pending too long."""
        mock_db._mock_cursor.rowcount = 3
        result = mock_db.expire_stale_pending_jobs(max_age_hours=12)
        assert result == 3
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "SET status = 'failed'" in sql
        assert "WHERE status = 'pending'" in sql


class TestResubmitDocument:
    def test_resubmit_creates_new_job(self, mock_db):
        """resubmit_document should clear all pipeline state."""
        # Mock get_document_transcription to return None (no transcript)
        mock_db._mock_cursor.fetchone.return_value = None
        mock_db._mock_cursor.rowcount = 2
        result = mock_db.resubmit_document("doc-789")
        # Should have called DELETE for transcription_jobs, analysis, embeddings, logs
        calls = [c[0][0] for c in mock_db._mock_cursor.execute.call_args_list]
        # First call is from get_document_transcription
        assert any("DELETE FROM transcription_job" in c for c in calls)
        assert any("DELETE FROM analysis" in c for c in calls)

    def test_resubmit_resets_document_status(self, mock_db):
        """Should reset transcription_retries to 0."""
        mock_db._mock_cursor.fetchone.return_value = None
        mock_db._mock_cursor.rowcount = 0
        mock_db.resubmit_document("doc-789")
        calls = [c[0][0] for c in mock_db._mock_cursor.execute.call_args_list]
        assert any("transcription_retries = 0" in c for c in calls)
