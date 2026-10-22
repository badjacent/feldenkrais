"""
Loader Lambda: processes files from staging/ to archive/ + DB.

Triggered by S3 events on staging/*.
"""

import json
import logging
from pathlib import Path
from uuid import uuid4

import psycopg2.errors

try:
    # Lambda environment (flat structure)
    from config import Config
    from database import Database
    from storage import Storage
except ImportError:
    # Local development (package structure)
    from ..config import Config
    from ..database import Database
    from ..storage import Storage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_LOAD_RETRIES = 5


def get_file_extension(key: str) -> str:
    """Extract file extension from S3 key."""
    return Path(key).suffix


def get_main_file_key(key: str) -> str:
    """Get the main file key from either a file or its sidecar."""
    if key.endswith(".json"):
        # This is a sidecar, get the main file
        return key[:-5]  # Remove .json
    return key


def process_file(
    storage: Storage,
    db: Database,
    main_key: str,
    grace_period_seconds: int,
) -> dict:
    """
    Process a single file from staging.

    Returns a dict with status and details.
    """
    sidecar_key = f"{main_key}.json"
    filename = Path(main_key).name

    logger.info(f"Processing: {main_key}")

    # Check if main file exists
    if not storage.object_exists(main_key):
        logger.info(f"Skipping - main file not found (sidecar arrived first): {main_key}")
        return {"status": "skipped", "reason": "main_file_not_found"}

    # Get file age
    file_age = storage.get_object_age_seconds(main_key)
    if file_age is None:
        logger.warning(f"Could not determine age of {main_key}")
        file_age = 0

    within_grace_period = file_age < grace_period_seconds

    # Check for sidecar
    sidecar = storage.parse_sidecar(sidecar_key)
    if sidecar is None:
        if within_grace_period:
            logger.info(f"Skipping - no sidecar, within grace period ({file_age:.0f}s): {main_key}")
            return {"status": "skipped", "reason": "no_sidecar_grace_period"}
        else:
            logger.error(f"Error - no sidecar after grace period ({file_age:.0f}s): {main_key}")
            db.log_event("load_error", title=filename,
                         detail={"filename": filename, "reason": "no_sidecar"})
            # Move to errors/
            error_key = main_key.replace("staging/", "errors/", 1)
            storage.copy_object(main_key, error_key)
            storage.delete_object(main_key)
            return {"status": "error", "reason": "no_sidecar"}

    # Parse metadata from sidecar
    title = sidecar.get("title", filename)
    source_url = sidecar.get("source_url")
    source_id = sidecar.get("source_id", 1)
    type_id = sidecar.get("type_id", 1)
    tags = json.dumps(sidecar.get("tags", []))
    lesson_date = sidecar.get("lesson_date")
    lesson_index = sidecar.get("lesson_index")

    # Preserve metadata from sidecar
    metadata = {}
    if sidecar.get("chunk_metadata"):
        metadata["chunk"] = sidecar["chunk_metadata"]
    if sidecar.get("scraper_metadata"):
        metadata["scraper"] = sidecar["scraper_metadata"]
    if sidecar.get("youtube_metadata"):
        metadata["youtube"] = sidecar["youtube_metadata"]
    if sidecar.get("atlas_metadata"):
        metadata["atlas"] = sidecar["atlas_metadata"]
    # FMTT (and any future scraper) enrichment blocks. These feed the /fmtt page
    # query (segment_label, lesson_type, chapter); without mapping them here the
    # data would be dropped and require a separate backfill pass.
    if sidecar.get("lesson_page"):
        metadata["lesson_page"] = sidecar["lesson_page"]
    if sidecar.get("lesson_metadata"):
        metadata["lesson_metadata"] = sidecar["lesson_metadata"]
    metadata_json = json.dumps(metadata) if metadata else None

    # Structured columns the sidecar may carry straight onto the document row.
    origin = sidecar.get("origin")
    presenter = sidecar.get("presenter")
    category_name = sidecar.get("category_name")

    # Check for duplicate document
    if db.document_exists(title, source_url):
        logger.info(f"Skipping duplicate: {title}")
        db.log_event("duplicate_skipped", title=title, detail={"filename": filename})
        # Clean up staging files
        storage.delete_object(main_key)
        storage.delete_object(sidecar_key)
        return {"status": "skipped", "reason": "duplicate"}

    # Generate document ID and archive key
    doc_id = str(uuid4())
    extension = get_file_extension(main_key)
    archive_key = f"archive/{doc_id}{extension}"

    try:
        # Copy to archive
        logger.info(f"Copying to {archive_key}")
        storage.copy_object(main_key, archive_key)

        # Create DB record
        logger.info(f"Creating DB record for {doc_id}")
        db.create_document(
            title=title,
            s3_key=archive_key,
            source_id=source_id,
            type_id=type_id,
            source_url=source_url,
            tags=tags,
            metadata=metadata_json,
            lesson_date=lesson_date,
            lesson_index=lesson_index,
            origin=origin,
            presenter=presenter,
            category_name=category_name,
        )

        # For text documents, store content as analysis (same as transcription)
        if type_id == 1:
            try:
                text_content = storage.get_object(main_key).decode("utf-8")
                db.create_analysis(doc_id, type_id=1, data=json.dumps({"text": text_content}))
                logger.info(f"Stored text content as analysis for {doc_id}")
            except Exception as e:
                logger.warning(f"Could not store text content for {doc_id}: {e}")
        else:
            # Audio or video needs transcribing: wake the Mini's transcription
            # worker, which checks this marker before it queries the database.
            try:
                storage.put_object("control/transcription/pending", b"")
            except Exception as e:
                logger.warning(f"Could not write the transcription wake marker: {e}")

        # Delete from staging
        logger.info(f"Cleaning up staging: {main_key}, {sidecar_key}")
        storage.delete_object(main_key)
        storage.delete_object(sidecar_key)

        logger.info(f"Loaded: {main_key} → {doc_id}")
        db.log_event("loaded", title=title, document_id=doc_id,
                     detail={"filename": filename, "archive_key": archive_key})
        return {
            "status": "loaded",
            "document_id": doc_id,
            "archive_key": archive_key,
        }

    except psycopg2.errors.UniqueViolation:
        # Duplicate detected by UNIQUE constraint - another Lambda already created this document
        logger.info(f"Duplicate detected (race condition): {title}")
        # Clean up - delete staged files and the archive copy we made
        storage.delete_object(main_key)
        storage.delete_object(sidecar_key)
        storage.delete_object(archive_key)
        return {"status": "skipped", "reason": "duplicate_race"}

    except Exception as e:
        logger.error(f"Failed to process {main_key}: {e}")
        db.log_event("load_error", title=title,
                     detail={"filename": filename, "error": str(e)})
        if within_grace_period:
            logger.info(f"Within grace period, will retry: {main_key}")
            return {"status": "error", "reason": "processing_failed_will_retry"}
        else:
            # Move to errors/
            error_key = main_key.replace("staging/", "errors/", 1)
            error_sidecar_key = sidecar_key.replace("staging/", "errors/", 1)
            try:
                storage.copy_object(main_key, error_key)
                storage.copy_object(sidecar_key, error_sidecar_key)
                storage.delete_object(main_key)
                storage.delete_object(sidecar_key)
            except Exception as cleanup_error:
                logger.error(f"Failed to move to errors: {cleanup_error}")
            return {"status": "error", "reason": str(e)}


def retry_from_errors(storage: Storage, db: Database) -> list[dict]:
    """Move eligible files from errors/ back to staging/ for retry.

    Checks each file's load_error count in pipeline_log. If under
    MAX_LOAD_RETRIES, moves back to staging/. Otherwise logs permanent_failure.

    Returns list of retry/failure results.
    """
    error_objects = storage.list_objects("errors/")
    # Only main files (skip sidecars)
    main_files = [obj for obj in error_objects if not obj["key"].endswith(".json")]

    if not main_files:
        return []

    results = []
    for obj in main_files:
        error_key = obj["key"]
        filename = Path(error_key).name

        error_count = db.count_events_for_filename("load_error", filename)

        if error_count >= MAX_LOAD_RETRIES:
            # Check if we already logged permanent_failure for this file
            pf_count = db.count_events_for_filename("permanent_failure", filename)
            if pf_count == 0:
                db.log_event("permanent_failure", title=filename,
                             detail={"filename": filename, "stage": "loader",
                                     "error_count": error_count})
                logger.warning(f"Permanent failure for {filename} ({error_count} errors)")
            results.append({"filename": filename, "status": "permanent_failure"})
            continue

        # Move back to staging/
        staging_key = error_key.replace("errors/", "staging/", 1)
        sidecar_error_key = f"{error_key}.json"
        sidecar_staging_key = f"{staging_key}.json"

        try:
            storage.copy_object(error_key, staging_key)
            storage.delete_object(error_key)
            # Move sidecar if it exists
            if storage.object_exists(sidecar_error_key):
                storage.copy_object(sidecar_error_key, sidecar_staging_key)
                storage.delete_object(sidecar_error_key)

            db.log_event("load_retry", title=filename,
                         detail={"filename": filename, "attempt": error_count + 1,
                                 "max_retries": MAX_LOAD_RETRIES})
            logger.info(f"Retrying {filename} (attempt {error_count + 1}/{MAX_LOAD_RETRIES})")
            results.append({"filename": filename, "status": "retried", "attempt": error_count + 1})

        except Exception as e:
            logger.error(f"Failed to move {error_key} back to staging: {e}")
            results.append({"filename": filename, "status": "error", "reason": str(e)})

    return results


def handler(event, context):
    """Lambda handler for S3 events on staging/."""
    logger.info(f"Received event: {json.dumps(event)}")

    config = Config.from_env()
    storage = Storage(config.bucket)

    # Initialize DB with PostgreSQL connection
    db = Database(config.database_url)
    db.initialize()

    # Acquire advisory lock to prevent concurrent runs
    lock_acquired = db.try_acquire_lock(Database.LOCK_LOADER)
    if not lock_acquired:
        logger.info("Another loader Lambda is running, skipping")
        db.close()
        return {"statusCode": 200, "body": json.dumps({"message": "Skipped - another instance running"})}
    logger.info("Acquired loader lock")

    try:
        # Retry eligible files from errors/ before processing staging/
        retry_results = retry_from_errors(storage, db)
        if retry_results:
            logger.info(f"Error retry results: {len(retry_results)} files checked")

        # List ALL files in staging/ (not just from the event)
        # This ensures we process everything
        staging_objects = storage.list_objects("staging/")
        main_files = set()
        for obj in staging_objects:
            key = obj["key"]
            # Skip sidecars (we'll find them via main file)
            if not key.endswith(".json"):
                main_files.add(key)

        logger.info(f"Files to process from staging/: {len(main_files)}")

        # Process each file
        results = []
        for main_key in main_files:
            result = process_file(
                storage=storage,
                db=db,
                main_key=main_key,
                grace_period_seconds=config.grace_period_seconds,
            )
            result["staging_key"] = main_key
            results.append(result)

        # Summarize results
        loaded = [r for r in results if r["status"] == "loaded"]
        skipped = [r for r in results if r["status"] == "skipped"]
        errors = [r for r in results if r["status"] == "error"]

        logger.info(f"Results: {len(loaded)} loaded, {len(skipped)} skipped, {len(errors)} errors")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "loaded": loaded,
                "skipped": skipped,
                "errors": errors,
            }),
        }

    finally:
        if lock_acquired:
            db.release_lock(Database.LOCK_LOADER)
            logger.info("Released loader lock")
        db.close()
