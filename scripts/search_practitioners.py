#!/usr/bin/env python3
"""
Practitioner content search queue processor.

Searches for Feldenkrais practitioners across platforms (YouTube, web, SoundCloud,
podcasts) to find free lesson content. Processes entries from practitioner_search_queue.json
and feeds findings into source_candidates.json.

Designed to be run incrementally — process a few practitioners when you have spare tokens.

Usage:
    # Show queue status
    .venv/bin/python private/scripts/search_practitioners.py status

    # Process next N pending practitioners (default: 3)
    .venv/bin/python private/scripts/search_practitioners.py process [--limit 3]

    # Process a specific practitioner by name
    .venv/bin/python private/scripts/search_practitioners.py process --name "Jeff Haller"

    # Add a new practitioner to the queue
    .venv/bin/python private/scripts/search_practitioners.py add "Jane Smith" --from "movement-and-creativity"

    # Push all findings with YouTube channels to source_candidates.json
    .venv/bin/python private/scripts/search_practitioners.py export
"""

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

QUEUE_FILE = Path(__file__).parent.parent / "data" / "practitioner_search_queue.json"
CANDIDATES_FILE = Path(__file__).parent.parent / "data" / "source_candidates.json"

# ---------------------------------------------------------------------------
# Queue I/O
# ---------------------------------------------------------------------------

def load_queue() -> dict:
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text())
    return {"_meta": {}, "practitioners": []}


def save_queue(data: dict):
    data["_meta"]["last_updated"] = date.today().isoformat()
    QUEUE_FILE.write_text(json.dumps(data, indent=2))


def load_candidates() -> dict:
    if CANDIDATES_FILE.exists():
        return json.loads(CANDIDATES_FILE.read_text())
    return {"_meta": {}, "candidates": []}


def save_candidates(data: dict):
    data["_meta"]["last_updated"] = date.today().isoformat()
    CANDIDATES_FILE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# YouTube search
# ---------------------------------------------------------------------------

def youtube_search(name: str, max_results: int = 10) -> list[dict]:
    """Search YouTube for a practitioner's Feldenkrais content."""
    if not yt_dlp:
        print("  [skip] yt-dlp not available", file=sys.stderr)
        return []

    queries = [
        f"{name} feldenkrais lesson",
        f"{name} feldenkrais ATM",
        f"{name} awareness through movement",
    ]

    all_videos = {}  # video_id -> dict
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}

    for query in queries:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                data = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            for e in data.get("entries") or []:
                if not e:
                    continue
                vid = e.get("id", "")
                if vid not in all_videos:
                    all_videos[vid] = {
                        "video_id": vid,
                        "title": e.get("title", ""),
                        "channel_name": e.get("channel", "") or e.get("uploader", ""),
                        "channel_id": e.get("channel_id", "") or e.get("uploader_id", ""),
                        "duration_seconds": e.get("duration"),
                        "view_count": e.get("view_count"),
                    }
        except Exception as ex:
            print(f"  [error] YouTube search '{query}': {ex}", file=sys.stderr)
        time.sleep(0.5)

    return list(all_videos.values())


def cluster_channels(videos: list[dict], practitioner_name: str) -> list[dict]:
    """Group videos by channel and identify practitioner's own channel."""
    channels = {}
    for v in videos:
        cid = v.get("channel_id", "")
        if not cid:
            continue
        if cid not in channels:
            channels[cid] = {
                "channel_id": cid,
                "name": v["channel_name"],
                "videos": [],
                "atm_count": 0,
                "name_match": False,
            }
        ch = channels[cid]
        ch["videos"].append(v)
        dur = v.get("duration_seconds") or 0
        if 900 <= dur <= 4500:
            ch["atm_count"] += 1

    # Check which channels match the practitioner's name
    last_name = practitioner_name.split()[-1].lower()
    first_name = practitioner_name.split()[0].lower()
    for ch in channels.values():
        cn = ch["name"].lower()
        if last_name in cn or first_name in cn:
            ch["name_match"] = True

    return sorted(channels.values(), key=lambda c: (c["name_match"], c["atm_count"]), reverse=True)


# ---------------------------------------------------------------------------
# Web search (basic — uses yt-dlp's generic extractor for site checks)
# ---------------------------------------------------------------------------

def find_practitioner_website(name: str) -> list[str]:
    """Try to find a practitioner's website via common patterns.

    Returns list of candidate URLs to check. Actual fetching/validation
    is left to the operator (or a future WebFetch pass).
    """
    # Generate search-friendly name variants
    slug = name.lower().replace(" ", "")
    slug_dash = name.lower().replace(" ", "-")
    slug_dot = name.lower().replace(" ", ".")

    candidates = [
        f"https://www.{slug}.com",
        f"https://www.{slug_dash}.com",
        f"https://{slug_dash}.com",
    ]

    # Known Feldenkrais practitioner directories to suggest searching
    search_suggestions = [
        f"site:feldenkrais.com {name}",
        f"site:ifeldenkrais.com {name}",
        f"{name} feldenkrais practitioner website",
        f"{name} feldenkrais podcast",
        f"{name} feldenkrais soundcloud",
    ]

    return candidates, search_suggestions


# ---------------------------------------------------------------------------
# Process a single practitioner
# ---------------------------------------------------------------------------

def process_practitioner(entry: dict, deep: bool = False) -> dict:
    """Search for a practitioner across platforms and update their entry."""
    name = entry["name"]
    print(f"\n{'='*60}")
    print(f"Processing: {name}")
    print(f"Known from: {', '.join(entry.get('known_from', []))}")

    # YouTube search
    if not entry.get("youtube_channels"):
        print(f"\n  YouTube search...")
        videos = youtube_search(name)
        channels = cluster_channels(videos, name)

        yt_channels = []
        for ch in channels[:5]:  # Top 5 most relevant
            marker = " ← NAME MATCH" if ch["name_match"] else ""
            print(f"    {ch['name']:40s} {len(ch['videos'])} vids, {ch['atm_count']} ATM{marker}")
            if ch["name_match"] or ch["atm_count"] >= 2:
                yt_channels.append({
                    "channel_id": ch["channel_id"],
                    "name": ch["name"],
                    "atm_count": ch["atm_count"],
                    "name_match": ch["name_match"],
                })

        entry["youtube_channels"] = yt_channels
        if not yt_channels:
            print("    No matching channels found")
    else:
        print(f"\n  YouTube: already searched ({len(entry['youtube_channels'])} channels)")

    # Website hints
    if not entry.get("websites"):
        urls, searches = find_practitioner_website(name)
        entry["web_search_hints"] = searches
        print(f"\n  Web search hints generated ({len(searches)} queries)")
    else:
        print(f"\n  Websites: already have {len(entry['websites'])}")

    # Update status
    if entry["status"] == "pending":
        entry["status"] = "searched"
    entry["searched_at"] = date.today().isoformat()

    return entry


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(args):
    data = load_queue()
    practitioners = data.get("practitioners", [])

    by_status = {}
    for p in practitioners:
        s = p.get("status", "pending")
        by_status.setdefault(s, []).append(p)

    print(f"Practitioner search queue: {len(practitioners)} total")
    print(f"Last updated: {data.get('_meta', {}).get('last_updated', 'unknown')}")
    print()
    for status in ["pending", "searched", "deep_searched", "done"]:
        entries = by_status.get(status, [])
        if entries:
            print(f"  {status}: {len(entries)}")
            for p in entries:
                yt_info = ""
                if p.get("youtube_channels"):
                    total_atm = sum(ch.get("atm_count", 0) for ch in p["youtube_channels"])
                    yt_info = f" [YT: {len(p['youtube_channels'])} ch, {total_atm} ATM]"
                print(f"    {p['name']}{yt_info}")


def cmd_process(args):
    data = load_queue()
    practitioners = data.get("practitioners", [])

    if args.name:
        # Process specific practitioner
        targets = [p for p in practitioners if p["name"].lower() == args.name.lower()]
        if not targets:
            print(f"Not found: {args.name}")
            return
    else:
        # Process next N pending
        targets = [p for p in practitioners if p["status"] == "pending"][:args.limit]
        if not targets:
            print("No pending practitioners in queue.")
            return

    print(f"Processing {len(targets)} practitioner(s)...")
    for entry in targets:
        process_practitioner(entry)

    save_queue(data)
    print(f"\nQueue saved. Run 'status' to see updated state.")


def cmd_add(args):
    data = load_queue()

    # Check for duplicates
    existing = {p["name"].lower() for p in data.get("practitioners", [])}
    if args.name.lower() in existing:
        print(f"Already in queue: {args.name}")
        return

    entry = {
        "name": args.name,
        "known_from": [args.source] if args.source else [],
        "status": "pending",
        "youtube_channels": [],
        "websites": [],
        "other_platforms": [],
        "notes": args.note or "",
    }
    data.setdefault("practitioners", []).append(entry)
    save_queue(data)
    print(f"Added: {args.name}")


def cmd_export(args):
    """Export YouTube channel findings to source_candidates.json."""
    queue = load_queue()
    candidates = load_candidates()
    existing_urls = {c["url"] for c in candidates.get("candidates", [])}

    added = 0
    today = date.today().isoformat()

    for p in queue.get("practitioners", []):
        for ch in p.get("youtube_channels", []):
            url = f"https://www.youtube.com/channel/{ch['channel_id']}"
            if url in existing_urls:
                continue
            if ch.get("atm_count", 0) == 0 and not ch.get("name_match"):
                continue  # Skip channels with no ATM content and no name match

            entry = {
                "url": url,
                "title": f"{p['name'].lower().replace(' ', '-')}-yt",
                "snippet": f"YouTube: {ch['name']}. {ch.get('atm_count', 0)} ATM-length videos. Practitioner from {', '.join(p.get('known_from', []))}",
                "discovered_via": "practitioner_search_queue",
                "discovered_at": today,
                "status": "discovered",
                "evaluation": None,
                "platform": "youtube",
                "source_slug": None,
            }
            candidates["candidates"].append(entry)
            existing_urls.add(url)
            added += 1
            print(f"  Added: {ch['name']} ({p['name']})")

    if added:
        save_candidates(candidates)
        print(f"\nExported {added} channels to source_candidates.json")
    else:
        print("No new channels to export.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Practitioner content search queue")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show queue status")

    p_proc = sub.add_parser("process", help="Process pending practitioners")
    p_proc.add_argument("--limit", type=int, default=3, help="Max practitioners to process (default: 3)")
    p_proc.add_argument("--name", help="Process a specific practitioner by name")

    p_add = sub.add_parser("add", help="Add a practitioner to the queue")
    p_add.add_argument("name", help="Practitioner name")
    p_add.add_argument("--from", dest="source", help="Where they're known from (e.g. movement-and-creativity)")
    p_add.add_argument("--note", help="Optional note")

    p_export = sub.add_parser("export", help="Export findings to source_candidates.json")

    args = parser.parse_args()
    if args.command == "status":
        cmd_status(args)
    elif args.command == "process":
        cmd_process(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "export":
        cmd_export(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
