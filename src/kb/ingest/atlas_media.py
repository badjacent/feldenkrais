"""Durable non-YouTube Atlas media intake for the Mac Mini.

Downloads audio-only renditions, stages them in S3 with loader sidecars, and
leaves transcription to the existing shared local transcription worker.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from config import Config
from database import Database
from storage import Storage

logger = logging.getLogger("atlas_media")
AUDIO_TYPE_ID = 2


def _safe(value: str, limit: int = 80) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return (value or "atlas-media")[:limit]


def _source_id(db: Database, item: dict) -> int:
    source_key = item.get("source_key") or "unknown"
    name = f"ATLAS_{_safe(source_key).upper()}"
    category = item.get("teacher") or urlsplit(item["url"]).hostname or "Atlas media"
    with db.get_cursor() as cursor:
        cursor.execute("SELECT id FROM document_source WHERE name=%s", (name,))
        row = cursor.fetchone()
        if row:
            return row["id"]
        cursor.execute(
            """INSERT INTO document_source (name, category_name, visible, public)
               VALUES (%s, %s, TRUE, TRUE) RETURNING id""",
            (name, category),
        )
        return cursor.fetchone()["id"]


def _extract_audio(url: str, output: Path) -> tuple[Path, dict]:
    import yt_dlp

    template = str(output / "%(id)s.%(ext)s")
    options = {
        "format": "bestaudio/best",
        "outtmpl": template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0",
        }],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    media_id = str(info.get("id") or "media")
    result = output / f"{media_id}.mp3"
    if not result.exists():
        matches = list(output.glob("*"))
        if len(matches) != 1:
            raise FileNotFoundError("yt-dlp did not produce exactly one audio file")
        result = matches[0]
    return result, info


def _claim(db: Database, limit: int) -> list[dict]:
    max_attempts = int(os.getenv("ATLAS_MEDIA_MAX_ATTEMPTS", "3"))
    stale_minutes = int(os.getenv("ATLAS_MEDIA_STALE_MINUTES", "360"))
    with db.get_cursor() as cursor:
        cursor.execute(
            """UPDATE atlas_media_queue SET status='pending', updated_at=NOW()
               WHERE attempts < %s AND (
                 (status='failed' AND COALESCE(next_attempt_at, updated_at) <= NOW())
                 OR (status='processing' AND updated_at < NOW() - (%s * INTERVAL '1 minute'))
               )""",
            (max_attempts, stale_minutes),
        )
        cursor.execute(
            """WITH selected AS (
                 SELECT id FROM atlas_media_queue
                 WHERE status='pending' AND attempts < %s
                   AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                 ORDER BY CASE platform
                     WHEN 'soundcloud' THEN 0 WHEN 'podcast' THEN 1 ELSE 2 END,
                   created_at FOR UPDATE SKIP LOCKED LIMIT %s
               )
               UPDATE atlas_media_queue q
               SET status='processing', attempts=attempts+1, error=NULL,
                   next_attempt_at=NULL, updated_at=NOW()
               FROM selected WHERE q.id=selected.id RETURNING q.*""",
            (max_attempts, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def _finish(db: Database, item_id: str, status: str, error: str | None = None) -> None:
    with db.get_cursor() as cursor:
        cursor.execute(
            """UPDATE atlas_media_queue SET status=%s, error=%s,
                   next_attempt_at=CASE WHEN %s='failed' THEN NOW() + INTERVAL '1 hour' ELSE NULL END,
                   updated_at=NOW() WHERE id=%s""",
            (status, error[:500] if error else None, status, item_id),
        )


def process(db: Database, storage: Storage, item: dict) -> dict:
    try:
        with tempfile.TemporaryDirectory(prefix="atlas-media-") as directory:
            audio, extracted = _extract_audio(item["url"], Path(directory))
            token = str(item["id"]).split("-", 1)[0]
            staging_key = f"staging/atlas-{token}-{_safe(item['title'])}.mp3"
            sidecar = {
                "title": item["title"],
                "source_url": item["url"],
                "source_id": _source_id(db, item),
                "type_id": AUDIO_TYPE_ID,
                "origin": "feldenkrais_atlas",
                "presenter": item.get("teacher"),
                "category_name": item.get("content_kind"),
                "atlas_metadata": {
                    "source_key": item.get("source_key"),
                    "platform": item.get("platform"),
                    "content_kind": item.get("content_kind"),
                    "duration_seconds": extracted.get("duration"),
                    **(item.get("metadata") or {}),
                },
            }
            storage.put_object(f"{staging_key}.json", json.dumps(sidecar).encode())
            storage.upload_file(str(audio), staging_key)
        _finish(db, str(item["id"]), "completed")
        return {"id": str(item["id"]), "url": item["url"], "status": "completed"}
    except Exception as exc:  # one bad source must not stop the durable queue
        logger.exception("media intake failed for %s", item["url"])
        _finish(db, str(item["id"]), "failed", str(exc))
        return {"id": str(item["id"]), "url": item["url"], "status": "failed", "error": str(exc)[:500]}


def status(db: Database) -> dict:
    with db.get_cursor(commit=False) as cursor:
        cursor.execute("SELECT status, count(*) AS count FROM atlas_media_queue GROUP BY status")
        return {row["status"]: row["count"] for row in cursor.fetchall()}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    run = sub.add_parser("run")
    run.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()
    config = Config.from_env()
    db = Database(config.database_url)
    try:
        if args.command == "status":
            print(json.dumps(status(db), sort_keys=True))
            return
        storage = Storage(config.bucket)
        items = _claim(db, args.limit)
        print(json.dumps({"claimed": len(items)}))
        for item in items:
            print(json.dumps(process(db, storage, item), sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
