"""
Scraper for Feldenkrais UK podbean.com episodes (public content, no auth needed).

Usage:
    # Dry run single episode:
    uv run python src/kb/podbean_scraper.py --dry-run "URL"

    # Stage single episode:
    uv run python src/kb/podbean_scraper.py "URL"

    # Stage all episodes from the podcast (skips already-ingested):
    uv run python src/kb/podbean_scraper.py --all

    # Dry run all (list episodes without downloading):
    uv run python src/kb/podbean_scraper.py --all --dry-run
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, unquote

import requests
from bs4 import BeautifulSoup

try:
    from config import Config
    from storage import Storage
except ImportError:
    from .config import Config
    from .storage import Storage

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PODBEAN_BASE_URL = "https://feldenkraisuk.podbean.com"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class PodbeanLesson:
    """A lesson from podbean.com."""
    url: str
    title: str
    audio_url: Optional[str] = None
    duration: Optional[str] = None
    filename: Optional[str] = None


def extract_audio_url(soup: BeautifulSoup) -> Optional[str]:
    """Extract audio URL from podbean page."""
    # Try <audio> tag first
    audio = soup.find("audio")
    if audio:
        src = audio.get("src")
        if src:
            return src
        source = audio.find("source")
        if source and source.get("src"):
            return source["src"]

    # Try og:audio meta tag
    og_audio = soup.find("meta", property="og:audio")
    if og_audio and og_audio.get("content"):
        return og_audio["content"]

    # Try data attributes on player elements
    for elem in soup.find_all(attrs={"data-uri": True}):
        uri = elem["data-uri"]
        if uri.endswith((".mp3", ".m4a", ".ogg", ".wav")):
            return uri

    # Try mcdn.podbean.com links in scripts
    for script in soup.find_all("script"):
        if script.string and "mcdn.podbean.com" in (script.string or ""):
            match = re.search(r'(https?://mcdn\.podbean\.com/[^\s"\']+\.m4a)', script.string)
            if match:
                return match.group(1)
            match = re.search(r'(https?://mcdn\.podbean\.com/[^\s"\']+\.mp3)', script.string)
            if match:
                return match.group(1)

    # Try any direct link to mcdn.podbean.com
    for a in soup.find_all("a", href=True):
        if "mcdn.podbean.com" in a["href"]:
            return a["href"]

    return None


def extract_page_title(soup: BeautifulSoup) -> str:
    """Extract lesson title from page."""
    # Try og:title
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        return og_title["content"].strip()

    # Try h1
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)

    # Fallback to <title>
    if soup.title and soup.title.string:
        return soup.title.string.strip()

    return "Untitled"


def extract_duration(soup: BeautifulSoup) -> Optional[str]:
    """Extract duration from page metadata."""
    # Try meta duration
    meta_dur = soup.find("meta", attrs={"name": "duration"})
    if meta_dur and meta_dur.get("content"):
        return meta_dur["content"]

    # Try ld+json
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and "duration" in data:
                return data["duration"]
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def get_filename_from_url(url: str) -> str:
    """Extract filename from audio URL."""
    parsed = urlparse(url)
    filename = unquote(Path(parsed.path).name)
    filename = re.sub(r'[^\w\-_.]', '_', filename)
    return filename


def scrape_lesson(url: str) -> PodbeanLesson:
    """Scrape a single podbean episode page."""
    logger.info(f"Fetching lesson: {url}")

    response = requests.get(
        url,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    title = extract_page_title(soup)
    duration = extract_duration(soup)
    audio_url = extract_audio_url(soup)

    if audio_url:
        filename = get_filename_from_url(audio_url)
        logger.info(f"Found audio: {filename}")
    else:
        filename = None
        logger.warning(f"No audio URL found for: {title}")

    return PodbeanLesson(
        url=url,
        title=title,
        audio_url=audio_url,
        duration=duration,
        filename=filename,
    )


def download_audio(url: str, timeout: int = 300) -> bytes:
    """Download audio file."""
    logger.info(f"Downloading: {url}")

    response = requests.get(
        url,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()

    content = response.content
    logger.info(f"Downloaded {len(content) / 1024 / 1024:.1f} MB")
    return content


def stage_lesson(
    storage: Storage,
    lesson: PodbeanLesson,
    content: bytes,
    source_id: int,
    type_id: int,
    tags: Optional[list[str]] = None,
) -> str:
    """Stage a lesson's audio file with sidecar JSON."""
    staging_key = f"staging/{lesson.filename}"
    sidecar_key = f"{staging_key}.json"

    sidecar = {
        "title": lesson.title,
        "source_url": lesson.url,
        "source_id": source_id,
        "type_id": type_id,
        "tags": tags or [],
        "scraper_metadata": {
            "audio_url": lesson.audio_url,
            "original_filename": lesson.filename,
            "duration": lesson.duration,
            "file_size": len(content),
            "source": "feldenkraisuk.podbean.com",
        }
    }

    logger.info(f"Staging: {staging_key} ({len(content) / 1024 / 1024:.1f} MB)")
    storage.put_object(sidecar_key, json.dumps(sidecar, indent=2).encode())
    storage.put_object(staging_key, content)

    return staging_key


def scrape(
    url: str,
    source_id: int,
    type_id: int,
    storage: Optional[Storage] = None,
    tags: Optional[list[str]] = None,
    dry_run: bool = False,
) -> dict:
    """
    Scrape a podbean episode URL and stage the audio.

    Returns dict with results.
    """
    results = {
        "url": url,
        "staged": [],
        "errors": [],
    }

    try:
        lesson = scrape_lesson(url)

        results["title"] = lesson.title
        results["audio_url"] = lesson.audio_url
        results["duration"] = lesson.duration

        if not lesson.audio_url:
            results["errors"].append({
                "error": "No audio URL found on page"
            })
            return results

        if dry_run:
            results["filename"] = lesson.filename
            return results

        if not storage:
            raise ValueError("storage required when not in dry_run mode")

        content = download_audio(lesson.audio_url)
        staging_key = stage_lesson(
            storage=storage,
            lesson=lesson,
            content=content,
            source_id=source_id,
            type_id=type_id,
            tags=tags,
        )

        results["staged"].append({
            "filename": lesson.filename,
            "staging_key": staging_key,
            "size": len(content),
            "title": lesson.title,
        })

    except Exception as e:
        logger.error(f"Failed to scrape {url}: {e}")
        results["errors"].append({"error": str(e)})

    return results


def get_existing_source_urls(source_id: int) -> set[str]:
    """Get source_urls already in the document table for this source."""
    import os
    try:
        import psycopg2
    except ImportError:
        logger.warning("psycopg2 not available, cannot check for duplicates")
        return set()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.warning("DATABASE_URL not set, cannot check for duplicates")
        return set()

    conn = psycopg2.connect(database_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT source_url FROM document WHERE source_id = %s AND source_url IS NOT NULL",
        (source_id,)
    )
    urls = {row[0] for row in cur.fetchall()}
    conn.close()
    logger.info(f"Found {len(urls)} existing documents for source_id={source_id}")
    return urls


def crawl_episode_urls(base_url: str = PODBEAN_BASE_URL) -> list[str]:
    """Crawl all pages of the podcast and return episode URLs."""
    episode_urls = []
    page = 1

    while True:
        url = f"{base_url}/page/{page}"
        logger.info(f"Crawling page {page}: {url}")

        response = requests.get(
            url,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=30,
        )

        if response.status_code == 404:
            break
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Find episode links — they match /e/slug/
        found = 0
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Normalize to full URL
            if href.startswith("/e/"):
                href = base_url + href
            if "/e/" in href and base_url in href:
                # Deduplicate
                if href not in episode_urls:
                    episode_urls.append(href)
                    found += 1

        logger.info(f"Page {page}: found {found} new episodes")

        if found == 0:
            break

        page += 1
        time.sleep(0.5)  # Be polite

    logger.info(f"Total episodes found: {len(episode_urls)}")
    return episode_urls


def scrape_all(
    source_id: int = 5,
    type_id: int = 2,
    storage: Optional[Storage] = None,
    tags: Optional[list[str]] = None,
    dry_run: bool = False,
    base_url: str = PODBEAN_BASE_URL,
) -> dict:
    """
    Crawl all episodes and stage them.

    Skips episodes already in the DB (by source_url).
    """
    episode_urls = crawl_episode_urls(base_url)
    existing_urls = get_existing_source_urls(source_id)

    # Filter out already-ingested
    new_urls = [u for u in episode_urls if u not in existing_urls]
    skipped = len(episode_urls) - len(new_urls)
    logger.info(f"Skipping {skipped} already-ingested episodes, {len(new_urls)} to process")

    summary = {
        "total_found": len(episode_urls),
        "skipped_existing": skipped,
        "to_process": len(new_urls),
        "staged": [],
        "errors": [],
    }

    if dry_run:
        summary["urls"] = new_urls
        return summary

    for i, url in enumerate(new_urls):
        logger.info(f"[{i+1}/{len(new_urls)}] Processing: {url}")
        result = scrape(
            url=url,
            source_id=source_id,
            type_id=type_id,
            storage=storage,
            tags=tags,
            dry_run=False,
        )
        summary["staged"].extend(result.get("staged", []))
        summary["errors"].extend(result.get("errors", []))

        # Brief pause between downloads
        if i < len(new_urls) - 1:
            time.sleep(1)

    logger.info(
        f"Done: {len(summary['staged'])} staged, {len(summary['errors'])} errors"
    )
    return summary


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(
        description="Scrape audio from podbean.com episodes"
    )
    parser.add_argument("url", nargs="?", help="Episode URL to scrape")
    parser.add_argument("--all", action="store_true",
                        help="Crawl and stage all episodes (skips already-ingested)")
    parser.add_argument("--source-id", type=int, default=5, help="Source ID (5=FELDENKRAIS_UK)")
    parser.add_argument("--type-id", type=int, default=2, help="Type ID (2=audio)")
    parser.add_argument("--tags", help="Comma-separated tags")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract info without downloading/staging")
    parser.add_argument("--bucket", help="S3 bucket (overrides KB_BUCKET)")

    args = parser.parse_args()

    if not args.all and not args.url:
        parser.error("either provide a URL or use --all")

    tags = args.tags.split(",") if args.tags else []

    from dotenv import load_dotenv
    load_dotenv()

    if args.all:
        if args.dry_run:
            results = scrape_all(
                source_id=args.source_id,
                type_id=args.type_id,
                tags=tags,
                dry_run=True,
            )
        else:
            config = Config.from_env()
            bucket = args.bucket or config.bucket
            if not bucket:
                print("Error: bucket required (--bucket or KB_BUCKET env var)")
                exit(1)
            storage = Storage(bucket)
            results = scrape_all(
                source_id=args.source_id,
                type_id=args.type_id,
                storage=storage,
                tags=tags,
                dry_run=False,
            )
    elif args.dry_run:
        results = scrape(
            url=args.url,
            source_id=args.source_id,
            type_id=args.type_id,
            tags=tags,
            dry_run=True,
        )
    else:
        config = Config.from_env()
        bucket = args.bucket or config.bucket
        if not bucket:
            print("Error: bucket required (--bucket or KB_BUCKET env var)")
            exit(1)

        storage = Storage(bucket)
        results = scrape(
            url=args.url,
            source_id=args.source_id,
            type_id=args.type_id,
            storage=storage,
            tags=tags,
            dry_run=False,
        )

    print(json.dumps(results, indent=2))
