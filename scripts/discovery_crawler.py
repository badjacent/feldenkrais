#!/usr/bin/env python3
"""
Event-driven discovery crawler for Feldenkrais lesson sources.

A work queue where each event can spawn new events — search for a practitioner,
find their YouTube channel, deep-scan it, discover related channels, check their
website for contributor lists, and so on.

Usage:
    # Show queue status
    ./dc status

    # Process next N pending events (default: 5)
    ./dc process [--limit 5]

    # Process events of a specific type only
    ./dc process --type search_practitioner_yt

    # Seed the queue with a practitioner
    ./dc seed practitioner "Jeff Haller" --from movement-and-creativity

    # Seed with a YouTube channel to deep-scan
    ./dc seed channel UCebpkt_dXo9KKyHWMmvt1xw --name "Feldenkrais Access"

    # Seed with a website to check for lessons/contributors
    ./dc seed website "https://example.com/teachers"

    # Seed with a YouTube search query
    ./dc seed query "feldenkrais ATM lesson breathing"

    # Export all discovered sources to source_candidates.json
    ./dc export

    # Show the event log (most recent first)
    ./dc log [--limit 20] [--type search_practitioner_yt]

(Create an alias: alias dc='.venv/bin/python private/scripts/discovery_crawler.py')
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except ImportError:
    pass

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

try:
    import httpx
except ImportError:
    httpx = None

# LLM support — add src/kb to path so we can import the LLM class
_src_kb = Path(__file__).resolve().parent.parent.parent / "src" / "kb"
if str(_src_kb) not in sys.path:
    sys.path.insert(0, str(_src_kb))

_llm_instance = None

def _get_llm():
    """Lazily create an LLM instance for classification tasks."""
    global _llm_instance
    if _llm_instance is None:
        import os
        from llm import LLM
        # Use Gemini Flash — cheap, fast, more than capable for classification
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not set — needed for LLM classification")
        _llm_instance = LLM(
            provider="gemini",
            model="gemini-3-flash-preview",
            api_key=api_key,
            default_max_tokens=2048,
        )
    return _llm_instance

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent.parent / "data"
QUEUE_FILE = DATA_DIR / "discovery_queue.json"
CANDIDATES_FILE = DATA_DIR / "source_candidates.json"

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

# Each event type has: processor function, description
EVENT_TYPES = {
    "search_practitioner_yt":  "Search YouTube for a practitioner's Feldenkrais content",
    "search_practitioner_web": "Search the web for a practitioner's Feldenkrais presence",
    "deep_scan_channel":       "Pull full video catalog from a YouTube channel",
    "search_yt_query":         "Run a YouTube search query and cluster by channel",
    "check_website":           "Fetch a website and look for lessons or contributor lists",
    "scrape_contributors":     "Scrape a contributor/teacher list page for practitioner names",
}

# Duration thresholds
ATM_MIN = 15 * 60
ATM_MAX = 75 * 60
ATM_SWEET = (20 * 60, 55 * 60)

LESSON_KEYWORDS = re.compile(
    r'\b(atm|awareness.through.movement|lesson|class|guided|floor|lying|'
    r'rolling|pelvic.clock|skeleton|breathing|feldenkrais)\b',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Queue I/O
# ---------------------------------------------------------------------------

def load_queue() -> dict:
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text())
    return {"_meta": {"created": date.today().isoformat()}, "events": []}


def save_queue(data: dict):
    data["_meta"]["last_updated"] = datetime.now().isoformat()
    QUEUE_FILE.write_text(json.dumps(data, indent=2, default=str))


def make_event(event_type: str, payload: dict, parent_id: str = None) -> dict:
    return {
        "id": f"evt_{uuid.uuid4().hex[:8]}",
        "type": event_type,
        "payload": payload,
        "status": "pending",
        "parent_id": parent_id,
        "created_at": datetime.now().isoformat(),
        "processed_at": None,
        "results": None,
        "spawned": [],
    }


def enqueue(data: dict, event: dict) -> str:
    """Add event to queue, deduplicating by type+payload."""
    # Check for duplicate pending events
    key = (event["type"], json.dumps(event["payload"], sort_keys=True))
    for existing in data["events"]:
        existing_key = (existing["type"], json.dumps(existing["payload"], sort_keys=True))
        if existing_key == key and existing["status"] in ("pending", "processing"):
            return None  # Already queued
    data["events"].append(event)
    return event["id"]


# ---------------------------------------------------------------------------
# Processors
# ---------------------------------------------------------------------------

def proc_search_practitioner_yt(event: dict, data: dict) -> tuple[dict, list[dict]]:
    """Search YouTube for a practitioner. May spawn deep_scan_channel events."""
    if not yt_dlp:
        return {"error": "yt-dlp not available"}, []

    name = event["payload"]["name"]
    known_from = event["payload"].get("known_from", [])
    print(f"  Searching YouTube for: {name}")

    queries = [
        f"{name} feldenkrais lesson",
        f"{name} feldenkrais ATM",
        f"{name} awareness through movement",
    ]

    all_videos = {}
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}

    for query in queries:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_data = ydl.extract_info(f"ytsearch10:{query}", download=False)
            for e in search_data.get("entries") or []:
                if not e:
                    continue
                vid = e.get("id", "")
                if vid and vid not in all_videos:
                    all_videos[vid] = {
                        "video_id": vid,
                        "title": e.get("title", ""),
                        "channel_name": e.get("channel", "") or e.get("uploader", ""),
                        "channel_id": e.get("channel_id", "") or e.get("uploader_id", ""),
                        "duration_seconds": e.get("duration"),
                        "view_count": e.get("view_count"),
                    }
        except Exception as ex:
            print(f"    Error: {ex}")
        time.sleep(0.5)

    # Cluster by channel
    channels = {}
    for v in all_videos.values():
        cid = v.get("channel_id", "")
        if not cid:
            continue
        if cid not in channels:
            channels[cid] = {"channel_id": cid, "name": v["channel_name"],
                             "videos": 0, "atm": 0, "name_match": False}
        ch = channels[cid]
        ch["videos"] += 1
        dur = v.get("duration_seconds") or 0
        if ATM_MIN <= dur <= ATM_MAX:
            ch["atm"] += 1

    # Check name match
    last_name = name.split()[-1].lower()
    first_name = name.split()[0].lower()
    for ch in channels.values():
        cn = ch["name"].lower()
        if last_name in cn or first_name in cn:
            ch["name_match"] = True

    ranked = sorted(channels.values(), key=lambda c: (c["name_match"], c["atm"]), reverse=True)

    # Report
    for ch in ranked[:5]:
        marker = " ← NAME MATCH" if ch["name_match"] else ""
        print(f"    {ch['name']:40s} {ch['videos']} vids, {ch['atm']} ATM{marker}")

    # Spawn deep-scan for channels that are promising
    new_events = []
    for ch in ranked:
        if ch["name_match"] or ch["atm"] >= 3:
            new_events.append(make_event("deep_scan_channel", {
                "channel_id": ch["channel_id"],
                "channel_name": ch["name"],
                "practitioner": name,
                "known_from": known_from,
            }, parent_id=event["id"]))

    results = {
        "practitioner": name,
        "total_videos": len(all_videos),
        "channels_found": len(channels),
        "channels": ranked[:10],
    }
    return results, new_events


# Platform patterns for classifying web search results
PLATFORM_PATTERNS = {
    "youtube":      re.compile(r'youtube\.com|youtu\.be', re.I),
    "soundcloud":   re.compile(r'soundcloud\.com', re.I),
    "podcast":      re.compile(r'podcasts?\.apple\.com|spotify\.com|anchor\.fm|podbean\.com|buzzsprout\.com|simplecast\.com|transistor\.fm|overcast\.fm|pocketcasts\.com|castbox\.fm', re.I),
    "insight_timer": re.compile(r'insighttimer\.com', re.I),
    "vimeo":        re.compile(r'vimeo\.com', re.I),
    "facebook":     re.compile(r'facebook\.com|fb\.com', re.I),
    "instagram":    re.compile(r'instagram\.com', re.I),
    "directory":    re.compile(r'feldenkrais\.com|feldenkraisguild|iff.*\.org|fgna\.org', re.I),
    "training":     re.compile(r'training|program|certification', re.I),
}

# Domains to skip — not useful for lesson discovery
SKIP_DOMAINS = re.compile(
    r'(linkedin\.com|twitter\.com|x\.com|amazon\.com|goodreads\.com|'
    r'yelp\.com|healthgrades\.com|psychologytoday\.com|'
    r'pinterest\.com|tiktok\.com|reddit\.com)',
    re.I,
)


def _classify_url(url: str) -> str:
    """Classify a URL into a platform category."""
    for platform, pattern in PLATFORM_PATTERNS.items():
        if pattern.search(url):
            return platform
    return "website"


def proc_search_practitioner_web(event: dict, data: dict) -> tuple[dict, list[dict]]:
    """Search the web for a practitioner's Feldenkrais presence.

    Finds personal websites, SoundCloud pages, podcast appearances, directory
    listings, and other non-YouTube sources. May spawn check_website events
    for promising personal/practitioner sites.
    """
    if not DDGS:
        return {"error": "duckduckgo-search not available (pip install duckduckgo-search)"}, []

    name = event["payload"]["name"]
    known_from = event["payload"].get("known_from", [])
    print(f"  Web search for: {name}")

    queries = [
        f'"{name}" feldenkrais',
        f'"{name}" awareness through movement',
        f'"{name}" feldenkrais lessons audio',
    ]

    all_results = {}  # url -> result dict, deduped

    for query in queries:
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(query, max_results=10))
            for hit in hits:
                url = hit.get("href", "")
                if not url or url in all_results:
                    continue
                if SKIP_DOMAINS.search(url):
                    continue
                all_results[url] = {
                    "url": url,
                    "title": hit.get("title", ""),
                    "snippet": hit.get("body", ""),
                    "platform": _classify_url(url),
                    "from_query": query,
                }
        except Exception as ex:
            print(f"    Search error ({query}): {ex}")
        time.sleep(1.0)  # rate-limit between queries

    # Group by platform
    by_platform = {}
    for r in all_results.values():
        by_platform.setdefault(r["platform"], []).append(r)

    # Report
    for platform, hits in sorted(by_platform.items()):
        print(f"    {platform}: {len(hits)}")
        for h in hits[:3]:
            print(f"      {h['url'][:80]}")
            print(f"        {h['title'][:70]}")

    # Spawn check_website for personal/practitioner websites (not big platforms)
    new_events = []
    big_platforms = {"youtube", "facebook", "instagram", "directory"}
    checked_urls = _already_checked_urls(data)

    for r in all_results.values():
        if r["platform"] in big_platforms:
            continue
        if r["url"] in checked_urls:
            continue
        # Heuristic: is the practitioner's name in the URL or title?
        name_lower = name.lower()
        last_name = name.split()[-1].lower()
        url_lower = r["url"].lower()
        title_lower = r["title"].lower()
        name_in_result = last_name in url_lower or last_name in title_lower

        # Spawn check_website for personal sites and promising audio platforms
        if r["platform"] in ("soundcloud", "podcast", "insight_timer", "vimeo"):
            new_events.append(make_event("check_website", {
                "url": r["url"],
                "practitioner": name,
                "platform": r["platform"],
                "known_from": known_from,
            }, parent_id=event["id"]))
        elif r["platform"] == "website" and name_in_result:
            new_events.append(make_event("check_website", {
                "url": r["url"],
                "practitioner": name,
                "platform": "personal_website",
                "known_from": known_from,
            }, parent_id=event["id"]))

    results = {
        "practitioner": name,
        "total_results": len(all_results),
        "by_platform": {p: len(hits) for p, hits in by_platform.items()},
        "results": list(all_results.values())[:20],  # cap stored results
    }
    return results, new_events


def _already_checked_urls(data: dict) -> set:
    """Return URLs that already have check_website events."""
    urls = set()
    for evt in data["events"]:
        if evt["type"] == "check_website":
            urls.add(evt["payload"].get("url"))
    return urls


def proc_deep_scan_channel(event: dict, data: dict) -> tuple[dict, list[dict]]:
    """Pull full video list from a YouTube channel."""
    if not yt_dlp:
        return {"error": "yt-dlp not available"}, []

    channel_id = event["payload"]["channel_id"]
    channel_name = event["payload"].get("channel_name", channel_id)
    print(f"  Deep-scanning: {channel_name}")

    ydl_opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": True, "playlistend": 500,
    }
    channel_url = f"https://www.youtube.com/channel/{channel_id}/videos"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            scan_data = ydl.extract_info(channel_url, download=False)
    except Exception as ex:
        return {"error": str(ex)}, []

    videos = []
    for e in scan_data.get("entries") or []:
        if not e:
            continue
        dur = e.get("duration") or 0
        videos.append({
            "video_id": e.get("id", ""),
            "title": e.get("title", ""),
            "duration_seconds": dur,
            "view_count": e.get("view_count"),
        })

    total = len(videos)
    atm = [v for v in videos if ATM_MIN <= (v["duration_seconds"] or 0) <= ATM_MAX]
    sweet = [v for v in videos if ATM_SWEET[0] <= (v["duration_seconds"] or 0) <= ATM_SWEET[1]]

    print(f"    Total: {total}, ATM-length: {len(atm)}, Sweet-spot: {len(sweet)}")

    # Show top ATM videos
    for v in sorted(atm, key=lambda x: x["duration_seconds"], reverse=True)[:5]:
        mins = v["duration_seconds"] / 60
        print(f"      {mins:5.1f}m  {v['title']}")

    results = {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "total_videos": total,
        "atm_count": len(atm),
        "sweet_spot_count": len(sweet),
        "atm_videos": sorted(atm, key=lambda x: x["duration_seconds"], reverse=True),
        "practitioner": event["payload"].get("practitioner"),
        "known_from": event["payload"].get("known_from", []),
    }
    return results, []


def proc_search_yt_query(event: dict, data: dict) -> tuple[dict, list[dict]]:
    """Run a YouTube search query, report channel clusters."""
    if not yt_dlp:
        return {"error": "yt-dlp not available"}, []

    query = event["payload"]["query"]
    max_results = event["payload"].get("max_results", 20)
    print(f"  Searching: {query}")

    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_data = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
    except Exception as ex:
        return {"error": str(ex)}, []

    channels = {}
    for e in search_data.get("entries") or []:
        if not e:
            continue
        cid = e.get("channel_id", "")
        if not cid:
            continue
        if cid not in channels:
            channels[cid] = {"channel_id": cid, "name": e.get("channel", ""), "videos": 0, "atm": 0}
        channels[cid]["videos"] += 1
        dur = e.get("duration") or 0
        if ATM_MIN <= dur <= ATM_MAX:
            channels[cid]["atm"] += 1

    ranked = sorted(channels.values(), key=lambda c: c["atm"], reverse=True)
    for ch in ranked[:5]:
        print(f"    {ch['name']:40s} {ch['videos']} vids, {ch['atm']} ATM")

    # Spawn deep-scans for channels with ATM content not already scanned
    new_events = []
    scanned = _already_scanned_channels(data)
    for ch in ranked:
        if ch["atm"] >= 2 and ch["channel_id"] not in scanned:
            new_events.append(make_event("deep_scan_channel", {
                "channel_id": ch["channel_id"],
                "channel_name": ch["name"],
            }, parent_id=event["id"]))

    return {"query": query, "channels": ranked[:10]}, new_events


CHECK_WEBSITE_SYSTEM = """You are analyzing a web page to determine if it contains Feldenkrais Awareness Through Movement (ATM) lesson content.

ATM lessons are guided somatic movement classes, typically 20-55 minutes long, available as audio, video, or text transcripts. They may be free or paywalled.

Analyze the page content and return a JSON assessment."""

CHECK_WEBSITE_SCHEMA = {
    "type": "object",
    "description": "Assessment of a web page for Feldenkrais ATM lesson content",
    "properties": {
        "relevant": {
            "type": "boolean",
            "description": "Is this page relevant to Feldenkrais ATM lesson discovery?"
        },
        "has_lessons": {
            "type": "boolean",
            "description": "Does this page contain or link to actual ATM lessons (audio/video/text)?"
        },
        "lesson_count_estimate": {
            "type": "string",
            "enum": ["none", "few (1-5)", "moderate (6-20)", "many (20+)", "unknown"],
            "description": "Estimated number of lessons available"
        },
        "content_types": {
            "type": "array",
            "items": {"type": "string", "enum": ["audio", "video", "text_transcript", "written_guide", "mixed"]},
            "description": "Types of lesson content found"
        },
        "access": {
            "type": "string",
            "enum": ["free", "paywalled", "mixed", "unknown"],
            "description": "Whether the lesson content is free or paywalled"
        },
        "platform_type": {
            "type": "string",
            "enum": ["personal_website", "course_platform", "podcast", "audio_hosting", "video_hosting", "directory_listing", "blog_post", "interview", "training_program", "other"],
            "description": "What kind of platform or page this is"
        },
        "practitioner_names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Names of Feldenkrais practitioners mentioned (for discovering new practitioners)"
        },
        "has_contributor_list": {
            "type": "boolean",
            "description": "Does this page have a list of teachers/contributors/practitioners that could be scraped?"
        },
        "summary": {
            "type": "string",
            "description": "Brief summary of what this page contains and its value for lesson discovery (1-2 sentences)"
        }
    },
    "required": ["relevant", "has_lessons", "lesson_count_estimate", "access", "platform_type", "summary"]
}

# Max chars of page text to send to LLM
PAGE_TEXT_LIMIT = 12000


def _fetch_page_text(url: str) -> tuple[str, int]:
    """Fetch a URL and extract visible text. Returns (text, status_code)."""
    if not httpx:
        raise RuntimeError("httpx not available")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15.0)

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        return f"[Non-HTML content: {content_type}]", resp.status_code

    html = resp.text

    # Strip HTML tags — lightweight extraction without BeautifulSoup
    import re as _re
    # Remove script/style blocks
    text = _re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=_re.DOTALL | _re.IGNORECASE)
    text = _re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove tags
    text = _re.sub(r'<[^>]+>', ' ', text)
    # Decode entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"')
    # Collapse whitespace
    text = _re.sub(r'\s+', ' ', text).strip()

    return text, resp.status_code


def proc_check_website(event: dict, data: dict) -> tuple[dict, list[dict]]:
    """Fetch a website and use an LLM to assess its Feldenkrais lesson content."""
    url = event["payload"]["url"]
    practitioner = event["payload"].get("practitioner", "")
    print(f"  Checking: {url}")

    # Fetch the page
    try:
        page_text, status_code = _fetch_page_text(url)
    except Exception as ex:
        print(f"    Fetch failed: {ex}")
        return {"url": url, "status": "fetch_error", "error": str(ex)}, []

    if status_code >= 400:
        print(f"    HTTP {status_code}")
        return {"url": url, "status": f"http_{status_code}"}, []

    if len(page_text) < 50:
        print(f"    Page too short ({len(page_text)} chars)")
        return {"url": url, "status": "empty_page"}, []

    # Truncate for LLM
    truncated = page_text[:PAGE_TEXT_LIMIT]
    if len(page_text) > PAGE_TEXT_LIMIT:
        truncated += f"\n\n[... truncated, {len(page_text)} total chars]"

    # Build prompt
    prompt_parts = [f"URL: {url}"]
    if practitioner:
        prompt_parts.append(f"Expected practitioner: {practitioner}")
    prompt_parts.append(f"\nPage content:\n{truncated}")
    prompt = "\n".join(prompt_parts)

    # LLM classification
    try:
        llm = _get_llm()
        assessment = llm.complete_structured_json(
            prompt,
            CHECK_WEBSITE_SCHEMA,
            system=CHECK_WEBSITE_SYSTEM,
        )
    except Exception as ex:
        print(f"    LLM error: {ex}")
        return {"url": url, "status": "llm_error", "error": str(ex), "page_length": len(page_text)}, []

    # Report
    print(f"    Relevant: {assessment.get('relevant')}, Lessons: {assessment.get('has_lessons')}, "
          f"Count: {assessment.get('lesson_count_estimate')}, Access: {assessment.get('access')}")
    print(f"    {assessment.get('summary', '')}")

    # Spawn events for discovered practitioners or contributor lists
    new_events = []
    if assessment.get("has_contributor_list"):
        new_events.append(make_event("scrape_contributors", {
            "url": url,
            "names": assessment.get("practitioner_names", []),
            "source_name": _url_to_source_name(url),
        }, parent_id=event["id"]))
        print(f"    → Found contributor list, spawning scrape")

    results = {
        "url": url,
        "status": "checked",
        "page_length": len(page_text),
        "assessment": assessment,
        "practitioner": practitioner,
    }
    return results, new_events


def _url_to_source_name(url: str) -> str:
    """Extract a short source name from a URL."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    # Strip www. and common TLDs
    domain = re.sub(r'^www\.', '', domain)
    domain = re.sub(r'\.(com|org|net|io|co)$', '', domain)
    return domain.replace('.', '-')


def proc_scrape_contributors(event: dict, data: dict) -> tuple[dict, list[dict]]:
    """Placeholder — contributor scraping requires WebFetch.

    When run manually, this is where you'd put the names found
    and it spawns search_practitioner_yt events for each.
    """
    url = event["payload"]["url"]
    names = event["payload"].get("names", [])
    source = event["payload"].get("source_name", "unknown")

    if not names:
        print(f"  Scrape contributors from: {url}")
        print(f"  [manual] Add names to payload.names and reprocess, or use:")
        print(f"    dc seed practitioner '<name>' --from '{source}'")
        return {"url": url, "status": "needs_names"}, []

    # Names provided — spawn practitioner searches (YouTube + web)
    new_events = []
    for name in names:
        new_events.append(make_event("search_practitioner_yt", {
            "name": name,
            "known_from": [source],
        }, parent_id=event["id"]))
        new_events.append(make_event("search_practitioner_web", {
            "name": name,
            "known_from": [source],
        }, parent_id=event["id"]))
    print(f"  Spawning {len(new_events)} practitioner searches ({len(names)} YT + {len(names)} web) from {source}")

    return {"url": url, "names": names, "source": source}, new_events


PROCESSORS = {
    "search_practitioner_yt":  proc_search_practitioner_yt,
    "search_practitioner_web": proc_search_practitioner_web,
    "deep_scan_channel":       proc_deep_scan_channel,
    "search_yt_query":         proc_search_yt_query,
    "check_website":           proc_check_website,
    "scrape_contributors":     proc_scrape_contributors,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _already_scanned_channels(data: dict) -> set:
    """Return channel IDs that have already been deep-scanned."""
    scanned = set()
    for evt in data["events"]:
        if evt["type"] == "deep_scan_channel" and evt["status"] == "done":
            scanned.add(evt["payload"].get("channel_id"))
    return scanned


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(args):
    data = load_queue()
    events = data.get("events", [])

    by_status = {}
    by_type = {}
    for e in events:
        by_status.setdefault(e["status"], []).append(e)
        by_type.setdefault(e["type"], []).append(e)

    print(f"Discovery queue: {len(events)} events")
    print(f"Last updated: {data.get('_meta', {}).get('last_updated', 'unknown')}")
    print()

    for status in ["pending", "processing", "done", "failed"]:
        entries = by_status.get(status, [])
        if entries:
            print(f"  {status}: {len(entries)}")
            # Group by type within status
            sub = {}
            for e in entries:
                sub.setdefault(e["type"], []).append(e)
            for t, evts in sub.items():
                print(f"    {t}: {len(evts)}")
                if status == "pending":
                    for e in evts[:5]:
                        label = _event_label(e)
                        print(f"      {label}")
                    if len(evts) > 5:
                        print(f"      ... and {len(evts)-5} more")

    # Summary of findings
    channels_found = set()
    practitioners_yt = set()
    practitioners_web = set()
    for e in events:
        if e["status"] == "done" and e["results"]:
            if e["type"] == "deep_scan_channel":
                channels_found.add(e["results"].get("channel_id"))
            elif e["type"] == "search_practitioner_yt":
                practitioners_yt.add(e["results"].get("practitioner"))
            elif e["type"] == "search_practitioner_web":
                practitioners_web.add(e["results"].get("practitioner"))

    if channels_found or practitioners_yt or practitioners_web:
        print(f"\n  Findings:")
        if practitioners_yt:
            print(f"    Practitioners searched (YT): {len(practitioners_yt)}")
        if practitioners_web:
            print(f"    Practitioners searched (web): {len(practitioners_web)}")
        if channels_found:
            print(f"    Channels deep-scanned: {len(channels_found)}")


def cmd_process(args):
    data = load_queue()
    events = data.get("events", [])

    pending = [e for e in events if e["status"] == "pending"]
    if args.type:
        pending = [e for e in pending if e["type"] == args.type]

    targets = pending[:args.limit]
    if not targets:
        print("No pending events to process.")
        return

    print(f"Processing {len(targets)} event(s)...\n")

    for event in targets:
        event_type = event["type"]
        processor = PROCESSORS.get(event_type)
        if not processor:
            print(f"  Unknown event type: {event_type}")
            event["status"] = "failed"
            event["results"] = {"error": f"No processor for {event_type}"}
            continue

        label = _event_label(event)
        print(f"[{event['id']}] {event_type}: {label}")
        event["status"] = "processing"

        try:
            results, new_events = processor(event, data)
            event["status"] = "done"
            event["results"] = results
            event["processed_at"] = datetime.now().isoformat()

            # Enqueue spawned events
            spawned_ids = []
            for new_evt in new_events:
                eid = enqueue(data, new_evt)
                if eid:
                    spawned_ids.append(eid)
                    print(f"  → Spawned: {new_evt['type']} {_event_label(new_evt)}")
            event["spawned"] = spawned_ids

        except Exception as ex:
            event["status"] = "failed"
            event["results"] = {"error": str(ex)}
            print(f"  ERROR: {ex}")

        print()
        time.sleep(0.5)

    save_queue(data)
    remaining = sum(1 for e in data["events"] if e["status"] == "pending")
    print(f"Done. {remaining} events still pending.")


def cmd_seed(args):
    data = load_queue()

    if args.seed_type == "practitioner":
        known_from = [args.source] if args.source else []
        event = make_event("search_practitioner_yt", {
            "name": args.value,
            "known_from": known_from,
        })
        web_event = make_event("search_practitioner_web", {
            "name": args.value,
            "known_from": known_from,
        })
        eid2 = enqueue(data, web_event)
        if eid2:
            print(f"Queued: [{eid2}] search_practitioner_web — {args.value}")
    elif args.seed_type == "channel":
        event = make_event("deep_scan_channel", {
            "channel_id": args.value,
            "channel_name": args.name or args.value,
        })
    elif args.seed_type == "website":
        event = make_event("check_website", {
            "url": args.value,
        })
    elif args.seed_type == "query":
        event = make_event("search_yt_query", {
            "query": args.value,
            "max_results": 20,
        })
    elif args.seed_type == "contributors":
        names = [n.strip() for n in args.value.split(",") if n.strip()]
        event = make_event("scrape_contributors", {
            "url": args.source or "manual",
            "names": names,
            "source_name": args.name or "manual",
        })
    else:
        print(f"Unknown seed type: {args.seed_type}")
        return

    eid = enqueue(data, event)
    if eid:
        save_queue(data)
        print(f"Queued: [{eid}] {event['type']} — {_event_label(event)}")
    else:
        print("Already in queue (duplicate).")


def cmd_seed_batch(args):
    """Seed multiple practitioners from a text file or comma-separated list."""
    data = load_queue()
    names = []

    if args.file:
        names = [line.strip() for line in Path(args.file).read_text().splitlines() if line.strip()]
    elif args.names:
        names = [n.strip() for n in args.names.split(",") if n.strip()]

    added = 0
    known_from = [args.source] if args.source else []
    for name in names:
        yt_event = make_event("search_practitioner_yt", {
            "name": name,
            "known_from": known_from,
        })
        web_event = make_event("search_practitioner_web", {
            "name": name,
            "known_from": known_from,
        })
        if enqueue(data, yt_event):
            added += 1
        if enqueue(data, web_event):
            added += 1

    if added:
        save_queue(data)
    print(f"Queued {added} events for {len(names)} practitioners.")


def cmd_export(args):
    """Export discovered channels to source_candidates.json."""
    data = load_queue()

    if not CANDIDATES_FILE.exists():
        print(f"No candidates file at {CANDIDATES_FILE}")
        return
    candidates = json.loads(CANDIDATES_FILE.read_text())
    existing_urls = {c["url"] for c in candidates.get("candidates", [])}

    today = date.today().isoformat()
    added = 0

    for event in data["events"]:
        if event["status"] != "done" or not event.get("results"):
            continue

        if event["type"] == "deep_scan_channel":
            r = event["results"]
            if r.get("error"):
                continue
            if r.get("atm_count", 0) == 0:
                continue

            url = f"https://www.youtube.com/channel/{r['channel_id']}"
            if url in existing_urls:
                continue

            practitioner = r.get("practitioner", "")
            known_from = r.get("known_from", [])
            snippet_parts = [f"YouTube: {r['channel_name']}."]
            snippet_parts.append(f"{r['total_videos']} videos, {r['atm_count']} ATM-length, {r.get('sweet_spot_count', 0)} sweet-spot.")
            if practitioner:
                snippet_parts.append(f"Practitioner: {practitioner}.")
            if known_from:
                snippet_parts.append(f"From: {', '.join(known_from)}.")

            entry = {
                "url": url,
                "title": r["channel_name"].lower().replace(" ", "-")[:40] + "-yt",
                "snippet": " ".join(snippet_parts),
                "discovered_via": "discovery_crawler",
                "discovered_at": today,
                "status": "discovered",
                "evaluation": None,
                "platform": "youtube",
                "source_slug": None,
            }
            candidates["candidates"].append(entry)
            existing_urls.add(url)
            added += 1
            print(f"  + {r['channel_name']} ({r['atm_count']} ATM)")

    if added:
        candidates["_meta"]["last_updated"] = today
        CANDIDATES_FILE.write_text(json.dumps(candidates, indent=2))
        print(f"\nExported {added} channels.")
    else:
        print("No new channels to export.")


def cmd_log(args):
    data = load_queue()
    events = data.get("events", [])

    if args.type:
        events = [e for e in events if e["type"] == args.type]
    if args.status:
        events = [e for e in events if e["status"] == args.status]

    # Sort by created_at descending
    events = sorted(events, key=lambda e: e.get("created_at", ""), reverse=True)
    events = events[:args.limit]

    for e in events:
        label = _event_label(e)
        spawned = f" → {len(e.get('spawned', []))} spawned" if e.get("spawned") else ""
        parent = f" (from {e['parent_id']})" if e.get("parent_id") else ""
        print(f"  [{e['id']}] {e['status']:10s} {e['type']:25s} {label}{spawned}{parent}")

        # Show key results
        if e.get("results") and args.verbose:
            r = e["results"]
            if e["type"] == "deep_scan_channel":
                print(f"           {r.get('total_videos',0)} videos, {r.get('atm_count',0)} ATM")
            elif e["type"] == "search_practitioner_yt":
                print(f"           {r.get('total_videos',0)} videos, {r.get('channels_found',0)} channels")
            elif e["type"] == "search_practitioner_web":
                print(f"           {r.get('total_results',0)} results: {r.get('by_platform', {})}")


def _event_label(event: dict) -> str:
    p = event.get("payload", {})
    t = event["type"]
    if t in ("search_practitioner_yt", "search_practitioner_web"):
        return p.get("name", "?")
    elif t == "deep_scan_channel":
        return p.get("channel_name") or p.get("channel_id", "?")
    elif t == "search_yt_query":
        return p.get("query", "?")
    elif t == "check_website":
        return p.get("url", "?")
    elif t == "scrape_contributors":
        n = len(p.get("names", []))
        return f"{p.get('source_name', '?')} ({n} names)" if n else p.get("url", "?")
    return str(p)[:60]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Discovery crawler for Feldenkrais lesson sources",
        prog="dc",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show queue status")

    p_proc = sub.add_parser("process", help="Process pending events")
    p_proc.add_argument("--limit", type=int, default=5)
    p_proc.add_argument("--type", choices=list(EVENT_TYPES.keys()))

    p_seed = sub.add_parser("seed", help="Add a seed event to the queue")
    p_seed.add_argument("seed_type", choices=["practitioner", "channel", "website", "query", "contributors"])
    p_seed.add_argument("value", help="Name, channel ID, URL, or query")
    p_seed.add_argument("--from", dest="source", help="Source attribution")
    p_seed.add_argument("--name", help="Display name (for channels)")

    p_batch = sub.add_parser("seed-batch", help="Seed multiple practitioners")
    p_batch.add_argument("--names", help="Comma-separated names")
    p_batch.add_argument("--file", help="File with one name per line")
    p_batch.add_argument("--from", dest="source", help="Source attribution")

    p_export = sub.add_parser("export", help="Export findings to source_candidates.json")

    p_log = sub.add_parser("log", help="Show event log")
    p_log.add_argument("--limit", type=int, default=20)
    p_log.add_argument("--type", choices=list(EVENT_TYPES.keys()))
    p_log.add_argument("--status", choices=["pending", "done", "failed"])
    p_log.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    cmd_map = {
        "status": cmd_status,
        "process": cmd_process,
        "seed": cmd_seed,
        "seed-batch": cmd_seed_batch,
        "export": cmd_export,
        "log": cmd_log,
    }
    fn = cmd_map.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
