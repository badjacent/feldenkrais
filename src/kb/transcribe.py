"""
Transcription module: converts audio documents to text using Groq's Whisper API.

Uses the Batch API for cost savings (50% discount).
"""

import json
import logging
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from groq import Groq

try:
    from config import Config
    from database import Database
    from storage import Storage
except ImportError:
    from .config import Config
    from .database import Database
    from .storage import Storage

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Analysis type ID for transcription
TRANSCRIPTION_TYPE_ID = 1

# Default configuration
DEFAULT_MODEL = "whisper-large-v3-turbo"
DEFAULT_MAX_RETRIES = 3
DEFAULT_COMPLETION_WINDOW = "24h"
DEFAULT_PRESIGNED_URL_EXPIRY = 86400  # 24 hours


@dataclass
class TranscriptionConfig:
    """Configuration for transcription."""
    model: str = DEFAULT_MODEL
    max_retries: int = DEFAULT_MAX_RETRIES
    completion_window: str = DEFAULT_COMPLETION_WINDOW
    presigned_url_expiry: int = DEFAULT_PRESIGNED_URL_EXPIRY


def create_batch_request(
    document_id: str,
    audio_url: str,
    model: str = DEFAULT_MODEL,
    language: str = "en",
) -> dict:
    """Create a single batch request for audio transcription."""
    return {
        "custom_id": document_id,
        "method": "POST",
        "url": "/v1/audio/transcriptions",
        "body": {
            "model": model,
            "url": audio_url,
            "response_format": "verbose_json",
            "language": language,
        },
    }


def create_batch_file(requests: list[dict], output_path: str) -> str:
    """
    Create a JSONL batch file from requests.

    Returns the path to the created file.
    """
    with open(output_path, "w") as f:
        for req in requests:
            f.write(json.dumps(req) + "\n")
    return output_path


class TranscriptionService:
    """Service for managing audio transcriptions."""

    def __init__(
        self,
        db: Database,
        storage: Storage,
        groq_client: Optional[Groq] = None,
        config: Optional[TranscriptionConfig] = None,
    ):
        self.db = db
        self.storage = storage
        self.groq = groq_client or Groq()
        self.config = config or TranscriptionConfig()

    def get_documents_to_transcribe(self) -> list[dict]:
        """Get documents that need transcription."""
        return self.db.get_untranscribed_documents(
            max_retries=self.config.max_retries
        )

    def create_batch(self, document_ids: Optional[list[str]] = None) -> Optional[str]:
        """
        Create a transcription batch for documents.

        If document_ids is None, processes all untranscribed documents.
        Returns the Groq batch ID, or None if no documents to process.
        """
        # Get documents to process
        if document_ids:
            documents = [
                self.db.get_document(doc_id)
                for doc_id in document_ids
                if self.db.get_document(doc_id)
            ]
        else:
            documents = self.get_documents_to_transcribe()

        if not documents:
            logger.info("No documents to transcribe")
            return None

        logger.info(f"Creating batch for {len(documents)} documents")

        # Generate pre-signed URLs and create batch requests
        requests = []
        jobs_to_create = []

        for doc in documents:
            s3_key = doc["s3_key"]
            doc_id = doc["id"]

            # Generate pre-signed URL
            presigned_url = self.storage.generate_presigned_url(
                s3_key,
                expiry_seconds=self.config.presigned_url_expiry,
            )

            # Check if there's a failed job to update vs create new
            failed_job = self.db.get_failed_transcription_job(doc_id)

            requests.append(create_batch_request(
                document_id=doc_id,
                audio_url=presigned_url,
                model=self.config.model,
            ))

            jobs_to_create.append({
                "document_id": doc_id,
                "failed_job": failed_job,
            })

        # Create JSONL batch file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".jsonl",
            delete=False
        ) as f:
            for req in requests:
                f.write(json.dumps(req) + "\n")
            batch_file_path = f.name

        try:
            # Upload batch file to Groq
            logger.info("Uploading batch file to Groq")
            with open(batch_file_path, "rb") as f:
                file_response = self.groq.files.create(
                    file=f,
                    purpose="batch",
                )
            file_id = file_response.id
            logger.info(f"Uploaded file: {file_id}")

            # Create batch job
            logger.info("Creating batch job")
            batch_response = self.groq.batches.create(
                input_file_id=file_id,
                endpoint="/v1/audio/transcriptions",
                completion_window=self.config.completion_window,
            )
            batch_id = batch_response.id
            logger.info(f"Created batch: {batch_id}")

            # Create or update transcription jobs in DB
            for job_info in jobs_to_create:
                doc_id = job_info["document_id"]
                failed_job = job_info["failed_job"]

                if failed_job:
                    # Update existing failed job
                    self.db.update_transcription_job(
                        job_id=failed_job["id"],
                        status="pending",
                        batch_id=batch_id,
                        error=None,
                        increment_attempts=True,
                    )
                    logger.info(f"Updated job {failed_job['id']} for document {doc_id}")
                else:
                    # Create new job
                    job_id = self.db.create_transcription_job(
                        document_id=doc_id,
                        batch_id=batch_id,
                    )
                    logger.info(f"Created job {job_id} for document {doc_id}")

            return batch_id

        finally:
            # Clean up temp file
            Path(batch_file_path).unlink(missing_ok=True)

    def check_batch_status(self, batch_id: str) -> dict:
        """Check the status of a batch."""
        batch = self.groq.batches.retrieve(batch_id)
        return {
            "id": batch.id,
            "status": batch.status,
            "created_at": batch.created_at,
            "completed_at": batch.completed_at,
            "failed_at": batch.failed_at,
            "request_counts": {
                "total": batch.request_counts.total,
                "completed": batch.request_counts.completed,
                "failed": batch.request_counts.failed,
            } if batch.request_counts else None,
            "output_file_id": batch.output_file_id,
            "error_file_id": batch.error_file_id,
        }

    def process_batch_results(self, batch_id: str) -> dict:
        """
        Process results from a completed batch.

        Returns summary of processed results.
        """
        # Get batch status
        batch = self.groq.batches.retrieve(batch_id)

        if batch.status not in ("completed", "failed", "expired"):
            return {
                "status": "not_ready",
                "batch_status": batch.status,
            }

        results = {
            "batch_id": batch_id,
            "completed": [],
            "failed": [],
        }

        # Process successful results
        if batch.output_file_id:
            output_content = self.groq.files.content(batch.output_file_id)
            for line in output_content.text.strip().split("\n"):
                if not line:
                    continue

                result = json.loads(line)
                custom_id = result["custom_id"]  # This is our document_id
                response = result.get("response", {})

                if response.get("status_code") == 200:
                    body = response.get("body", {})

                    # Create analysis record
                    analysis_data = {
                        "text": body.get("text", ""),
                        "segments": body.get("segments", []),
                        "language": body.get("language"),
                        "model": self.config.model,
                        "batch_id": batch_id,
                        "transcribed_at": datetime.utcnow().isoformat(),
                    }

                    self.db.create_analysis(
                        document_id=custom_id,
                        type_id=TRANSCRIPTION_TYPE_ID,
                        data=json.dumps(analysis_data),
                    )

                    # Update job status
                    jobs = self.db.get_transcription_jobs_by_batch(batch_id)
                    for job in jobs:
                        if job["document_id"] == custom_id:
                            self.db.update_transcription_job(
                                job_id=job["id"],
                                status="completed",
                                result_data=json.dumps(body),
                            )
                            break

                    results["completed"].append({
                        "document_id": custom_id,
                        "text_length": len(body.get("text", "")),
                    })
                    logger.info(f"Processed transcription for document {custom_id}")

                else:
                    # API returned error
                    error_msg = response.get("body", {}).get("error", {}).get("message", "Unknown error")
                    self._mark_job_failed(batch_id, custom_id, error_msg)
                    results["failed"].append({
                        "document_id": custom_id,
                        "error": error_msg,
                    })

        # Process error file if present
        if batch.error_file_id:
            error_content = self.groq.files.content(batch.error_file_id)
            for line in error_content.text.strip().split("\n"):
                if not line:
                    continue

                error_result = json.loads(line)
                custom_id = error_result.get("custom_id")
                error_msg = error_result.get("error", {}).get("message", "Unknown error")

                if custom_id:
                    self._mark_job_failed(batch_id, custom_id, error_msg)
                    results["failed"].append({
                        "document_id": custom_id,
                        "error": error_msg,
                    })

        return results

    def _mark_job_failed(self, batch_id: str, document_id: str, error: str):
        """Mark a transcription job as failed."""
        jobs = self.db.get_transcription_jobs_by_batch(batch_id)
        for job in jobs:
            if job["document_id"] == document_id:
                self.db.update_transcription_job(
                    job_id=job["id"],
                    status="failed",
                    error=error,
                )
                logger.warning(f"Job failed for document {document_id}: {error}")
                break

    def poll_and_process(self, batch_id: str, timeout: int = 3600, interval: int = 30) -> dict:
        """
        Poll a batch until completion and process results.

        Args:
            batch_id: The Groq batch ID
            timeout: Maximum time to wait in seconds
            interval: Polling interval in seconds

        Returns summary of results.
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            status = self.check_batch_status(batch_id)
            logger.info(f"Batch {batch_id} status: {status['status']}")

            if status["status"] in ("completed", "failed", "expired", "cancelled"):
                return self.process_batch_results(batch_id)

            time.sleep(interval)

        return {"status": "timeout", "batch_id": batch_id}

    def transcribe_single(self, document_id: str) -> dict:
        """
        Transcribe a single document synchronously (for testing).

        Uses the sync API instead of batch.
        """
        doc = self.db.get_document(document_id)
        if not doc:
            return {"error": "Document not found"}

        # Generate pre-signed URL
        presigned_url = self.storage.generate_presigned_url(
            doc["s3_key"],
            expiry_seconds=3600,
        )

        logger.info(f"Transcribing document {document_id}")

        # Call sync API
        response = self.groq.audio.transcriptions.create(
            model=self.config.model,
            url=presigned_url,
            response_format="verbose_json",
            language="en",
        )

        # Create analysis record
        analysis_data = {
            "text": response.text,
            "segments": [s.model_dump() for s in (response.segments or [])],
            "language": response.language,
            "model": self.config.model,
            "transcribed_at": datetime.utcnow().isoformat(),
        }

        self.db.create_analysis(
            document_id=document_id,
            type_id=TRANSCRIPTION_TYPE_ID,
            data=json.dumps(analysis_data),
        )

        logger.info(f"Transcription complete for document {document_id}")

        return {
            "document_id": document_id,
            "text_length": len(response.text),
            "text_preview": response.text[:200] + "..." if len(response.text) > 200 else response.text,
        }


def handler(event, context):
    """Lambda handler for transcription operations."""
    logger.info(f"Received event: {json.dumps(event)}")

    action = event.get("action", "create_batch")
    config = Config.from_env()

    # Sync DB from S3
    storage = Storage(config.bucket)
    from loader import sync_db_from_s3, sync_db_to_s3
    sync_db_from_s3(storage, config.db_path)

    db = Database(config.db_path)
    db.initialize()

    service = TranscriptionService(db=db, storage=storage)

    try:
        if action == "create_batch":
            batch_id = service.create_batch()
            result = {"action": "create_batch", "batch_id": batch_id}

        elif action == "check_status":
            batch_id = event.get("batch_id")
            if not batch_id:
                return {"statusCode": 400, "body": json.dumps({"error": "batch_id required"})}
            result = service.check_batch_status(batch_id)

        elif action == "process_results":
            batch_id = event.get("batch_id")
            if not batch_id:
                return {"statusCode": 400, "body": json.dumps({"error": "batch_id required"})}
            result = service.process_batch_results(batch_id)

        elif action == "transcribe_single":
            document_id = event.get("document_id")
            if not document_id:
                return {"statusCode": 400, "body": json.dumps({"error": "document_id required"})}
            result = service.transcribe_single(document_id)

        else:
            return {"statusCode": 400, "body": json.dumps({"error": f"Unknown action: {action}"})}

        # Sync DB back to S3
        db.close()
        sync_db_to_s3(storage, config.db_path)

        return {"statusCode": 200, "body": json.dumps(result)}

    except Exception as e:
        logger.error(f"Error: {e}")
        db.close()
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


if __name__ == "__main__":
    import argparse
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="Transcription CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # create-batch command
    create_parser = subparsers.add_parser("create-batch", help="Create a transcription batch")
    create_parser.add_argument("--document-ids", help="Comma-separated document IDs (optional)")

    # status command
    status_parser = subparsers.add_parser("status", help="Check batch status")
    status_parser.add_argument("batch_id", help="Groq batch ID")

    # process-results command
    process_parser = subparsers.add_parser("process-results", help="Process completed batch results")
    process_parser.add_argument("batch_id", help="Groq batch ID")

    # poll command
    poll_parser = subparsers.add_parser("poll", help="Poll batch until complete and process")
    poll_parser.add_argument("batch_id", help="Groq batch ID")
    poll_parser.add_argument("--timeout", type=int, default=3600, help="Timeout in seconds")
    poll_parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds")

    # single command
    single_parser = subparsers.add_parser("single", help="Transcribe a single document (sync)")
    single_parser.add_argument("document_id", help="Document ID")

    # list-pending command
    list_parser = subparsers.add_parser("list-pending", help="List documents needing transcription")

    # Common arguments
    parser.add_argument("--bucket", help="S3 bucket (overrides KB_BUCKET)")
    parser.add_argument("--db-path", help="Database path (for local mode)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        exit(1)

    # Initialize
    config = Config.from_env()
    bucket = args.bucket or config.bucket

    if config.db_mode == "s3":
        storage = Storage(bucket)
        from kb.loader import sync_db_from_s3, sync_db_to_s3
        sync_db_from_s3(storage, config.db_path)
        db_path = config.db_path
    else:
        storage = Storage(bucket) if bucket else None
        db_path = args.db_path or config.db_path

    db = Database(db_path)
    db.initialize()

    if args.command == "list-pending":
        docs = db.get_untranscribed_documents()
        print(json.dumps([{"id": d["id"], "title": d["title"], "s3_key": d["s3_key"]} for d in docs], indent=2))
        exit(0)

    if not storage:
        print("Error: S3 bucket required")
        exit(1)

    service = TranscriptionService(db=db, storage=storage)

    try:
        if args.command == "create-batch":
            doc_ids = args.document_ids.split(",") if args.document_ids else None
            batch_id = service.create_batch(doc_ids)
            if batch_id:
                print(f"Created batch: {batch_id}")
            else:
                print("No documents to transcribe")

        elif args.command == "status":
            status = service.check_batch_status(args.batch_id)
            print(json.dumps(status, indent=2))

        elif args.command == "process-results":
            results = service.process_batch_results(args.batch_id)
            print(json.dumps(results, indent=2))

        elif args.command == "poll":
            results = service.poll_and_process(
                args.batch_id,
                timeout=args.timeout,
                interval=args.interval,
            )
            print(json.dumps(results, indent=2))

        elif args.command == "single":
            result = service.transcribe_single(args.document_id)
            print(json.dumps(result, indent=2))

    finally:
        db.close()
        if config.db_mode == "s3":
            sync_db_to_s3(storage, config.db_path)
