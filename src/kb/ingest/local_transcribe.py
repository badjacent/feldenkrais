"""
Local transcription worker (Apple Silicon / mlx-whisper).

Bulk alternative to the Groq Lambda path, intended for the Mac mini: claims
untranscribed documents from the same Postgres queue, downloads audio from S3,
runs Whisper locally with word-level timestamps, and writes the identical
analysis JSON shape as ingest/transcribe.py (text, segments, words, language,
model, chunk_count, transcribed_at) — so nothing downstream changes.

Coexistence with the Lambda: claiming inserts a `transcription_job` row
(batch_id 'local-mlx') only when the document has no jobs and no transcription,
so the scheduled Groq flow skips anything this worker owns, and vice versa.

Usage (from repo root, .env loaded):
    python -m ingest.local_transcribe list
    python -m ingest.local_transcribe run --limit 5 [--source OPENATM]
    python -m ingest.local_transcribe run --doc <document_id>
    # long session on the mini:
    caffeinate -i nice -n 19 .venv/bin/python -m ingest.local_transcribe run --limit 200

Env:
    LOCAL_WHISPER_MODEL   default mlx-community/whisper-large-v3-turbo
                          (use mlx-community/whisper-large-v3 for max quality)
"""

import argparse
import json
import logging
import os
import socket
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    from config import Config
    from database import Database
    from storage import Storage
except ImportError:
    from ..config import Config
    from ..database import Database
    from ..storage import Storage

logger = logging.getLogger("local_transcribe")
TRANSCRIPTION_TYPE_ID = 1
DEFAULT_MODEL = os.environ.get("LOCAL_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
BATCH_TAG = "local-mlx"


def _find_candidates(
    db: Database,
    limit: int,
    source: str | None,
    *,
    max_retries: int = 3,
    stale_minutes: int = 360,
) -> list[dict]:
    """Return new, retriable-failed, or stale locally claimed documents."""
    sql = """
        SELECT d.id, d.title, d.s3_key, ds.name AS source_name
        FROM document d
        JOIN document_source ds ON ds.id = d.source_id
        WHERE d.type_id IN (2, 3)
          AND NOT EXISTS (SELECT 1 FROM analysis a
                          WHERE a.document_id = d.id AND a.type_id = %s)
          AND (
            NOT EXISTS (SELECT 1 FROM transcription_job j WHERE j.document_id = d.id)
            OR EXISTS (
                SELECT 1 FROM transcription_job j
                WHERE j.document_id = d.id AND j.batch_id = %s
                  AND j.attempts < %s
                  AND (
                    j.status = 'failed'
                    OR (j.status IN ('pending', 'processing')
                        AND j.updated_at < NOW() - (%s * INTERVAL '1 minute'))
                  )
            )
          )
    """
    params: list = [TRANSCRIPTION_TYPE_ID, BATCH_TAG, max_retries, stale_minutes]
    if source:
        sql += " AND ds.name = %s"
        params.append(source)
    sql += " ORDER BY d.created_at LIMIT %s"
    params.append(limit)
    with db.get_cursor(commit=False) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _claim(
    db: Database,
    document_id: str,
    *,
    max_retries: int = 3,
    stale_minutes: int = 360,
) -> str | None:
    """Atomically claim a document by inserting a local job row.

    Returns the job id, or None if another worker/the Lambda got there first.
    """
    job_id = str(uuid.uuid4())
    with db.get_cursor() as cur:
        cur.execute(
            """
            UPDATE transcription_job
            SET status='processing', attempts=attempts + 1, error=NULL,
                updated_at=NOW()
            WHERE id = (
                SELECT id FROM transcription_job
                WHERE document_id=%s AND batch_id=%s AND attempts < %s
                  AND (
                    status='failed'
                    OR (status IN ('pending', 'processing')
                        AND updated_at < NOW() - (%s * INTERVAL '1 minute'))
                  )
                ORDER BY updated_at DESC
                FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING id
            """,
            (document_id, BATCH_TAG, max_retries, stale_minutes),
        )
        retried = cur.fetchone()
        if retried:
            return str(retried["id"])
        cur.execute(
            """
            INSERT INTO transcription_job
                (id, document_id, batch_id, status, attempts,
                 chunk_index, total_chunks, created_at, updated_at)
            SELECT %s, %s, %s, 'processing', 1, 0, 1, NOW(), NOW()
            WHERE NOT EXISTS (SELECT 1 FROM transcription_job WHERE document_id = %s)
              AND NOT EXISTS (SELECT 1 FROM analysis
                              WHERE document_id = %s AND type_id = %s)
            """,
            (job_id, document_id, BATCH_TAG, document_id, document_id, TRANSCRIPTION_TYPE_ID),
        )
        return job_id if cur.rowcount == 1 else None


def _finish_job(db: Database, job_id: str, status: str, error: str | None = None,
                result: dict | None = None):
    with db.get_cursor() as cur:
        cur.execute(
            """UPDATE transcription_job
               SET status=%s, error=%s, result_data=%s::jsonb, updated_at=NOW()
               WHERE id=%s""",
            (status, error, json.dumps(result or {}), job_id),
        )


def _prune_segment(seg: dict) -> dict:
    keep = ("id", "start", "end", "text")
    out = {k: seg[k] for k in keep if k in seg}
    return out


def transcribe_document(
    db: Database,
    storage: Storage,
    doc: dict,
    model: str,
    language: str | None,
    dry_run: bool,
    *,
    max_retries: int = 3,
    stale_minutes: int = 360,
) -> dict:
    import mlx_whisper  # lazy: only available on Apple Silicon

    doc_id = str(doc["id"])
    job_id = None
    if not dry_run:
        job_id = _claim(
            db, doc_id, max_retries=max_retries, stale_minutes=stale_minutes
        )
        if job_id is None:
            return {"document_id": doc_id, "skipped": "claimed_elsewhere"}

    with tempfile.NamedTemporaryFile(suffix=Path(doc["s3_key"]).suffix or ".mp3",
                                     delete=False) as f:
        local_path = f.name
    try:
        storage.download_file(doc["s3_key"], local_path)
        logger.info("transcribing %s (%s, %.1f MB, model=%s)", doc_id,
                    doc.get("title", "")[:60],
                    Path(local_path).stat().st_size / 1e6, model)
        t0 = datetime.now(timezone.utc)
        result = mlx_whisper.transcribe(
            local_path,
            path_or_hf_repo=model,
            word_timestamps=True,
            language=language,
            verbose=None,
        )
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()

        segments = [_prune_segment(s) for s in result.get("segments", [])]
        words = [
            {"word": w.get("word"), "start": w.get("start"), "end": w.get("end")}
            for s in result.get("segments", []) for w in (s.get("words") or [])
        ]
        analysis_data = {
            "text": (result.get("text") or "").strip(),
            "segments": segments,
            "words": words,
            "language": result.get("language"),
            "model": model,
            "engine": "mlx-whisper",
            "host": socket.gethostname(),
            "chunk_count": 1,
            "transcribed_at": datetime.now(timezone.utc).isoformat(),
        }
        summary = {
            "document_id": doc_id,
            "text_length": len(analysis_data["text"]),
            "segments": len(segments),
            "words": len(words),
            "elapsed_seconds": round(elapsed, 1),
        }
        if dry_run:
            summary["dry_run"] = True
            return summary

        db.create_analysis(document_id=doc_id, type_id=TRANSCRIPTION_TYPE_ID,
                           data=json.dumps(analysis_data))
        _finish_job(db, job_id, "completed",
                    result={"text_length": summary["text_length"],
                            "elapsed_seconds": summary["elapsed_seconds"]})
        db.log_event("transcription_complete", title=doc.get("title"),
                     document_id=doc_id,
                     detail={"engine": "mlx-whisper", "model": model,
                             "elapsed_seconds": summary["elapsed_seconds"]})
        return summary
    except Exception as e:  # noqa: BLE001 - record failure, keep the batch going
        logger.exception("failed on %s", doc_id)
        if job_id:
            _finish_job(db, job_id, "failed", error=str(e)[:500])
        return {"document_id": doc_id, "error": str(e)}
    finally:
        Path(local_path).unlink(missing_ok=True)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Local mlx-whisper transcription worker")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Show untranscribed candidates by source")
    sub.add_parser("status", help="Print machine-readable local-worker queue health")
    run = sub.add_parser("run", help="Claim and transcribe documents")
    run.add_argument("--limit", type=int, default=1)
    run.add_argument("--source", help="Restrict to a document_source name")
    run.add_argument("--doc", help="Transcribe one specific document id")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--language", default=None,
                     help="Force language code (default: autodetect)")
    run.add_argument("--dry-run", action="store_true",
                     help="Transcribe but write nothing to the database")
    run.add_argument("--max-seconds", type=int, default=3300)
    run.add_argument("--max-retries", type=int, default=3)
    run.add_argument("--stale-minutes", type=int, default=360)
    args = ap.parse_args()

    config = Config.from_env()
    db = Database(config.database_url)
    db.initialize()
    storage = Storage(config.bucket)

    try:
        if args.command in {"list", "status"}:
            with db.get_cursor(commit=False) as cur:
                cur.execute("""
                    SELECT ds.name AS source_name, count(*) AS count FROM document d
                    JOIN document_source ds ON ds.id = d.source_id
                    WHERE d.type_id IN (2,3)
                      AND NOT EXISTS (SELECT 1 FROM analysis a
                                      WHERE a.document_id=d.id AND a.type_id=%s)
                      AND NOT EXISTS (SELECT 1 FROM transcription_job j
                                      WHERE j.document_id=d.id)
                    GROUP BY ds.name ORDER BY 2 DESC
                """, (TRANSCRIPTION_TYPE_ID,))
                rows = cur.fetchall()
                if args.command == "list":
                    for row in rows:
                        print(f"{row['count']:6d}  {row['source_name']}")
                else:
                    cur.execute(
                        """SELECT status, count(*) AS count FROM transcription_job
                           WHERE batch_id=%s GROUP BY status ORDER BY status""",
                        (BATCH_TAG,),
                    )
                    jobs = cur.fetchall()
                    print(
                        json.dumps(
                            {
                                "worker": "local-mlx",
                                "unclaimed": sum(row["count"] for row in rows),
                                "jobs": {row["status"]: row["count"] for row in jobs},
                                "host": socket.gethostname(),
                                "checked_at": datetime.now(timezone.utc).isoformat(),
                            },
                            sort_keys=True,
                        )
                    )
            return

        if args.doc:
            doc = db.get_document(args.doc)
            if not doc:
                sys.exit(f"no document {args.doc}")
            docs = [doc]
        else:
            docs = _find_candidates(
                db,
                args.limit,
                args.source,
                max_retries=args.max_retries,
                stale_minutes=args.stale_minutes,
            )
        print(f"{len(docs)} document(s) to process")
        started = time.monotonic()
        completed = failed = skipped = 0
        for doc in docs:
            if time.monotonic() - started >= args.max_seconds:
                break
            result = transcribe_document(
                db, storage, doc, args.model, args.language, args.dry_run,
                max_retries=args.max_retries,
                stale_minutes=args.stale_minutes,
            )
            print(json.dumps(result))
            if result.get("error"):
                failed += 1
            elif result.get("skipped"):
                skipped += 1
            else:
                completed += 1
        print(json.dumps({"worker_summary": {
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }}, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
