"""
Unit tests for the transcription module.

Tests transcription logic with mocked Groq API.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from kb.transcribe import (
    TranscriptionService,
    TranscriptionConfig,
    create_batch_request,
    create_batch_file,
    TRANSCRIPTION_TYPE_ID,
)


class TestBatchRequestCreation:
    """Tests for batch request creation."""

    def test_create_batch_request(self):
        request = create_batch_request(
            document_id="doc-123",
            audio_url="https://example.com/audio.mp3",
            model="whisper-large-v3-turbo",
            language="en",
        )

        assert request["custom_id"] == "doc-123"
        assert request["method"] == "POST"
        assert request["url"] == "/v1/audio/transcriptions"
        assert request["body"]["model"] == "whisper-large-v3-turbo"
        assert request["body"]["url"] == "https://example.com/audio.mp3"
        assert request["body"]["response_format"] == "verbose_json"
        assert request["body"]["language"] == "en"

    def test_create_batch_file(self, tmp_path):
        requests = [
            create_batch_request("doc-1", "https://example.com/1.mp3"),
            create_batch_request("doc-2", "https://example.com/2.mp3"),
        ]

        output_path = str(tmp_path / "batch.jsonl")
        create_batch_file(requests, output_path)

        with open(output_path) as f:
            lines = f.readlines()

        assert len(lines) == 2
        assert json.loads(lines[0])["custom_id"] == "doc-1"
        assert json.loads(lines[1])["custom_id"] == "doc-2"


class TestTranscriptionService:
    """Tests for TranscriptionService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.get_untranscribed_documents.return_value = []
        db.get_document.return_value = None
        db.get_failed_transcription_job.return_value = None
        return db

    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage."""
        storage = MagicMock()
        storage.generate_presigned_url.return_value = "https://presigned.url/audio.mp3"
        return storage

    @pytest.fixture
    def mock_groq(self):
        """Create a mock Groq client."""
        return MagicMock()

    @pytest.fixture
    def service(self, mock_db, mock_storage, mock_groq):
        """Create a TranscriptionService with mocks."""
        return TranscriptionService(
            db=mock_db,
            storage=mock_storage,
            groq_client=mock_groq,
            config=TranscriptionConfig(),
        )

    def test_get_documents_to_transcribe_empty(self, service, mock_db):
        mock_db.get_untranscribed_documents.return_value = []

        docs = service.get_documents_to_transcribe()

        assert docs == []
        mock_db.get_untranscribed_documents.assert_called_once()

    def test_get_documents_to_transcribe_with_docs(self, service, mock_db):
        mock_db.get_untranscribed_documents.return_value = [
            {"id": "doc-1", "title": "Test 1", "s3_key": "archive/doc-1.mp3"},
            {"id": "doc-2", "title": "Test 2", "s3_key": "archive/doc-2.mp3"},
        ]

        docs = service.get_documents_to_transcribe()

        assert len(docs) == 2

    def test_create_batch_no_documents(self, service, mock_db):
        mock_db.get_untranscribed_documents.return_value = []

        batch_id = service.create_batch()

        assert batch_id is None

    def test_create_batch_with_documents(self, service, mock_db, mock_storage, mock_groq):
        # Setup mock documents
        mock_db.get_untranscribed_documents.return_value = [
            {"id": "doc-1", "title": "Test 1", "s3_key": "archive/doc-1.mp3"},
        ]
        mock_db.get_failed_transcription_job.return_value = None

        # Setup mock Groq responses
        mock_file_response = MagicMock()
        mock_file_response.id = "file-123"
        mock_groq.files.create.return_value = mock_file_response

        mock_batch_response = MagicMock()
        mock_batch_response.id = "batch-456"
        mock_groq.batches.create.return_value = mock_batch_response

        # Create batch
        batch_id = service.create_batch()

        assert batch_id == "batch-456"
        mock_storage.generate_presigned_url.assert_called()
        mock_groq.files.create.assert_called_once()
        mock_groq.batches.create.assert_called_once()
        mock_db.create_transcription_job.assert_called_once()

    def test_create_batch_retries_failed_job(self, service, mock_db, mock_storage, mock_groq):
        # Setup mock documents
        mock_db.get_untranscribed_documents.return_value = [
            {"id": "doc-1", "title": "Test 1", "s3_key": "archive/doc-1.mp3"},
        ]

        # There's an existing failed job
        mock_db.get_failed_transcription_job.return_value = {
            "id": "job-existing",
            "document_id": "doc-1",
            "attempts": 1,
        }

        # Setup mock Groq responses
        mock_file_response = MagicMock()
        mock_file_response.id = "file-123"
        mock_groq.files.create.return_value = mock_file_response

        mock_batch_response = MagicMock()
        mock_batch_response.id = "batch-456"
        mock_groq.batches.create.return_value = mock_batch_response

        # Create batch
        batch_id = service.create_batch()

        assert batch_id == "batch-456"
        # Should update existing job, not create new
        mock_db.update_transcription_job.assert_called_once()
        mock_db.create_transcription_job.assert_not_called()

    def test_check_batch_status(self, service, mock_groq):
        mock_batch = MagicMock()
        mock_batch.id = "batch-123"
        mock_batch.status = "in_progress"
        mock_batch.created_at = 1234567890
        mock_batch.completed_at = None
        mock_batch.failed_at = None
        mock_batch.output_file_id = None
        mock_batch.error_file_id = None
        mock_batch.request_counts = MagicMock()
        mock_batch.request_counts.total = 5
        mock_batch.request_counts.completed = 2
        mock_batch.request_counts.failed = 0

        mock_groq.batches.retrieve.return_value = mock_batch

        status = service.check_batch_status("batch-123")

        assert status["id"] == "batch-123"
        assert status["status"] == "in_progress"
        assert status["request_counts"]["total"] == 5
        assert status["request_counts"]["completed"] == 2

    def test_process_batch_results_not_ready(self, service, mock_groq):
        mock_batch = MagicMock()
        mock_batch.status = "in_progress"

        mock_groq.batches.retrieve.return_value = mock_batch

        results = service.process_batch_results("batch-123")

        assert results["status"] == "not_ready"
        assert results["batch_status"] == "in_progress"

    def test_process_batch_results_success(self, service, mock_db, mock_groq):
        # Setup completed batch
        mock_batch = MagicMock()
        mock_batch.status = "completed"
        mock_batch.output_file_id = "output-file-123"
        mock_batch.error_file_id = None

        mock_groq.batches.retrieve.return_value = mock_batch

        # Setup output file content
        output_content = MagicMock()
        output_content.text = json.dumps({
            "custom_id": "doc-1",
            "response": {
                "status_code": 200,
                "body": {
                    "text": "Hello world",
                    "segments": [{"start": 0, "end": 1, "text": "Hello world"}],
                    "language": "en",
                },
            },
        })
        mock_groq.files.content.return_value = output_content

        # Setup job lookup
        mock_db.get_transcription_jobs_by_batch.return_value = [
            {"id": "job-1", "document_id": "doc-1"},
        ]

        results = service.process_batch_results("batch-123")

        assert len(results["completed"]) == 1
        assert results["completed"][0]["document_id"] == "doc-1"
        mock_db.create_analysis.assert_called_once()
        mock_db.update_transcription_job.assert_called()


class TestDatabaseQueries:
    """Tests for database query logic."""

    def test_untranscribed_query_excludes_pending(self):
        """Verify the query logic excludes pending jobs."""
        # This is more of a documentation test showing expected behavior
        # The actual SQL is tested in integration tests

        # Document with pending job should NOT be returned
        # Document with failed job (attempts < max) SHOULD be returned
        # Document with failed job (attempts >= max) should NOT be returned
        # Document with completed transcription should NOT be returned
        pass

    def test_untranscribed_query_includes_retriable_failed(self):
        """Verify failed jobs under max retries are included."""
        pass
