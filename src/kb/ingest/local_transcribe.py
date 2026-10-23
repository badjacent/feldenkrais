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
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
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
DEFAULT_DOCUMENT_TIMEOUT_SECONDS = 1800


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
        SELECT d.id, d.title, d.s3_key, ds.name AS source_name, d.origin
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
    sql += " ORDER BY (d.origin = 'feldenkrais_atlas') DESC, d.created_at LIMIT %s"
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


def _fail_timed_out_job(db: Database, document_id: str, timeout_seconds: int) -> None:
    """Fail this worker's active claim after its transcription child is killed."""
    error = f"local transcription exceeded {timeout_seconds}s wall-clock timeout"
    with db.get_cursor() as cur:
        cur.execute(
            """
            UPDATE transcription_job
            SET status='failed', error=%s, result_data=%s::jsonb, updated_at=NOW()
            WHERE id = (
                SELECT id FROM transcription_job
                WHERE document_id=%s AND batch_id=%s AND status='processing'
                ORDER BY updated_at DESC LIMIT 1
            )
            """,
            (error, json.dumps({"timeout_seconds": timeout_seconds}), document_id, BATCH_TAG),
        )
    db.log_event(
        "transcription_timeout",
        document_id=document_id,
        detail={"engine": "mlx-whisper", "timeout_seconds": timeout_seconds},
    )


def _transcribe_in_subprocess(
    db: Database,
    doc: dict,
    *,
    model: str,
    language: str | None,
    dry_run: bool,
    max_retries: int,
    stale_minutes: int,
    timeout_seconds: int,
) -> dict:
    """Run one recording behind a killable wall-clock boundary."""
    doc_id = str(doc["id"])
    command = [
        sys.executable,
        "-m",
        "ingest.local_transcribe",
        "one",
        "--doc",
        doc_id,
        "--model",
        model,
        "--max-retries",
        str(max_retries),
        "--stale-minutes",
        str(stale_minutes),
    ]
    if language:
        command.extend(["--language", language])
    if dry_run:
        command.append("--dry-run")
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        if not dry_run:
            _fail_timed_out_job(db, doc_id, timeout_seconds)
        return {
            "document_id": doc_id,
            "error": f"transcription timed out after {timeout_seconds}s",
            "timed_out": True,
        }

    output_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        return {
            "document_id": doc_id,
            "error": f"transcription child exited {completed.returncode}: {message[-500:]}",
        }
    if not output_lines:
        return {"document_id": doc_id, "error": "transcription child returned no result"}
    try:
        return json.loads(output_lines[-1])
    except json.JSONDecodeError:
        return {
            "document_id": doc_id,
            "error": f"transcription child returned invalid JSON: {output_lines[-1][-500:]}",
        }


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
        t0 = datetime.now(UTC)
        result = mlx_whisper.transcribe(
            local_path,
            path_or_hf_repo=model,
            word_timestamps=True,
            language=language,
            verbose=None,
        )
        elapsed = (datetime.now(UTC) - t0).total_seconds()

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
            "transcribed_at": datetime.now(UTC).isoformat(),
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
    except Exception as e:
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
    one = sub.add_parser("one", help=argparse.SUPPRESS)
    one.add_argument("--doc", required=True)
    one.add_argument("--model", default=DEFAULT_MODEL)
    one.add_argument("--language", default=None)
    one.add_argument("--dry-run", action="store_true")
    one.add_argument("--max-retries", type=int, default=3)
    one.add_argument("--stale-minutes", type=int, default=360)
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
    run.add_argument(
        "--document-timeout-seconds",
        type=int,
        default=DEFAULT_DOCUMENT_TIMEOUT_SECONDS,
        help="Kill and fail one recording after this wall-clock duration",
    )
    run.add_argument("--max-retries", type=int, default=3)
    run.add_argument("--stale-minutes", type=int, default=360)
    args = ap.parse_args()

    config = Config.from_env()
    db = Database(config.database_url)
    # Runtime workers consume an already-migrated database. Re-running DDL and
    # queue migrations on every launch can deadlock with crawler startup.
    if os.environ.get("WORKER_INITIALIZE_DATABASE") == "1":
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
                    cur.execute(
                        """
                        SELECT j.document_id, d.title, j.attempts,
                               EXTRACT(EPOCH FROM (NOW() - j.updated_at))::int AS age_seconds
                        FROM transcription_job j
                        JOIN document d ON d.id=j.document_id
                        WHERE j.batch_id=%s AND j.status='processing'
                        ORDER BY j.updated_at LIMIT 1
                        """,
                        (BATCH_TAG,),
                    )
                    active = cur.fetchone()
                    cur.execute(
                        """
                        SELECT
                            count(*) FILTER (
                                WHERE status='failed' AND attempts >= %s
                            ) AS quarantined,
                            count(*) FILTER (
                                WHERE status='failed' AND error LIKE '%%wall-clock timeout%%'
                            ) AS timed_out
                        FROM transcription_job WHERE batch_id=%s
                        """,
                        (3, BATCH_TAG),
                    )
                    failures = cur.fetchone()
                    print(
                        json.dumps(
                            {
                                "worker": "local-mlx",
                                "unclaimed": sum(row["count"] for row in rows),
                                "jobs": {row["status"]: row["count"] for row in jobs},
                                "active": dict(active) if active else None,
                                "quarantined": failures["quarantined"],
                                "timed_out": failures["timed_out"],
                                "host": socket.gethostname(),
                                "checked_at": datetime.now(UTC).isoformat(),
                            },
                            sort_keys=True,
                        )
                    )
            return

        if args.command == "one":
            doc = db.get_document(args.doc)
            if not doc:
                sys.exit(f"no document {args.doc}")
            result = transcribe_document(
                db, storage, doc, args.model, args.language, args.dry_run,
                max_retries=args.max_retries,
                stale_minutes=args.stale_minutes,
            )
            print(json.dumps(result))
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
        stopped_early = False
        for doc in docs:
            if time.monotonic() - started >= args.max_seconds:
                stopped_early = True
                break
            result = _transcribe_in_subprocess(
                db, doc, model=args.model, language=args.language, dry_run=args.dry_run,
                max_retries=args.max_retries,
                stale_minutes=args.stale_minutes,
                timeout_seconds=args.document_timeout_seconds,
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
        # The next run must happen whatever the loader does: failures are due a
        # retry, a full batch may have left more, or the clock cut this one short.
        if not args.doc and (failed or stopped_early or len(docs) >= args.limit):
            try:
                from ingest import transcription_wake
                transcription_wake.arm()
            except Exception as error:  # the next marker from the loader still covers new work
                print(json.dumps({"transcription_wake": f"could not re-arm: {error}"}))
    finally:
        db.close()


if __name__ == "__main__":
    main()
