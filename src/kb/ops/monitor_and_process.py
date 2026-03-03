#!/usr/bin/env python3
"""
Monitor free lesson loading + transcription pipeline, then auto-run
pass0b analysis and rebuild search embeddings when everything is ready.

Polls every 5 minutes. Kicks off processing once all lessons are transcribed.

Usage:
    .venv/bin/python src/kb/ops/monitor_and_process.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# Add src/kb to path for infrastructure imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

from database import Database

POLL_INTERVAL = 300  # 5 minutes
SOURCE_NAME = "FELDENKRAIS_PROJECT_FREE"
SCRIPT_DIR = Path(__file__).resolve().parent.parent  # src/kb/ — where sibling scripts live
VENV_PYTHON = str(Path(__file__).resolve().parent.parent.parent.parent / ".venv" / "bin" / "python")


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def get_source_id(db):
    with db.get_cursor(commit=False) as cursor:
        cursor.execute("SELECT id FROM document_source WHERE name = %s", (SOURCE_NAME,))
        row = cursor.fetchone()
        return row["id"] if row else None


def get_status(db, source_id):
    """Returns (total_docs, transcribed_count, pending_staging)."""
    with db.get_cursor(commit=False) as cursor:
        # Total documents from this source
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM document WHERE source_id = %s",
            (source_id,),
        )
        total = cursor.fetchone()["cnt"]

        # Documents with transcriptions
        cursor.execute("""
            SELECT COUNT(DISTINCT d.id) as cnt
            FROM document d
            JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
            WHERE d.source_id = %s
        """, (source_id,))
        transcribed = cursor.fetchone()["cnt"]

    return total, transcribed


def check_staging_files(db):
    """Check if there are files waiting in S3 staging/."""
    try:
        from storage import Storage
        bucket = os.environ.get("KB_BUCKET")
        if not bucket:
            return 0
        storage = Storage(bucket)
        # List staging/ prefix
        import boto3
        s3 = boto3.client("s3")
        response = s3.list_objects_v2(Bucket=bucket, Prefix="staging/", MaxKeys=100)
        count = response.get("KeyCount", 0)
        return count
    except Exception:
        return -1  # unknown


def run_pipeline_batch(limit=70, workers=3):
    """Run pass0b analysis on all lessons without condensed cache."""
    log(f"Starting pass0b pipeline batch (limit={limit}, workers={workers})...")
    cmd = [
        VENV_PYTHON, str(SCRIPT_DIR / "pipeline" / "batch.py"),
        "--apply", f"--limit={limit}", f"--workers={workers}",
    ]
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR), capture_output=False)
    return result.returncode == 0


def run_embeddings_rebuild():
    """Rebuild search index for all lessons with condensed data."""
    log("Starting search index rebuild (build_embeddings.py --apply)...")
    cmd = [
        VENV_PYTHON, str(SCRIPT_DIR / "search" / "build_embeddings.py"),
        "--apply",
    ]
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR), capture_output=False)
    return result.returncode == 0


def fresh_db():
    """Create a fresh DB connection (Neon drops idle connections)."""
    db = Database(os.environ["DATABASE_URL"])
    db.initialize()
    return db


def main():
    db = fresh_db()
    source_id = get_source_id(db)
    db.close()

    if not source_id:
        log(f"ERROR: Source {SOURCE_NAME} not found in database")
        sys.exit(1)

    log(f"Monitoring source {SOURCE_NAME} (id={source_id})")
    log(f"Polling every {POLL_INTERVAL}s until all lessons are transcribed")
    log("")

    # We expect ~67 free lessons based on the dry run
    expected_min = 50  # at least this many should appear

    while True:
        db = fresh_db()
        try:
            total, transcribed = get_status(db, source_id)
            staging = check_staging_files(db)
        finally:
            db.close()

        staging_str = f", staging files: {staging}" if staging >= 0 else ""

        log(f"Documents: {total} loaded, {transcribed} transcribed{staging_str}")

        # Check if we're ready:
        # - At least expected_min documents have been loaded
        # - All loaded documents are transcribed
        # - No files waiting in staging
        if total >= expected_min and transcribed >= total and staging <= 1:
            log(f"All {total} documents transcribed! Starting processing...")
            break

        # If we have docs but some aren't transcribed yet, show progress
        if total > 0 and transcribed < total:
            remaining = total - transcribed
            log(f"  Waiting for {remaining} transcription(s) to complete...")

        time.sleep(POLL_INTERVAL)

    # Phase 1: Run pass0b analysis
    log("")
    log("=" * 60)
    log("PHASE 1: Pass0b analysis (somatic + language + condensed)")
    log("=" * 60)
    ok = run_pipeline_batch(limit=70, workers=3)
    if not ok:
        log("WARNING: Pipeline batch had errors (check /tmp/pipeline-batch-overnight.log)")

    # Phase 2: Rebuild ALL embeddings (not just new ones)
    log("")
    log("=" * 60)
    log("PHASE 2: Rebuild search index (all lessons)")
    log("=" * 60)
    ok = run_embeddings_rebuild()
    if not ok:
        log("WARNING: Embedding rebuild had errors")

    log("")
    log("=" * 60)
    log("DONE - All processing complete!")
    log("=" * 60)


if __name__ == "__main__":
    main()
