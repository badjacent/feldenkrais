"""Durable non-YouTube Atlas media intake for the Mac Mini.

Downloads audio-only renditions, stages them in S3 with loader sidecars, and
leaves transcription to the existing shared local transcription worker.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import signal
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from config import Config
from database import Database
from storage import Storage

logger = logging.getLogger("atlas_media")
AUDIO_TYPE_ID = 2
MEDIA_URL_RE = re.compile(
    r'''(?P<url>(?:https?:)?//[^\s"'<>]+?\.(?:mp3|m4a|aac|ogg|opus|wav)(?:\?[^\s"'<>]*)?)''',
    re.IGNORECASE,
)


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


def _embedded_media_url(page: str, page_url: str) -> str | None:
    match = MEDIA_URL_RE.search(html.unescape(page))
    return urljoin(page_url, match.group("url")) if match else None


def _resolve_media_url(url: str) -> str:
    if Path(urlsplit(url).path).suffix.casefold() in {
        ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav",
    }:
        return url
    if urlsplit(url).hostname in {"soundcloud.com", "www.soundcloud.com"}:
        return url
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "FeldenkraisAtlasMedia/1.0 (+https://feldenkraisatlas.com)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return url
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError("media host page exceeds 2 MB resolver limit")
            embedded = _embedded_media_url(body.decode("utf-8", "ignore"), response.url)
            return embedded or url
    except (OSError, ValueError) as exc:
        logger.info("host-page media resolution failed for %s: %s", url, exc)
        return url


class MediaTimeoutError(TimeoutError):
    pass


def _timeout_handler(_signum, _frame):
    raise MediaTimeoutError("media extraction exceeded bounded runtime")


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
        "socket_timeout": int(os.getenv("ATLAS_MEDIA_SOCKET_TIMEOUT_SECONDS", "30")),
        "retries": 2,
        "fragment_retries": 2,
    }
    media_url = _resolve_media_url(url)
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(media_url, download=True)
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
    # Item runtime is capped at ten minutes; thirty minutes leaves ample cleanup
    # margin while preventing a killed worker from blocking throughput for hours.
    stale_minutes = int(os.getenv("ATLAS_MEDIA_STALE_MINUTES", "30"))
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
    started = time.monotonic()
    timeout_seconds = int(os.getenv("ATLAS_MEDIA_ITEM_TIMEOUT_SECONDS", "600"))
    previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout_seconds)
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
        return {"id": str(item["id"]), "url": item["url"], "status": "completed",
                "elapsed_seconds": round(time.monotonic() - started, 1)}
    except Exception as exc:  # one bad source must not stop the durable queue
        logger.exception("media intake failed for %s", item["url"])
        _finish(db, str(item["id"]), "failed", str(exc))
        return {"id": str(item["id"]), "url": item["url"], "status": "failed",
                "error": str(exc)[:500],
                "elapsed_seconds": round(time.monotonic() - started, 1)}
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


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
        started = time.monotonic()
        results = []
        for _ in range(args.limit):
            items = _claim(db, 1)
            if not items:
                break
            result = process(db, storage, items[0])
            results.append(result)
            print(json.dumps(result, sort_keys=True), flush=True)
        print(json.dumps({"worker_summary": {
            "attempted": len(results),
            "completed": sum(item["status"] == "completed" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }}, sort_keys=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
