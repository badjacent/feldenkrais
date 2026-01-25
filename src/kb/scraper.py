"""
Scraper: discovers and stages media files from web pages.

Supports:
- Squarespace audio blocks (sqs-audio-embed)
- Direct audio/video links
- HTML5 audio/video tags
"""

import json
import logging
import re
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

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class MediaFile:
    """A discovered media file."""
    url: str
    filename: str
    title: Optional[str] = None
    media_type: str = "audio"  # "audio" or "video"


def get_filename_from_url(url: str) -> str:
    """Extract and sanitize filename from URL."""
    parsed = urlparse(url)
    path = unquote(parsed.path)

    # Handle Squarespace pattern: .../filename.mp3/original/filename.mp3
    if "/original/" in path:
        filename = path.split("/original/")[-1]
    else:
        filename = Path(path).name

    # Sanitize: keep only alphanumeric, dash, underscore, dot
    filename = re.sub(r'[^\w\-_.]', '_', filename)
    return filename


def extract_page_title(soup: BeautifulSoup) -> Optional[str]:
    """Extract title from page."""
    # Try og:title first
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        return og_title["content"]

    # Try <title>
    if soup.title and soup.title.string:
        return soup.title.string.strip()

    # Try <h1>
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)

    return None


def find_squarespace_audio(soup: BeautifulSoup) -> list[MediaFile]:
    """Find Squarespace audio embeds."""
    media = []

    # Look for sqs-audio-embed elements
    for embed in soup.find_all(class_="sqs-audio-embed"):
        # Try various data attributes
        url = (
            embed.get("data-url") or
            embed.get("data-src") or
            embed.get("data-audio-url")
        )

        if not url:
            # Check for nested source or data in JSON
            data_json = embed.get("data-block-json")
            if data_json:
                try:
                    data = json.loads(data_json)
                    url = data.get("url") or data.get("audioUrl")
                except json.JSONDecodeError:
                    pass

        if url:
            filename = get_filename_from_url(url)
            title = embed.get("data-title") or embed.get("data-name")
            media.append(MediaFile(url=url, filename=filename, title=title))

    return media


def find_html5_media(soup: BeautifulSoup) -> list[MediaFile]:
    """Find HTML5 audio and video tags."""
    media = []

    # Audio tags
    for audio in soup.find_all("audio"):
        src = audio.get("src")
        if src:
            media.append(MediaFile(
                url=src,
                filename=get_filename_from_url(src),
                media_type="audio"
            ))
        # Check source children
        for source in audio.find_all("source"):
            src = source.get("src")
            if src:
                media.append(MediaFile(
                    url=src,
                    filename=get_filename_from_url(src),
                    media_type="audio"
                ))

    # Video tags
    for video in soup.find_all("video"):
        src = video.get("src")
        if src:
            media.append(MediaFile(
                url=src,
                filename=get_filename_from_url(src),
                media_type="video"
            ))
        for source in video.find_all("source"):
            src = source.get("src")
            if src:
                media.append(MediaFile(
                    url=src,
                    filename=get_filename_from_url(src),
                    media_type="video"
                ))

    return media


def find_direct_links(soup: BeautifulSoup, base_url: str) -> list[MediaFile]:
    """Find direct links to media files."""
    media = []

    for a in soup.find_all("a", href=True):
        href = a["href"]

        # Check if it's a media file
        parsed = urlparse(href)
        ext = Path(parsed.path).suffix.lower()

        if ext in MEDIA_EXTENSIONS:
            # Make absolute URL if relative
            if not parsed.scheme:
                base_parsed = urlparse(base_url)
                href = f"{base_parsed.scheme}://{base_parsed.netloc}{href}"

            media_type = "audio" if ext in AUDIO_EXTENSIONS else "video"
            media.append(MediaFile(
                url=href,
                filename=get_filename_from_url(href),
                media_type=media_type,
                title=a.get_text(strip=True) or None
            ))

    return media


def scrape_page(url: str, user_agent: str = DEFAULT_USER_AGENT) -> tuple[list[MediaFile], str]:
    """
    Scrape a page for media files.

    Returns (list of MediaFile, page_title).
    """
    logger.info(f"Fetching: {url}")

    response = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    page_title = extract_page_title(soup) or "Untitled"

    # Find media from various sources
    media = []
    media.extend(find_squarespace_audio(soup))
    media.extend(find_html5_media(soup))
    media.extend(find_direct_links(soup, url))

    # Deduplicate by URL
    seen_urls = set()
    unique_media = []
    for m in media:
        if m.url not in seen_urls:
            seen_urls.add(m.url)
            unique_media.append(m)

    logger.info(f"Found {len(unique_media)} media files")
    return unique_media, page_title


def download_media(
    media: MediaFile,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = 300
) -> bytes:
    """Download a media file and return its content."""
    logger.info(f"Downloading: {media.filename}")

    response = requests.get(
        media.url,
        headers={"User-Agent": user_agent},
        timeout=timeout,
        stream=True
    )
    response.raise_for_status()

    # Read content
    content = response.content
    logger.info(f"Downloaded {len(content)} bytes")
    return content


def stage_media(
    storage: Storage,
    media: MediaFile,
    content: bytes,
    source_url: str,
    page_title: str,
    source_id: int,
    type_id: int,
    tags: Optional[list[str]] = None,
) -> str:
    """
    Stage a media file with its sidecar JSON.

    Returns the staging key.
    """
    staging_key = f"staging/{media.filename}"
    sidecar_key = f"{staging_key}.json"

    # Determine title
    title = media.title or page_title

    # Create sidecar
    sidecar = {
        "title": title,
        "source_url": source_url,
        "source_id": source_id,
        "type_id": type_id,
        "tags": tags or [],
        "scraper_metadata": {
            "media_url": media.url,
            "original_filename": media.filename,
            "page_title": page_title,
        }
    }

    # Upload sidecar first, then media
    logger.info(f"Staging: {staging_key}")
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
    Scrape a URL for media and stage files.

    Args:
        url: Page to scrape
        source_id: Document source enum ID
        type_id: Document type enum ID
        storage: Storage instance (required unless dry_run)
        tags: Tags to apply to all media
        dry_run: If True, just list media without downloading/staging

    Returns dict with staged files and any errors.
    """
    results = {
        "url": url,
        "staged": [],
        "errors": [],
    }

    try:
        media_files, page_title = scrape_page(url)
        results["page_title"] = page_title
        results["media_found"] = len(media_files)

        if dry_run:
            results["media"] = [
                {"url": m.url, "filename": m.filename, "title": m.title}
                for m in media_files
            ]
            return results

        if not storage:
            raise ValueError("storage required when not in dry_run mode")

        for media in media_files:
            try:
                content = download_media(media)
                staging_key = stage_media(
                    storage=storage,
                    media=media,
                    content=content,
                    source_url=url,
                    page_title=page_title,
                    source_id=source_id,
                    type_id=type_id,
                    tags=tags,
                )
                results["staged"].append({
                    "filename": media.filename,
                    "staging_key": staging_key,
                    "title": media.title or page_title,
                })
            except Exception as e:
                logger.error(f"Failed to process {media.filename}: {e}")
                results["errors"].append({
                    "filename": media.filename,
                    "url": media.url,
                    "error": str(e),
                })

    except Exception as e:
        logger.error(f"Failed to scrape {url}: {e}")
        results["errors"].append({"error": str(e)})

    return results


def handler(event, context):
    """Lambda handler for scraping."""
    logger.info(f"Received event: {json.dumps(event)}")

    url = event.get("url")
    source_id = event.get("source_id", 1)
    type_id = event.get("type_id", 2)  # Default to AUDIO
    tags = event.get("tags", [])
    dry_run = event.get("dry_run", False)

    if not url:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "url is required"})
        }

    config = Config.from_env()
    storage = None if dry_run else Storage(config.bucket)

    results = scrape(
        url=url,
        source_id=source_id,
        type_id=type_id,
        storage=storage,
        tags=tags,
        dry_run=dry_run,
    )

    return {
        "statusCode": 200,
        "body": json.dumps(results),
    }


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="Scrape media from a URL")
    parser.add_argument("url", help="URL to scrape")
    parser.add_argument("--source-id", type=int, default=1, help="Source ID")
    parser.add_argument("--type-id", type=int, default=2, help="Type ID (2=audio)")
    parser.add_argument("--tags", help="Comma-separated tags")
    parser.add_argument("--dry-run", action="store_true", help="List media without staging")
    parser.add_argument("--bucket", help="S3 bucket (overrides KB_BUCKET)")

    args = parser.parse_args()

    tags = args.tags.split(",") if args.tags else []

    if args.dry_run:
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
