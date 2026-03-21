#!/usr/bin/env python3
"""
YouTube channel discovery for Feldenkrais lessons.

Searches YouTube for Feldenkrais content, clusters results by channel,
and ranks channels by likelihood of containing full-length ATM lessons.

Usage:
    # Run searches across all query categories
    .venv/bin/python private/scripts/discover_youtube.py search

    # Run searches for specific categories only
    .venv/bin/python private/scripts/discover_youtube.py search --categories body_parts classic_lessons

    # Show channel rankings from saved results
    .venv/bin/python private/scripts/discover_youtube.py report

    # Show detailed info for a specific channel
    .venv/bin/python private/scripts/discover_youtube.py channel <channel_id>

    # Deep-scan a channel (pull full video list via channel URL)
    .venv/bin/python private/scripts/discover_youtube.py deep-scan <channel_id>
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import yt_dlp
except ImportError:
    print("yt-dlp required: uv pip install yt-dlp", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Search queries — adapted from discover_sources.py
# ---------------------------------------------------------------------------

QUERIES = {
    "body_parts": [
        "feldenkrais lesson back pain",
        "feldenkrais ATM lesson shoulders",
        "feldenkrais lesson neck tension",
        "feldenkrais hip mobility lesson",
        "feldenkrais jaw TMJ lesson",
        "feldenkrais lesson for knees",
        "feldenkrais lesson feet arches",
        "feldenkrais lesson wrists hands",
        "feldenkrais lesson pelvic floor",
        "feldenkrais rib cage breathing lesson",
    ],
    "functions": [
        "feldenkrais lesson improve breathing",
        "feldenkrais lesson better posture",
        "feldenkrais lesson walking gait",
        "feldenkrais lesson balance stability",
        "feldenkrais lesson flexibility",
        "feldenkrais lesson coordination",
        "feldenkrais rolling lesson floor",
        "feldenkrais sitting lesson",
        "feldenkrais twisting rotation lesson",
        "feldenkrais lesson reaching overhead",
    ],
    "populations": [
        "feldenkrais lesson seniors elderly",
        "feldenkrais lesson musicians",
        "feldenkrais lesson athletes runners",
        "feldenkrais lesson desk workers office",
        "feldenkrais lesson pregnancy",
        "feldenkrais lesson children",
        "feldenkrais chair lesson limited mobility",
        "feldenkrais lesson after surgery recovery",
        "feldenkrais lesson chronic pain",
        "feldenkrais lesson anxiety stress",
    ],
    "classic_lessons": [
        "feldenkrais clock lesson",
        "feldenkrais pelvic clock ATM",
        "feldenkrais skeleton lesson bones",
        "feldenkrais eyes lesson",
        "feldenkrais tongue mouth lesson",
        "feldenkrais lesson imagining movement",
        "feldenkrais lesson side bending",
        "feldenkrais lesson flexion extension",
        "feldenkrais lesson spirals",
        "alexander yanai lesson audio",
    ],
    "general": [
        "feldenkrais awareness through movement full lesson",
        "feldenkrais ATM class recording",
        "feldenkrais method guided lesson",
        "feldenkrais floor lesson",
        "feldenkrais lesson lying down",
        "moshe feldenkrais original lesson",
    ],
}

# Duration thresholds (seconds)
ATM_MIN_DURATION = 15 * 60   # 15 min — short but plausible
ATM_MAX_DURATION = 75 * 60   # 75 min — long class with intro/outro
ATM_SWEET_SPOT = (20 * 60, 55 * 60)  # 20-55 min

# Lesson-signal keywords in titles/descriptions
LESSON_KEYWORDS = re.compile(
    r'\b(atm|awareness.through.movement|lesson|class|guided|floor|lying|'
    r'rolling|pelvic.clock|skeleton|breathing|feldenkrais)\b',
    re.IGNORECASE,
)

# Anti-signal: marketing, intro, promo content
ANTI_KEYWORDS = re.compile(
    r'\b(trailer|promo|preview|teaser|what.is.feldenkrais|introduction.to|'
    r'interview|podcast|webinar|review|testimonial)\b',
    re.IGNORECASE,
)

RESULTS_FILE = Path(__file__).parent.parent / "data" / "youtube_discovery.json"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class VideoResult:
    video_id: str
    title: str
    channel_name: str
    channel_id: str
    duration_seconds: Optional[float]
    view_count: Optional[int]
    description: str = ""
    upload_date: str = ""
    found_via: list = field(default_factory=list)

    @property
    def duration_minutes(self) -> float:
        return (self.duration_seconds or 0) / 60

    @property
    def is_atm_length(self) -> bool:
        d = self.duration_seconds or 0
        return ATM_MIN_DURATION <= d <= ATM_MAX_DURATION

    @property
    def is_sweet_spot(self) -> bool:
        d = self.duration_seconds or 0
        return ATM_SWEET_SPOT[0] <= d <= ATM_SWEET_SPOT[1]

    @property
    def lesson_signal_score(self) -> int:
        text = f"{self.title} {self.description}"
        signals = len(LESSON_KEYWORDS.findall(text))
        anti = len(ANTI_KEYWORDS.findall(text))
        return signals - (anti * 2)


@dataclass
class ChannelProfile:
    channel_id: str
    channel_name: str
    videos: list  # list of VideoResult dicts
    total_found: int = 0
    atm_length_count: int = 0
    sweet_spot_count: int = 0
    avg_lesson_signal: float = 0.0
    score: float = 0.0

    def compute(self):
        self.total_found = len(self.videos)
        atm = [v for v in self.videos if ATM_MIN_DURATION <= (v.get("duration_seconds") or 0) <= ATM_MAX_DURATION]
        sweet = [v for v in self.videos if ATM_SWEET_SPOT[0] <= (v.get("duration_seconds") or 0) <= ATM_SWEET_SPOT[1]]
        self.atm_length_count = len(atm)
        self.sweet_spot_count = len(sweet)

        signals = []
        for v in self.videos:
            text = f"{v.get('title', '')} {v.get('description', '')}"
            s = len(LESSON_KEYWORDS.findall(text)) - len(ANTI_KEYWORDS.findall(text)) * 2
            signals.append(s)
        self.avg_lesson_signal = sum(signals) / len(signals) if signals else 0

        # Score: weighted combination
        # - ATM-length videos are the primary signal
        # - Sweet-spot bonus
        # - Lesson keyword signal
        # - Slight boost for more unique queries hitting the channel
        self.score = (
            self.atm_length_count * 3
            + self.sweet_spot_count * 2
            + self.avg_lesson_signal * self.total_found
        )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_youtube(query: str, max_results: int = 20) -> list[VideoResult]:
    """Search YouTube via yt-dlp and return video metadata."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }
    search_url = f"ytsearch{max_results}:{query}"
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(search_url, download=False)
    except Exception as e:
        print(f"  Error searching '{query}': {e}", file=sys.stderr)
        return []

    results = []
    for entry in data.get("entries") or []:
        if not entry:
            continue
        results.append(VideoResult(
            video_id=entry.get("id", ""),
            title=entry.get("title", ""),
            channel_name=entry.get("channel", "") or entry.get("uploader", ""),
            channel_id=entry.get("channel_id", "") or entry.get("uploader_id", ""),
            duration_seconds=entry.get("duration"),
            view_count=entry.get("view_count"),
            description=entry.get("description") or "",
            upload_date=entry.get("upload_date") or "",
            found_via=[query],
        ))
    return results


def deep_scan_channel(channel_id: str, max_videos: int = 200) -> list[VideoResult]:
    """Pull all videos from a channel via yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "playlistend": max_videos,
    }
    channel_url = f"https://www.youtube.com/channel/{channel_id}/videos"
    print(f"  Scanning channel {channel_id}...")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(channel_url, download=False)
    except Exception as e:
        print(f"  Error scanning channel: {e}", file=sys.stderr)
        return []

    results = []
    for entry in data.get("entries") or []:
        if not entry:
            continue
        results.append(VideoResult(
            video_id=entry.get("id", ""),
            title=entry.get("title", ""),
            channel_name=data.get("channel", "") or data.get("uploader", "") or entry.get("channel", ""),
            channel_id=channel_id,
            duration_seconds=entry.get("duration"),
            view_count=entry.get("view_count"),
            description=entry.get("description") or "",
            upload_date=entry.get("upload_date") or "",
            found_via=["deep_scan"],
        ))
    return results


# ---------------------------------------------------------------------------
# Clustering & scoring
# ---------------------------------------------------------------------------

def cluster_by_channel(videos: list[VideoResult]) -> dict[str, ChannelProfile]:
    """Group videos by channel and compute channel scores."""
    channels: dict[str, dict] = {}

    for v in videos:
        cid = v.channel_id
        if not cid:
            continue
        if cid not in channels:
            channels[cid] = {
                "channel_id": cid,
                "channel_name": v.channel_name,
                "video_map": {},
                "query_hits": set(),
            }
        ch = channels[cid]
        # Update name if we get a better one
        if v.channel_name and not ch["channel_name"]:
            ch["channel_name"] = v.channel_name

        vid = v.video_id
        if vid not in ch["video_map"]:
            ch["video_map"][vid] = asdict(v)
        else:
            # Merge found_via queries
            existing = ch["video_map"][vid]
            existing["found_via"] = list(set(existing["found_via"] + v.found_via))

        for q in v.found_via:
            ch["query_hits"].add(q)

    # Build profiles
    profiles = {}
    for cid, ch in channels.items():
        profile = ChannelProfile(
            channel_id=cid,
            channel_name=ch["channel_name"],
            videos=list(ch["video_map"].values()),
        )
        profile.compute()
        profiles[cid] = profile
    return profiles


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_results() -> dict:
    if RESULTS_FILE.exists():
        return json.loads(RESULTS_FILE.read_text())
    return {"videos": {}, "channels": {}, "meta": {}}


def save_results(data: dict):
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(data, indent=2, default=str))
    print(f"Saved to {RESULTS_FILE}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_search(args):
    categories = args.categories or list(QUERIES.keys())
    max_per_query = args.max_results

    data = load_results()
    all_videos: dict[str, VideoResult] = {}

    # Restore existing videos
    for vid, vdata in data.get("videos", {}).items():
        all_videos[vid] = VideoResult(**{k: v for k, v in vdata.items()
                                         if k in VideoResult.__dataclass_fields__})

    total_queries = sum(len(QUERIES[c]) for c in categories if c in QUERIES)
    query_num = 0

    for cat in categories:
        if cat not in QUERIES:
            print(f"Unknown category: {cat}", file=sys.stderr)
            continue
        queries = QUERIES[cat]
        print(f"\n=== {cat} ({len(queries)} queries) ===")

        for query in queries:
            query_num += 1
            print(f"  [{query_num}/{total_queries}] {query}...", end=" ", flush=True)
            results = search_youtube(query, max_results=max_per_query)
            new_count = 0
            for v in results:
                if v.video_id in all_videos:
                    # Merge found_via
                    existing = all_videos[v.video_id]
                    existing.found_via = list(set(existing.found_via + v.found_via))
                else:
                    all_videos[v.video_id] = v
                    new_count += 1
            atm_count = sum(1 for v in results if v.is_atm_length)
            print(f"{len(results)} results ({new_count} new, {atm_count} ATM-length)")

            # Be polite to YouTube
            time.sleep(1)

    # Cluster and score
    profiles = cluster_by_channel(list(all_videos.values()))

    # Save
    data["videos"] = {vid: asdict(v) for vid, v in all_videos.items()}
    data["channels"] = {cid: asdict(p) for cid, p in profiles.items()}
    data["meta"] = {
        "last_search": datetime.now().isoformat(),
        "categories_searched": categories,
        "total_videos": len(all_videos),
        "total_channels": len(profiles),
    }
    save_results(data)

    # Print summary
    print(f"\n{'='*60}")
    print(f"Total unique videos: {len(all_videos)}")
    print(f"Total channels: {len(profiles)}")
    ranked = sorted(profiles.values(), key=lambda p: p.score, reverse=True)
    print(f"\nTop 20 channels by lesson score:")
    print(f"{'Score':>6}  {'ATM':>4}  {'Sweet':>5}  {'Total':>5}  Channel")
    print(f"{'-'*6}  {'-'*4}  {'-'*5}  {'-'*5}  {'-'*40}")
    for p in ranked[:20]:
        print(f"{p.score:6.1f}  {p.atm_length_count:4d}  {p.sweet_spot_count:5d}  {p.total_found:5d}  {p.channel_name}")


def cmd_report(args):
    data = load_results()
    if not data.get("channels"):
        print("No results yet. Run 'search' first.")
        return

    meta = data.get("meta", {})
    print(f"Last search: {meta.get('last_search', 'unknown')}")
    print(f"Total videos: {meta.get('total_videos', 0)}")
    print(f"Total channels: {meta.get('total_channels', 0)}")

    # Rebuild profiles for scoring
    profiles = []
    for cid, ch_data in data["channels"].items():
        p = ChannelProfile(
            channel_id=ch_data["channel_id"],
            channel_name=ch_data["channel_name"],
            videos=ch_data["videos"],
        )
        p.compute()
        profiles.append(p)

    profiles.sort(key=lambda p: p.score, reverse=True)

    min_score = args.min_score if hasattr(args, "min_score") else 0
    filtered = [p for p in profiles if p.score >= min_score]

    print(f"\nChannels (score >= {min_score}): {len(filtered)}")
    print(f"{'Score':>6}  {'ATM':>4}  {'Sweet':>5}  {'Total':>5}  Channel")
    print(f"{'-'*6}  {'-'*4}  {'-'*5}  {'-'*5}  {'-'*40}")
    for p in filtered:
        print(f"{p.score:6.1f}  {p.atm_length_count:4d}  {p.sweet_spot_count:5d}  {p.total_found:5d}  {p.channel_name}")
        if args.verbose:
            print(f"         https://www.youtube.com/channel/{p.channel_id}")


def cmd_channel(args):
    data = load_results()
    ch_data = data.get("channels", {}).get(args.channel_id)
    if not ch_data:
        print(f"Channel {args.channel_id} not found in results.")
        return

    p = ChannelProfile(
        channel_id=ch_data["channel_id"],
        channel_name=ch_data["channel_name"],
        videos=ch_data["videos"],
    )
    p.compute()

    print(f"Channel: {p.channel_name}")
    print(f"URL: https://www.youtube.com/channel/{p.channel_id}")
    print(f"Score: {p.score:.1f}")
    print(f"Videos found: {p.total_found} ({p.atm_length_count} ATM-length, {p.sweet_spot_count} sweet-spot)")
    print()

    # Sort videos by duration descending
    videos = sorted(p.videos, key=lambda v: v.get("duration_seconds") or 0, reverse=True)
    for v in videos:
        dur = v.get("duration_seconds") or 0
        mins = dur / 60
        marker = " *" if ATM_MIN_DURATION <= dur <= ATM_MAX_DURATION else ""
        sweet = " [sweet]" if ATM_SWEET_SPOT[0] <= dur <= ATM_SWEET_SPOT[1] else ""
        views = v.get("view_count") or 0
        print(f"  {mins:5.1f}m  {views:>7} views  {v['title']}{marker}{sweet}")
        print(f"          https://youtube.com/watch?v={v['video_id']}")
        if v.get("found_via"):
            print(f"          found via: {', '.join(v['found_via'][:3])}")


def cmd_deep_scan(args):
    data = load_results()
    channel_id = args.channel_id

    # Get channel name if we already know it
    existing = data.get("channels", {}).get(channel_id, {})
    channel_name = existing.get("channel_name", channel_id)
    print(f"Deep scanning: {channel_name}")

    videos = deep_scan_channel(channel_id, max_videos=args.max_videos)
    if not videos:
        print("No videos found.")
        return

    # Stats
    atm = [v for v in videos if v.is_atm_length]
    sweet = [v for v in videos if v.is_sweet_spot]
    print(f"\nTotal videos: {len(videos)}")
    print(f"ATM-length (15-75m): {len(atm)}")
    print(f"Sweet-spot (20-55m): {len(sweet)}")

    # Merge into stored data
    for v in videos:
        if v.video_id not in data.get("videos", {}):
            data.setdefault("videos", {})[v.video_id] = asdict(v)

    # Rebuild channel entry with all videos
    all_channel_videos = {}
    for vid, vdata in data.get("videos", {}).items():
        if vdata.get("channel_id") == channel_id:
            all_channel_videos[vid] = vdata

    profile = ChannelProfile(
        channel_id=channel_id,
        channel_name=videos[0].channel_name if videos else channel_name,
        videos=list(all_channel_videos.values()),
    )
    profile.compute()
    data.setdefault("channels", {})[channel_id] = asdict(profile)
    data.setdefault("meta", {})["last_deep_scan"] = {
        "channel_id": channel_id,
        "timestamp": datetime.now().isoformat(),
        "videos_found": len(videos),
    }
    save_results(data)

    # Show ATM-length videos
    if atm:
        print(f"\nATM-length videos:")
        for v in sorted(atm, key=lambda v: v.duration_seconds or 0, reverse=True):
            print(f"  {v.duration_minutes:5.1f}m  {v.title}")
            print(f"          https://youtube.com/watch?v={v.video_id}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="YouTube Feldenkrais channel discovery")
    sub = parser.add_subparsers(dest="command")

    # search
    p_search = sub.add_parser("search", help="Run YouTube searches across query categories")
    p_search.add_argument("--categories", nargs="+", choices=list(QUERIES.keys()),
                          help="Only search these categories")
    p_search.add_argument("--max-results", type=int, default=20,
                          help="Max results per query (default: 20)")

    # report
    p_report = sub.add_parser("report", help="Show channel rankings from saved results")
    p_report.add_argument("--min-score", type=float, default=0,
                          help="Only show channels with score >= this")
    p_report.add_argument("-v", "--verbose", action="store_true",
                          help="Show channel URLs")

    # channel
    p_channel = sub.add_parser("channel", help="Show details for a specific channel")
    p_channel.add_argument("channel_id", help="YouTube channel ID")

    # deep-scan
    p_deep = sub.add_parser("deep-scan", help="Pull full video list from a channel")
    p_deep.add_argument("channel_id", help="YouTube channel ID")
    p_deep.add_argument("--max-videos", type=int, default=200,
                         help="Max videos to pull (default: 200)")

    args = parser.parse_args()
    if args.command == "search":
        cmd_search(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "channel":
        cmd_channel(args)
    elif args.command == "deep-scan":
        cmd_deep_scan(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
