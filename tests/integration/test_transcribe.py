"""
Integration tests for transcription.

Tests with real S3 but mocked Groq API by default.
Use --run-real-groq flag to test with actual Groq API.
"""

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest

from kb.database import Database
from kb.storage import Storage
from kb.transcribe import TranscriptionService, TranscriptionConfig

TERRAFORM_DIR = Path(__file__).parent.parent.parent / "terraform"
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
TEST_ENV = "test"
TEST_GRACE_PERIOD = 0


@pytest.fixture(scope="module")
def aws_infrastructure():
    """Set up and tear down AWS infrastructure for tests."""
    subprocess.run(
        ["terraform", "init"],
        cwd=TERRAFORM_DIR,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "terraform", "apply", "-auto-approve",
            f"-var=environment={TEST_ENV}",
            f"-var=grace_period_seconds={TEST_GRACE_PERIOD}",
        ],
        cwd=TERRAFORM_DIR,
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=TERRAFORM_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    outputs = json.loads(result.stdout)

    yield {
        "bucket_name": outputs["bucket_name"]["value"],
    }

    # Teardown
    bucket_name = outputs["bucket_name"]["value"]
    s3 = boto3.resource("s3")
    bucket = s3.Bucket(bucket_name)
    bucket.objects.all().delete()
    bucket.object_versions.all().delete()

    subprocess.run(
        [
            "terraform", "destroy", "-auto-approve",
            f"-var=environment={TEST_ENV}",
            f"-var=grace_period_seconds={TEST_GRACE_PERIOD}",
        ],
        cwd=TERRAFORM_DIR,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def db(aws_infrastructure, tmp_path):
    """Create a test database."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.initialize()
    yield db
    db.close()


@pytest.fixture
def storage(aws_infrastructure):
    """Create storage instance."""
    return Storage(aws_infrastructure["bucket_name"])


@pytest.fixture
def test_audio_content():
    """Load test audio file content."""
    audio_path = FIXTURES_DIR / "test_audio.wav"
    if not audio_path.exists():
        pytest.skip("Test audio file not found. Run: python tests/fixtures/create_test_audio.py")
    return audio_path.read_bytes()


class TestTranscriptionIntegration:
    """Integration tests with mocked Groq but real S3."""

    def test_presigned_url_generation(self, storage, test_audio_content):
        """Test that we can generate presigned URLs for audio files."""
        # Upload test audio
        s3_key = "archive/test-presigned.wav"
        storage.put_object(s3_key, test_audio_content, content_type="audio/wav")

        # Generate presigned URL
        url = storage.generate_presigned_url(s3_key, expiry_seconds=3600)

        assert url is not None
        assert "test-presigned.wav" in url
        assert "X-Amz-Signature" in url

    def test_untranscribed_documents_query(self, db, storage, test_audio_content):
        """Test querying for documents needing transcription."""
        # Create an audio document
        doc_id = db.create_document(
            title="Test Audio",
            s3_key="archive/test-query.wav",
            source_id=1,
            type_id=2,  # AUDIO
        )

        # Should appear in untranscribed list
        docs = db.get_untranscribed_documents()
        assert any(d["id"] == doc_id for d in docs)

        # Create a text document (should not appear)
        text_doc_id = db.create_document(
            title="Test Text",
            s3_key="archive/test.txt",
            source_id=1,
            type_id=1,  # TEXT
        )

        docs = db.get_untranscribed_documents()
        assert not any(d["id"] == text_doc_id for d in docs)

    def test_transcription_job_workflow(self, db):
        """Test transcription job state transitions."""
        # Create document
        doc_id = db.create_document(
            title="Test Audio",
            s3_key="archive/workflow-test.wav",
            source_id=1,
            type_id=2,
        )

        # Create job
        job_id = db.create_transcription_job(
            document_id=doc_id,
            batch_id="batch-123",
        )

        # Check job status
        job = db.get_transcription_job(job_id)
        assert job["status"] == "pending"
        assert job["attempts"] == 1

        # Document should not appear in untranscribed (has pending job)
        docs = db.get_untranscribed_documents()
        assert not any(d["id"] == doc_id for d in docs)

        # Mark as failed
        db.update_transcription_job(job_id, status="failed", error="Test error")

        job = db.get_transcription_job(job_id)
        assert job["status"] == "failed"
        assert job["error"] == "Test error"

        # Document should appear again (failed, under max retries)
        docs = db.get_untranscribed_documents(max_retries=3)
        assert any(d["id"] == doc_id for d in docs)

        # Increment attempts past max
        db.update_transcription_job(job_id, increment_attempts=True)  # 2
        db.update_transcription_job(job_id, increment_attempts=True)  # 3

        job = db.get_transcription_job(job_id)
        assert job["attempts"] == 3

        # Document should not appear (max retries reached)
        docs = db.get_untranscribed_documents(max_retries=3)
        assert not any(d["id"] == doc_id for d in docs)

    def test_create_batch_with_mocked_groq(self, db, storage, test_audio_content):
        """Test batch creation with mocked Groq API."""
        # Upload test audio
        s3_key = "archive/test-batch.wav"
        storage.put_object(s3_key, test_audio_content, content_type="audio/wav")

        # Create document
        doc_id = db.create_document(
            title="Test Batch Audio",
            s3_key=s3_key,
            source_id=1,
            type_id=2,
        )

        # Create mocked Groq client
        mock_groq = MagicMock()

        mock_file_response = MagicMock()
        mock_file_response.id = "file-test-123"
        mock_groq.files.create.return_value = mock_file_response

        mock_batch_response = MagicMock()
        mock_batch_response.id = "batch-test-456"
        mock_groq.batches.create.return_value = mock_batch_response

        # Create service with mocked Groq
        service = TranscriptionService(
            db=db,
            storage=storage,
            groq_client=mock_groq,
        )

        # Create batch
        batch_id = service.create_batch()

        assert batch_id == "batch-test-456"

        # Verify Groq was called correctly
        mock_groq.files.create.assert_called_once()
        mock_groq.batches.create.assert_called_once()

        # Verify job was created
        jobs = db.get_transcription_jobs_by_batch(batch_id)
        assert len(jobs) == 1
        assert jobs[0]["document_id"] == doc_id
        assert jobs[0]["status"] == "pending"

    def test_process_results_creates_analysis(self, db, storage, test_audio_content):
        """Test that processing results creates analysis records."""
        # Setup: create document and job
        s3_key = "archive/test-results.wav"
        storage.put_object(s3_key, test_audio_content)

        doc_id = db.create_document(
            title="Test Results Audio",
            s3_key=s3_key,
            source_id=1,
            type_id=2,
        )

        batch_id = "batch-results-test"
        job_id = db.create_transcription_job(
            document_id=doc_id,
            batch_id=batch_id,
        )

        # Create mocked Groq client with completed batch
        mock_groq = MagicMock()

        mock_batch = MagicMock()
        mock_batch.status = "completed"
        mock_batch.output_file_id = "output-file-123"
        mock_batch.error_file_id = None
        mock_groq.batches.retrieve.return_value = mock_batch

        # Mock output file content
        output_content = MagicMock()
        output_content.text = json.dumps({
            "custom_id": doc_id,
            "response": {
                "status_code": 200,
                "body": {
                    "text": "This is the transcribed text.",
                    "segments": [
                        {"start": 0.0, "end": 2.0, "text": "This is the transcribed text."}
                    ],
                    "language": "en",
                },
            },
        })
        mock_groq.files.content.return_value = output_content

        # Process results
        service = TranscriptionService(
            db=db,
            storage=storage,
            groq_client=mock_groq,
        )

        results = service.process_batch_results(batch_id)

        assert len(results["completed"]) == 1
        assert results["completed"][0]["document_id"] == doc_id

        # Verify analysis was created
        analysis = db.get_analysis(doc_id, type_id=1)
        assert analysis is not None

        analysis_data = json.loads(analysis["data"])
        assert analysis_data["text"] == "This is the transcribed text."
        assert len(analysis_data["segments"]) == 1

        # Verify job was marked complete
        job = db.get_transcription_job(job_id)
        assert job["status"] == "completed"


@pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="GROQ_API_KEY not set - skipping real Groq test"
)
class TestRealGroqIntegration:
    """
    Integration tests with real Groq API.

    Only runs if GROQ_API_KEY is set.
    These tests cost money and take time.
    """

    def test_transcribe_single_real(self, db, storage, test_audio_content):
        """Test single document transcription with real Groq API."""
        # Upload test audio
        s3_key = "archive/test-real-groq.wav"
        storage.put_object(s3_key, test_audio_content, content_type="audio/wav")

        # Create document
        doc_id = db.create_document(
            title="Real Groq Test",
            s3_key=s3_key,
            source_id=1,
            type_id=2,
        )

        # Create service with real Groq client
        service = TranscriptionService(
            db=db,
            storage=storage,
            # Uses real Groq client (GROQ_API_KEY from env)
        )

        # Transcribe (sync API for testing)
        result = service.transcribe_single(doc_id)

        assert "error" not in result
        assert result["document_id"] == doc_id
        assert result["text_length"] > 0

        # Verify analysis was created
        analysis = db.get_analysis(doc_id, type_id=1)
        assert analysis is not None
