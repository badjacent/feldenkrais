#!/usr/bin/env python3
"""Queue newly cataloged public lessons for private transcription.

The Atlas remains the discovery authority and this repository remains the
transcript/analysis authority.  This bridge copies URLs only: transcripts,
media, and private analysis never flow back to the public catalog.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from kb.database import Database


def youtube_id(url: str) -> str | None:
    parts = urlsplit(url or "")
    host = (parts.hostname or "").casefold().removeprefix("www.")
    if host == "youtu.be":
        return parts.path.strip("/").split("/", 1)[0] or None
    if host in {"youtube.com", "m.youtube.com"}:
        if parts.path == "/watch":
            return (parse_qs(parts.query).get("v") or [None])[0]
        for prefix in ("/shorts/", "/embed/", "/live/"):
            if parts.path.startswith(prefix):
                return parts.path[len(prefix) :].split("/", 1)[0] or None
    return None


def eligible_urls(catalog: list[dict]) -> dict[str, str]:
    result = {}
    for item in catalog:
        url = str(item.get("url") or "")
        native_id = youtube_id(url)
        if (
            native_id
            and item.get("entry_kind") == "resource"
            and item.get("access") == "free"
            and item.get("content_kind") == "video_lesson"
            and item.get("status") != "dead"
        ):
            result.setdefault(native_id, url)
    return result


def eligible_media(catalog: list[dict]) -> dict[str, dict]:
    """Return non-YouTube, free, on-demand media suitable for local intake."""
    result = {}
    supported_kinds = {"audio_lesson", "podcast_episode", "video_lesson"}
    for item in catalog:
        url = str(item.get("url") or "")
        if (
            url
            and not youtube_id(url)
            and item.get("entry_kind") == "resource"
            and item.get("access") == "free"
            and item.get("content_kind") in supported_kinds
            and item.get("status") != "dead"
            and item.get("platform") in {"website", "soundcloud", "podcast"}
        ):
            result.setdefault(url, item)
    return result


def plan_media_intake(db: Database, catalog: list[dict]) -> dict:
    eligible = eligible_media(catalog)
    with db.get_cursor(commit=False) as cursor:
        cursor.execute("SELECT source_url FROM document WHERE source_url = ANY(%s)", (list(eligible),))
        documents = {row["source_url"] for row in cursor.fetchall()}
        cursor.execute("SELECT url FROM atlas_media_queue WHERE url = ANY(%s)", (list(eligible),))
        queued = {row["url"] for row in cursor.fetchall()}
    pending = [eligible[url] for url in sorted(set(eligible) - documents - queued)]
    return {
        "eligible": len(eligible),
        "already_document": len(set(eligible) & documents),
        "already_queued": len((set(eligible) - documents) & queued),
        "pending": pending,
    }


def queue_media(db: Database, items: list[dict]) -> int:
    added = 0
    with db.get_cursor() as cursor:
        for item in items:
            cursor.execute(
                """INSERT INTO atlas_media_queue
                       (url, title, source_key, platform, content_kind, teacher, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                   ON CONFLICT (url) DO NOTHING""",
                (
                    item["url"], item.get("title") or item["url"],
                    item.get("source_key"), item.get("platform"),
                    item.get("content_kind"), item.get("teacher"),
                    json.dumps({
                        "duration_seconds": item.get("duration_seconds"),
                        "language": item.get("language"),
                        "description": item.get("description"),
                    }),
                ),
            )
            added += cursor.rowcount
    return added


def plan_intake(db: Database, catalog: list[dict]) -> dict:
    eligible = eligible_urls(catalog)
    with db.get_cursor(commit=False) as cursor:
        cursor.execute("SELECT source_url FROM document WHERE source_url IS NOT NULL")
        document_ids = {
            native_id
            for row in cursor.fetchall()
            if (native_id := youtube_id(row["source_url"]))
        }
        cursor.execute("SELECT url FROM youtube_queue")
        queue_ids = {
            native_id
            for row in cursor.fetchall()
            if (native_id := youtube_id(row["url"]))
        }
    pending_ids = sorted(set(eligible) - document_ids - queue_ids)
    return {
        "eligible": len(eligible),
        "already_document": len(set(eligible) & document_ids),
        "already_queued": len((set(eligible) - document_ids) & queue_ids),
        "pending": [eligible[native_id] for native_id in pending_ids],
    }


def reconcile_atlas_origins(db: Database, catalog: list[dict]) -> int:
    """Mark previously queued Atlas lessons for local-only transcription."""
    eligible_ids = set(eligible_urls(catalog))
    updated = 0
    with db.get_cursor() as cursor:
        cursor.execute("SELECT id, url, origin FROM youtube_queue")
        for row in cursor.fetchall():
            if (
                row.get("origin") != "feldenkrais_atlas"
                and youtube_id(row["url"]) in eligible_ids
            ):
                cursor.execute(
                    """UPDATE youtube_queue
                       SET origin = %s,
                           status = CASE WHEN status = 'pending'
                               THEN 'local_pending' ELSE status END,
                           updated_at = NOW()
                       WHERE id = %s""",
                    ("feldenkrais_atlas", row["id"]),
                )
                updated += cursor.rowcount
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Queue free Atlas YouTube lessons for private analysis"
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required")
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    catalog = payload.get("resources", []) if isinstance(payload, dict) else payload
    if not isinstance(catalog, list):
        parser.error("catalog must be a JSON list or contain a resources list")
    db = Database(database_url)
    try:
        report = plan_intake(db, catalog)
        media_report = plan_media_intake(db, catalog)
        origins_reconciled = reconcile_atlas_origins(db, catalog) if args.apply else 0
        selected = report.pop("pending")[: args.limit]
        selected_media = media_report.pop("pending")[: args.limit]
        added = 0
        media_added = 0
        if args.apply:
            added = sum(
                db.add_youtube_url(url, origin="feldenkrais_atlas") is not None
                for url in selected
            )
            media_added = queue_media(db, selected_media)
        report.update(
            {
                "pending": len(selected),
                "added": added,
                "origins_reconciled": origins_reconciled,
                "dry_run": not args.apply,
                "sample": selected[:10],
                "media": {
                    **media_report,
                    "pending": len(selected_media),
                    "added": media_added,
                    "sample": [item["url"] for item in selected_media[:10]],
                },
            }
        )
    finally:
        db.close()
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
