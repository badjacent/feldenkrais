"""
Loader Lambda: processes files from staging/ to archive/ + DB.

Triggered by S3 events on staging/*.
"""

import json
import logging
from pathlib import Path
from typing import Optional
from uuid import uuid4

try:
    # Lambda environment (flat structure)
    from config import Config
    from database import Database
    from storage import Storage
except ImportError:
    # Local development (package structure)
    from .config import Config
    from .database import Database
    from .storage import Storage

logger = logging.getLogger()
logger.setLevel(logging.INFO)


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
        )

        # Delete from staging
        logger.info(f"Cleaning up staging: {main_key}, {sidecar_key}")
        storage.delete_object(main_key)
        storage.delete_object(sidecar_key)

        logger.info(f"Loaded: {main_key} → {doc_id}")
        return {
            "status": "loaded",
            "document_id": doc_id,
            "archive_key": archive_key,
        }

    except Exception as e:
        logger.error(f"Failed to process {main_key}: {e}")
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


def sync_db_from_s3(storage: Storage, db_path: str):
    """Download DB from S3 if it exists."""
    db_key = "db/knowledge.db"
    if storage.object_exists(db_key):
        logger.info(f"Downloading DB from s3://{storage.bucket}/{db_key}")
        storage.download_file(db_key, db_path)
    else:
        logger.info("No existing DB in S3, will create new one")


def sync_db_to_s3(storage: Storage, db_path: str):
    """Upload DB to S3."""
    db_key = "db/knowledge.db"
    logger.info(f"Uploading DB to s3://{storage.bucket}/{db_key}")
    storage.upload_file(db_path, db_key)


def handler(event, context):
    """Lambda handler for S3 events on staging/."""
    logger.info(f"Received event: {json.dumps(event)}")

    config = Config.from_env()
    storage = Storage(config.bucket)

    # Sync DB from S3
    sync_db_from_s3(storage, config.db_path)

    # Initialize DB
    db = Database(config.db_path)
    db.initialize()

    # Build set of main files to process from the event
    main_files = set()
    for record in event.get("Records", []):
        key = record["s3"]["object"]["key"]
        main_key = get_main_file_key(key)
        # Skip folder markers
        if not main_key.endswith("/"):
            main_files.add(main_key)

    logger.info(f"Files to process: {main_files}")

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

    # Sync DB back to S3
    db.close()
    sync_db_to_s3(storage, config.db_path)

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
