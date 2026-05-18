"""
Transcription module: converts audio documents to text using Groq's Whisper API.

Uses the Batch API for cost savings (50% discount).
Handles large files by chunking them locally before batch submission.

Lambda Actions (via event["action"]):
  - (default/scheduled): Process completed batches, then create new batch for untranscribed docs
  - "create_batch": Create a new transcription batch
  - "check_status": Check status of a batch (requires batch_id)
  - "process_results": Process results of a completed batch (requires batch_id)
  - "retry_failed": Reset failed jobs to pending for retry
  - "transcribe_single": Sync transcribe one document (requires document_id, for testing)

Example invocations:
  aws lambda invoke --function-name feldenkrais-transcription-prod \\
    --cli-binary-format raw-in-base64-out \\
    --payload '{"action": "retry_failed"}' /tmp/out.json

  aws lambda invoke --function-name feldenkrais-transcription-prod \\
    --cli-binary-format raw-in-base64-out \\
    --payload '{"action": "process_results", "batch_id": "batch_xxx"}' /tmp/out.json
"""

import json
import logging
import shutil
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
    from audio import prepare_audio_for_transcription, reassemble_transcriptions
except ImportError:
    from ..config import Config
    from ..database import Database
    from ..storage import Storage
    from ..audio import prepare_audio_for_transcription, reassemble_transcriptions

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Analysis type ID for transcription
TRANSCRIPTION_TYPE_ID = 1

# Default configuration
DEFAULT_MODEL = "whisper-large-v3"
DEFAULT_MAX_RETRIES = 5
DEFAULT_COMPLETION_WINDOW = "24h"
DEFAULT_PRESIGNED_URL_EXPIRY = 86400  # 24 hours
# After this many async retries (retries 0..2 are async batch), switch to sync
# transcription which hits a separate Groq capacity pool.
SYNC_FALLBACK_THRESHOLD = 3
DEFAULT_SYNC_DOCS_PER_RUN = 2  # Cap per Lambda invocation; sync is slow (~1 min/chunk)


@dataclass
class TranscriptionConfig:
    """Configuration for transcription."""
    model: str = DEFAULT_MODEL
    max_retries: int = DEFAULT_MAX_RETRIES
    completion_window: str = DEFAULT_COMPLETION_WINDOW
    presigned_url_expiry: int = DEFAULT_PRESIGNED_URL_EXPIRY
    max_documents_per_batch: int = 10  # Limit documents per Lambda run to avoid timeout


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
            "timestamp_granularities": ["word", "segment"],
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
        """Get documents that need transcription via the async batch API.

        Caps retries at SYNC_FALLBACK_THRESHOLD so high-retry docs go through
        process_sync_fallback() instead of bouncing through the failing batch path.
        """
        return self.db.get_untranscribed_documents(
            max_retries=SYNC_FALLBACK_THRESHOLD
        )

    def create_batch(self, document_ids: Optional[list[str]] = None) -> Optional[str]:
        """
        Create a transcription batch for documents.

        Downloads files, chunks if needed (>20MB), uploads chunks to temp S3,
        and submits batch with pre-signed URLs.

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

        # Limit documents per batch to avoid Lambda timeout
        if len(documents) > self.config.max_documents_per_batch:
            logger.info(f"Limiting batch to {self.config.max_documents_per_batch} of {len(documents)} documents")
            documents = documents[:self.config.max_documents_per_batch]

        logger.info(f"Creating batch for {len(documents)} documents")

        # Create placeholder jobs FIRST to claim documents (prevents race conditions)
        # Other Lambda instances will skip documents that already have pending jobs
        placeholder_jobs = {}
        for doc in documents:
            doc_id = doc["id"]
            try:
                placeholder_id = self.db.create_transcription_job(
                    document_id=doc_id,
                    chunk_index=0,
                    total_chunks=1,  # Will be updated after chunking
                )
                placeholder_jobs[doc_id] = placeholder_id
                logger.info(f"Claimed document {doc_id} with placeholder job {placeholder_id}")
            except Exception as e:
                # Document may have been claimed by another Lambda
                logger.warning(f"Could not claim document {doc_id}: {e}")

        # Filter to only documents we successfully claimed
        documents = [doc for doc in documents if doc["id"] in placeholder_jobs]
        if not documents:
            logger.info("No documents claimed (all may be in progress)")
            return None

        logger.info(f"Processing {len(documents)} claimed documents")

        # Process each document: download, chunk if needed, upload chunks
        requests = []
        jobs_created = []
        temp_dirs = []

        try:
            for doc in documents:
                s3_key = doc["s3_key"]
                doc_id = doc["id"]
                placeholder_id = placeholder_jobs[doc_id]

                # Create temp directory for this document
                temp_dir = tempfile.mkdtemp(prefix=f"transcribe_{doc_id}_")
                temp_dirs.append(temp_dir)

                # Download file
                extension = Path(s3_key).suffix or ".mp3"
                local_path = Path(temp_dir) / f"original{extension}"
                logger.info(f"Downloading {s3_key} to {local_path}")
                self.storage.download_file(s3_key, str(local_path))

                # Chunk if needed
                chunks = prepare_audio_for_transcription(
                    str(local_path),
                    output_dir=str(Path(temp_dir) / "chunks"),
                )
                total_chunks = len(chunks)
                logger.info(f"Document {doc_id}: {total_chunks} chunk(s)")

                # Handle the placeholder job:
                # - If single chunk: update the placeholder with correct info
                # - If multiple chunks: delete placeholder and create new jobs
                if total_chunks == 1:
                    # Update placeholder job with actual chunk info
                    chunk = chunks[0]
                    self.db.update_transcription_job(
                        job_id=placeholder_id,
                        chunk_index=chunk.index,
                        chunk_offset_seconds=chunk.offset_seconds,
                        total_chunks=1,
                    )
                    first_job_id = placeholder_id
                else:
                    # Delete placeholder and create proper jobs for each chunk
                    self.db.delete_transcription_job(placeholder_id)
                    first_job_id = None

                # Upload chunks to temp S3 location and create jobs
                for chunk in chunks:
                    chunk_filename = Path(chunk.path).name
                    chunk_s3_key = f"temp/transcription/{doc_id}/{chunk_filename}"

                    # Upload chunk to S3
                    with open(chunk.path, "rb") as f:
                        self.storage.put_object(chunk_s3_key, f.read())
                    logger.info(f"Uploaded chunk to {chunk_s3_key}")

                    # Create or use existing job
                    if chunk.index == 0 and first_job_id:
                        job_id = first_job_id
                    else:
                        job_id = self.db.create_transcription_job(
                            document_id=doc_id,
                            chunk_index=chunk.index,
                            chunk_offset_seconds=chunk.offset_seconds,
                            total_chunks=total_chunks,
                        )

                    # Generate pre-signed URL
                    presigned_url = self.storage.generate_presigned_url(
                        chunk_s3_key,
                        expiry_seconds=self.config.presigned_url_expiry,
                    )

                    # Create batch request using job_id as custom_id
                    requests.append(create_batch_request(
                        document_id=job_id,  # Use job_id to identify chunk
                        audio_url=presigned_url,
                        model=self.config.model,
                    ))

                    jobs_created.append({
                        "job_id": job_id,
                        "document_id": doc_id,
                        "chunk_s3_key": chunk_s3_key,
                    })

            if not requests:
                logger.info("No requests to submit")
                return None

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
                logger.info(f"Uploading batch file to Groq ({len(requests)} requests)")
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

                # Update all jobs with batch_id
                for job_info in jobs_created:
                    self.db.update_transcription_job(
                        job_id=job_info["job_id"],
                        batch_id=batch_id,
                    )

                # Log batch creation
                doc_titles = [doc.get("title", doc["id"]) for doc in documents]
                self.db.log_event("batch_created", detail={
                    "batch_id": batch_id,
                    "document_count": len(documents),
                    "job_count": len(jobs_created),
                    "titles": doc_titles,
                })

                return batch_id

            finally:
                Path(batch_file_path).unlink(missing_ok=True)

        finally:
            # Clean up temp directories
            for temp_dir in temp_dirs:
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to clean up {temp_dir}: {e}")

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

        Handles chunk reassembly: stores individual chunk results, then
        reassembles when all chunks for a document are complete.

        Returns summary of processed results.
        """
        # Get batch status
        batch = self.groq.batches.retrieve(batch_id)

        if batch.status not in ("completed", "failed", "expired", "cancelled"):
            return {
                "status": "not_ready",
                "batch_status": batch.status,
            }

        results = {
            "batch_id": batch_id,
            "jobs_completed": [],
            "jobs_failed": [],
            "documents_completed": [],
        }

        # Process successful results
        if batch.output_file_id:
            output_content = self.groq.files.content(batch.output_file_id)
            for line in output_content.read().decode().strip().split("\n"):
                if not line:
                    continue

                result = json.loads(line)
                job_id = result["custom_id"]  # This is our job_id (not document_id)
                response = result.get("response", {})

                if response.get("status_code") == 200:
                    body = response.get("body", {})

                    # Update job with result
                    self.db.update_transcription_job(
                        job_id=job_id,
                        status="completed",
                        result_data=json.dumps(body),
                    )

                    results["jobs_completed"].append({
                        "job_id": job_id,
                        "text_length": len(body.get("text", "")),
                    })
                    logger.info(f"Job {job_id} completed")

                else:
                    # API returned error
                    error_msg = response.get("body", {}).get("error", {}).get("message", "Unknown error")
                    self.db.update_transcription_job(
                        job_id=job_id,
                        status="failed",
                        error=error_msg,
                        increment_attempts=True,
                    )

                    # Log transcription failure
                    job = self.db.get_transcription_job(job_id)
                    if job:
                        doc = self.db.get_document(job["document_id"])
                        doc_title = doc.get("title", job["document_id"]) if doc else job["document_id"]
                        self.db.log_event("transcription_failed", title=doc_title,
                                          document_id=job["document_id"], detail={
                                              "batch_id": batch_id,
                                              "job_id": job_id,
                                              "error": error_msg,
                                          })

                    results["jobs_failed"].append({
                        "job_id": job_id,
                        "error": error_msg,
                    })
                    logger.warning(f"Job {job_id} failed: {error_msg}")

        # Process error file if present
        if batch.error_file_id:
            error_content = self.groq.files.content(batch.error_file_id)
            for line in error_content.read().decode().strip().split("\n"):
                if not line:
                    continue

                error_result = json.loads(line)
                job_id = error_result.get("custom_id")
                # Error can be in top-level "error" or in "response.body.error"
                error_msg = "Unknown error"
                if error_result.get("error"):
                    error_msg = error_result["error"].get("message", "Unknown error")
                elif error_result.get("response", {}).get("body", {}).get("error"):
                    error_msg = error_result["response"]["body"]["error"].get("message", "Unknown error")

                if job_id:
                    self.db.update_transcription_job(
                        job_id=job_id,
                        status="failed",
                        error=error_msg,
                        increment_attempts=True,
                    )

                    job = self.db.get_transcription_job(job_id)
                    if job:
                        doc = self.db.get_document(job["document_id"])
                        doc_title = doc.get("title", job["document_id"]) if doc else job["document_id"]
                        self.db.log_event("transcription_failed", title=doc_title,
                                          document_id=job["document_id"], detail={
                                              "batch_id": batch_id,
                                              "job_id": job_id,
                                              "error": error_msg,
                                          })

                    results["jobs_failed"].append({
                        "job_id": job_id,
                        "error": error_msg,
                    })
                    logger.warning(f"Job {job_id} failed (error file): {error_msg}")

        # For cancelled batches, mark any still-pending jobs as failed
        if batch.status == "cancelled":
            jobs = self.db.get_transcription_jobs_by_batch(batch_id)
            for job in jobs:
                if job["status"] == "pending":
                    error_msg = f"Batch {batch_id} was cancelled"
                    self.db.update_transcription_job(
                        job_id=job["id"],
                        status="failed",
                        error=error_msg,
                        increment_attempts=True,
                    )

                    doc = self.db.get_document(job["document_id"])
                    doc_title = doc.get("title", job["document_id"]) if doc else job["document_id"]
                    self.db.log_event("transcription_failed", title=doc_title,
                                      document_id=job["document_id"], detail={
                                          "batch_id": batch_id,
                                          "job_id": job["id"],
                                          "error": error_msg,
                                      })

                    results["jobs_failed"].append({
                        "job_id": job["id"],
                        "error": "Batch cancelled",
                    })
                    logger.warning(f"Job {job['id']} failed: batch was cancelled")

        # Check for documents where all chunks are complete and reassemble
        completed_docs = self._reassemble_completed_documents(batch_id)
        results["documents_completed"] = completed_docs

        # Clean up temp chunks from S3
        self._cleanup_temp_chunks(batch_id)

        return results

    def _reassemble_completed_documents(self, batch_id: str) -> list[dict]:
        """
        Find documents where all chunks are complete and create analysis records.

        Returns list of completed documents.
        """
        jobs = self.db.get_transcription_jobs_by_batch(batch_id)
        if not jobs:
            return []

        # Group jobs by document_id
        doc_jobs = {}
        for job in jobs:
            doc_id = job["document_id"]
            if doc_id not in doc_jobs:
                doc_jobs[doc_id] = []
            doc_jobs[doc_id].append(job)

        completed_docs = []

        for doc_id, doc_job_list in doc_jobs.items():
            # Check if document already has an analysis
            existing = self.db.get_analysis(doc_id, TRANSCRIPTION_TYPE_ID)
            if existing:
                logger.info(f"Document {doc_id} already has transcription, skipping")
                continue

            # Check if all jobs are complete
            all_complete = all(j["status"] == "completed" for j in doc_job_list)
            any_failed = any(j["status"] == "failed" for j in doc_job_list)

            if any_failed:
                logger.warning(f"Document {doc_id} has failed chunks, cannot complete")
                continue

            if not all_complete:
                logger.info(f"Document {doc_id} still has pending chunks")
                continue

            # All chunks complete - reassemble
            total_chunks = doc_job_list[0].get("total_chunks") or len(doc_job_list)

            if len(doc_job_list) != total_chunks:
                logger.warning(f"Document {doc_id} has {len(doc_job_list)} jobs but expected {total_chunks}")
                continue

            # Sort by chunk_index
            doc_job_list.sort(key=lambda j: j.get("chunk_index") or 0)

            # Extract chunk results and offsets
            chunk_results = []
            chunk_offsets = []

            for job in doc_job_list:
                result_data = job.get("result_data")
                if not result_data:
                    logger.error(f"Job {job['id']} has no result_data")
                    continue

                # Handle both string and dict result_data (PostgreSQL returns dict for JSONB)
                if isinstance(result_data, str):
                    body = json.loads(result_data)
                else:
                    body = result_data
                chunk_results.append({
                    "text": body.get("text", ""),
                    "segments": body.get("segments", []),
                    "words": body.get("words", []),
                })
                chunk_offsets.append(job.get("chunk_offset_seconds") or 0)

            if len(chunk_results) != total_chunks:
                logger.error(f"Document {doc_id} missing chunk results")
                continue

            # Reassemble
            if total_chunks > 1:
                combined = reassemble_transcriptions(chunk_results, chunk_offsets)
                text = combined["text"]
                segments = combined["segments"]
                words = combined.get("words", [])
            else:
                text = chunk_results[0]["text"]
                segments = chunk_results[0]["segments"]
                words = chunk_results[0].get("words", [])

            # Create analysis record
            analysis_data = {
                "text": text,
                "segments": segments,
                "words": words,
                "language": "en",  # TODO: get from first chunk
                "model": self.config.model,
                "batch_id": batch_id,
                "chunk_count": total_chunks,
                "transcribed_at": datetime.utcnow().isoformat(),
            }

            self.db.create_analysis(
                document_id=doc_id,
                type_id=TRANSCRIPTION_TYPE_ID,
                data=json.dumps(analysis_data),
            )

            logger.info(f"Created transcription for document {doc_id} ({total_chunks} chunks)")

            # Log transcription completion
            doc = self.db.get_document(doc_id)
            doc_title = doc.get("title", doc_id) if doc else doc_id
            self.db.log_event("transcription_complete", title=doc_title,
                              document_id=doc_id, detail={
                                  "batch_id": batch_id,
                                  "text_length": len(text),
                                  "chunk_count": total_chunks,
                              })

            completed_docs.append({
                "document_id": doc_id,
                "text_length": len(text),
                "chunk_count": total_chunks,
            })

        return completed_docs

    def _cleanup_temp_chunks(self, batch_id: str):
        """Clean up temporary chunk files from S3 after processing."""
        jobs = self.db.get_transcription_jobs_by_batch(batch_id)

        # Get unique document IDs
        doc_ids = set(job["document_id"] for job in jobs)

        for doc_id in doc_ids:
            temp_prefix = f"temp/transcription/{doc_id}/"
            try:
                # List and delete temp files
                temp_objects = self.storage.list_objects(temp_prefix)
                for obj in temp_objects:
                    self.storage.delete_object(obj["key"])
                    logger.info(f"Deleted temp file: {obj['key']}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp chunks for {doc_id}: {e}")

    def cleanup_orphaned_temp_files(self, max_age_seconds: int = 86400) -> int:
        """Delete temp/transcription/ files older than max_age_seconds.

        Returns count of files deleted.
        """
        temp_objects = self.storage.list_objects("temp/transcription/")
        if not temp_objects:
            return 0

        deleted = 0
        for obj in temp_objects:
            age = self.storage.get_object_age_seconds(obj["key"])
            if age is not None and age > max_age_seconds:
                try:
                    self.storage.delete_object(obj["key"])
                    deleted += 1
                    logger.info(f"Deleted orphaned temp file: {obj['key']} (age: {age:.0f}s)")
                except Exception as e:
                    logger.warning(f"Failed to delete {obj['key']}: {e}")

        if deleted:
            self.db.log_event("temp_cleanup", detail={
                "files_deleted": deleted,
                "max_age_seconds": max_age_seconds,
            })
            logger.info(f"Cleaned up {deleted} orphaned temp files")

        return deleted

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

    def process_completed_batches(self) -> list[dict]:
        """
        Check all pending batches and process any that are completed.

        Returns list of processing results.
        """
        # Get unique batch IDs from pending jobs
        batch_ids = self.db.get_pending_batches()

        if not batch_ids:
            return []

        logger.info(f"Checking {len(batch_ids)} pending batches")
        results = []

        for batch_id in batch_ids:
            try:
                status = self.check_batch_status(batch_id)
                logger.info(f"Batch {batch_id}: {status['status']}")

                if status["status"] in ("completed", "failed", "expired", "cancelled"):
                    result = self.process_batch_results(batch_id)
                    result["batch_id"] = batch_id
                    results.append(result)
                    logger.info(f"Processed batch {batch_id}: {len(result.get('jobs_completed', []))} jobs completed, {len(result.get('documents_completed', []))} documents assembled")

            except Exception as e:
                logger.error(f"Error checking batch {batch_id}: {e}")
                results.append({"batch_id": batch_id, "error": str(e)})

        return results

    def expire_stale_jobs(self, max_age_hours: int = 24) -> int:
        """
        Expire pending transcription jobs that have been stuck too long.

        Presigned URLs expire after 24h, so any pending job older than that
        cannot succeed. Marking them as failed lets auto_retry_failed() pick
        them up on the next cycle.

        Returns the number of jobs expired.
        """
        expired = self.db.expire_stale_pending_jobs(max_age_hours=max_age_hours)
        if expired:
            logger.info(f"Expired {expired} stale pending jobs (older than {max_age_hours}h)")
            self.db.log_event("stale_jobs_expired", detail={
                "jobs_expired": expired,
                "max_age_hours": max_age_hours,
            })
        return expired

    def auto_retry_failed(self) -> list[dict]:
        """
        Automatically restage failed documents for retry.

        Finds documents with only failed transcription jobs that haven't exceeded
        the retry limit, deletes their jobs, and increments the document retry counter.
        The documents will then be picked up by create_batch() on the next run.

        Returns list of restaged documents.
        """
        # Log permanent_failure for docs that have exceeded retries
        perm_failed = self.db.get_permanently_failed_documents(
            max_retries=self.config.max_retries
        )
        for doc in perm_failed:
            # Only log once — check if we already logged for this doc
            existing = self.db.get_pipeline_log(limit=1, event="permanent_failure")
            already_logged = any(
                e.get("document_id") and str(e["document_id"]) == str(doc["id"])
                for e in self.db.get_pipeline_log(limit=100, event="permanent_failure")
            )
            if not already_logged:
                self.db.log_event(
                    "permanent_failure",
                    title=doc["title"],
                    document_id=str(doc["id"]),
                    detail={
                        "stage": "transcription",
                        "retry_count": doc["retry_count"],
                        "max_retries": self.config.max_retries,
                    },
                )
                logger.warning(
                    f"Permanent transcription failure: {doc['title']} "
                    f"({doc['retry_count']} retries)"
                )

        # Async retry only handles docs under the sync-fallback threshold;
        # higher-retry docs go through process_sync_fallback() instead.
        failed_docs = self.db.get_failed_documents_for_retry(
            max_retries=SYNC_FALLBACK_THRESHOLD
        )

        if not failed_docs:
            return []

        restaged = []
        for doc in failed_docs:
            doc_id = str(doc["id"])
            retries = doc["transcription_retries"]
            deleted = self.db.restage_document_for_retry(doc_id)

            logger.info(
                f"Restaged document {doc['title']} for retry "
                f"(attempt {retries + 1}/{self.config.max_retries}, "
                f"deleted {deleted} failed jobs)"
            )

            self.db.log_event(
                "transcription_retry",
                title=doc["title"],
                document_id=doc_id,
                detail={
                    "attempt": retries + 1,
                    "max_retries": self.config.max_retries,
                    "jobs_deleted": deleted,
                },
            )

            restaged.append({
                "document_id": doc_id,
                "title": doc["title"],
                "attempt": retries + 1,
                "jobs_deleted": deleted,
            })

        logger.info(f"Restaged {len(restaged)} documents for retry")
        return restaged

    def process_sync_fallback(self, max_docs: int = DEFAULT_SYNC_DOCS_PER_RUN) -> list[dict]:
        """Run synchronous transcription for docs that exhausted async retries.

        Hits Groq's sync `audio.transcriptions.create` endpoint, which has a
        separate capacity pool from the batch API. Slow (~30-90s per chunk) so
        caller caps how many docs per Lambda invocation.

        On success: writes transcription analysis, logs transcription_complete.
        On failure: increments doc retry counter, logs transcription_failed.
        Once doc retries hit max_retries, the next auto_retry_failed call logs
        permanent_failure.
        """
        candidates = self.db.get_documents_for_sync_fallback(
            min_retries=SYNC_FALLBACK_THRESHOLD,
            max_retries=self.config.max_retries,
        )

        if not candidates:
            return []

        candidates = candidates[:max_docs]
        logger.info(f"Sync fallback: attempting {len(candidates)} doc(s)")

        results = []
        for doc in candidates:
            doc_id = str(doc["id"])
            title = doc["title"]
            retries = doc["transcription_retries"]
            logger.info(f"Sync transcribe attempt {retries}/{self.config.max_retries}: {title}")

            try:
                outcome = self.transcribe_single(doc_id)
                if outcome.get("error"):
                    raise RuntimeError(outcome["error"])

                self.db.log_event(
                    "transcription_complete",
                    title=title,
                    document_id=doc_id,
                    detail={
                        "path": "sync_fallback",
                        "text_length": outcome.get("text_length"),
                        "chunk_count": outcome.get("chunk_count"),
                    },
                )
                results.append({**outcome, "status": "completed"})
                logger.info(f"Sync transcribe SUCCESS: {title}")

            except Exception as e:
                error_msg = str(e)[:500]
                new_retries = self.db.increment_document_retries(doc_id)
                self.db.log_event(
                    "transcription_failed",
                    title=title,
                    document_id=doc_id,
                    detail={
                        "path": "sync_fallback",
                        "attempt": retries + 1,
                        "max_retries": self.config.max_retries,
                        "error": error_msg,
                    },
                )
                results.append({
                    "document_id": doc_id,
                    "title": title,
                    "status": "failed",
                    "retries": new_retries,
                    "error": error_msg,
                })
                logger.warning(f"Sync transcribe FAILED: {title} -> {error_msg}")

        return results

    def transcribe_single(self, document_id: str) -> dict:
        """
        Transcribe a single document synchronously.

        Uses the sync API with chunking for large files.
        """
        doc = self.db.get_document(document_id)
        if not doc:
            return {"error": "Document not found"}

        # Idempotency guard: another invocation may have just completed this
        # doc (e.g. scheduled lambda raced a manual invoke). Don't write a duplicate.
        if self.db.get_analysis(document_id, TRANSCRIPTION_TYPE_ID):
            logger.info(f"Document {document_id} already has transcription, skipping sync")
            return {"document_id": document_id, "skipped": "already_transcribed"}

        s3_key = doc["s3_key"]
        logger.info(f"Transcribing document {document_id} ({s3_key})")

        # Download file from S3
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            local_path = f.name

        try:
            self.storage.download_file(s3_key, local_path)
            file_size = Path(local_path).stat().st_size
            logger.info(f"Downloaded {file_size} bytes to {local_path}")

            # Prepare audio (chunk if needed)
            chunks = prepare_audio_for_transcription(local_path)
            logger.info(f"Prepared {len(chunks)} chunk(s) for transcription")

            # Transcribe each chunk
            chunk_results = []
            chunk_offsets = []

            for chunk in chunks:
                logger.info(f"Transcribing chunk {chunk.index} ({chunk.size_bytes} bytes)")

                with open(chunk.path, "rb") as audio_file:
                    response = self.groq.audio.transcriptions.create(
                        file=audio_file,
                        model=self.config.model,
                        response_format="verbose_json",
                        language="en",
                        timestamp_granularities=["word", "segment"],
                    )

                # Handle segments - may be dicts or objects depending on API
                segments = response.segments or []
                if segments and hasattr(segments[0], 'model_dump'):
                    segments = [s.model_dump() for s in segments]

                # Handle words - same treatment
                words = getattr(response, 'words', None) or []
                if words and hasattr(words[0], 'model_dump'):
                    words = [w.model_dump() for w in words]

                chunk_results.append({
                    "text": response.text,
                    "segments": segments,
                    "words": words,
                })
                chunk_offsets.append(chunk.offset_seconds)

            # Reassemble if multiple chunks
            if len(chunks) > 1:
                combined = reassemble_transcriptions(chunk_results, chunk_offsets)
                text = combined["text"]
                segments = combined["segments"]
                words = combined.get("words", [])
            else:
                text = chunk_results[0]["text"]
                segments = chunk_results[0]["segments"]
                words = chunk_results[0].get("words", [])

            # Create analysis record
            analysis_data = {
                "text": text,
                "segments": segments,
                "words": words,
                "language": response.language,
                "model": self.config.model,
                "chunk_count": len(chunks),
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
                "text_length": len(text),
                "chunk_count": len(chunks),
                "text_preview": text[:200] + "..." if len(text) > 200 else text,
            }

        finally:
            # Cleanup temp files
            Path(local_path).unlink(missing_ok=True)


def handler(event, context):
    """Lambda handler for transcription operations."""
    logger.info(f"Received event: {json.dumps(event)}")

    # Default to create_batch (uses batch API - handles chunking internally)
    action = event.get("action", "create_batch")
    config = Config.from_env()
    storage = Storage(config.bucket)

    # Initialize DB with PostgreSQL connection
    db = Database(config.database_url)
    db.initialize()

    # Check LLM kill switch — stall (skip) rather than fail
    settings = db.get_cache("settings:llm_enabled")
    if not settings or not settings.get("enabled", False):
        logger.info("LLM disabled, skipping transcription")
        db.close()
        return {"statusCode": 200, "body": json.dumps({"message": "Skipped - LLM disabled"})}

    # Acquire advisory lock for any action that writes state. Manual invokes
    # (e.g. ad-hoc sync_fallback) need this too — otherwise two invocations
    # see the same eligible docs and race-write duplicate transcription analyses.
    is_scheduled = "source" in event and event["source"] == "aws.events"
    writes_state = action in ("create_batch", "sync_fallback", "retry_failed", "transcribe_single") or is_scheduled
    lock_acquired = False

    if writes_state:
        lock_acquired = db.try_acquire_lock(Database.LOCK_TRANSCRIPTION)
        if not lock_acquired:
            logger.info("Another transcription Lambda is running, skipping")
            db.close()
            return {"statusCode": 200, "body": json.dumps({"message": "Skipped - another instance running"})}
        logger.info("Acquired transcription lock")

    try:
        service = TranscriptionService(db=db, storage=storage)

        # Clean up orphaned temp files on scheduled runs
        if is_scheduled:
            try:
                cleaned = service.cleanup_orphaned_temp_files()
                if cleaned:
                    logger.info(f"Cleaned up {cleaned} orphaned temp files")
            except Exception as e:
                logger.warning(f"Temp cleanup failed: {e}")

        if action == "create_batch":
            # First, process any completed batches
            completed_results = service.process_completed_batches()

            # Expire stale pending jobs so retry can pick them up
            stale_expired = service.expire_stale_jobs()

            # Auto-retry failed documents before creating new batch
            retried = service.auto_retry_failed()

            # Then create a new batch if there are untranscribed documents
            batch_id = service.create_batch()

            # Finally, run sync fallback for docs that exhausted async retries.
            # Sync is slow, so do it last — earlier steps stay fast even on timeout.
            sync_results = service.process_sync_fallback()

            result = {
                "action": "create_batch",
                "batch_id": batch_id,
                "completed_batches": completed_results,
                "stale_jobs_expired": stale_expired,
                "retried_documents": retried,
                "sync_fallback": sync_results,
            }

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

        elif action == "retry_failed":
            retried = service.auto_retry_failed()
            result = {
                "action": "retry_failed",
                "documents_restaged": len(retried),
                "documents": retried,
            }
            logger.info(f"Restaged {len(retried)} documents for retry")

        elif action == "sync_fallback":
            sync_results = service.process_sync_fallback(
                max_docs=event.get("max_docs", DEFAULT_SYNC_DOCS_PER_RUN)
            )
            result = {"action": "sync_fallback", "sync_fallback": sync_results}

        else:
            # Default action for scheduled events: use batch API
            if is_scheduled:
                # First, process any completed batches
                completed_results = service.process_completed_batches()

                # Expire stale pending jobs so retry can pick them up
                stale_expired = service.expire_stale_jobs()

                # Auto-retry failed documents before creating new batch
                retried = service.auto_retry_failed()

                # Then create a new batch if there are untranscribed documents
                batch_id = service.create_batch()

                # Finally, run sync fallback for docs that exhausted async retries.
                # Sync is slow, so do it last — earlier steps stay fast even on timeout.
                sync_results = service.process_sync_fallback()

                result = {
                    "action": "scheduled_batch",
                    "batch_id": batch_id,
                    "completed_batches": completed_results,
                    "stale_jobs_expired": stale_expired,
                    "retried_documents": retried,
                    "sync_fallback": sync_results,
                }
            else:
                return {"statusCode": 400, "body": json.dumps({"error": f"Unknown action: {action}"})}

        return {"statusCode": 200, "body": json.dumps(result)}

    except Exception as e:
        logger.error(f"Error: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

    finally:
        if lock_acquired:
            db.release_lock(Database.LOCK_TRANSCRIPTION)
            logger.info("Released transcription lock")
        db.close()


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

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        exit(1)

    # Initialize
    config = Config.from_env()
    bucket = args.bucket or config.bucket

    db = Database(config.database_url)
    db.initialize()
    storage = Storage(bucket) if bucket else None

    if args.command == "list-pending":
        docs = db.get_untranscribed_documents()
        print(json.dumps([{"id": str(d["id"]), "title": d["title"], "s3_key": d["s3_key"]} for d in docs], indent=2))
        db.close()
        exit(0)

    if not storage:
        print("Error: S3 bucket required")
        db.close()
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
