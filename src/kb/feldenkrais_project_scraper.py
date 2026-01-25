"""
Scraper for feldenkraisproject.com (patron content).

Requires authentication via browser cookies. Cookies are loaded from:
  1. FP_COOKIES_FILE env var (path to JSON file)
  2. ~/.feldenkrais_cookies.json (default)

Cookie file format:
{
    "wordpress_logged_in_HASH": "value",
    "wordpress_sec_HASH": "value"
}

To get cookies:
  1. Log in at feldenkraisproject.com in Safari
  2. Dev Tools (Cmd+Opt+I) → Storage → Cookies
  3. Copy the wordpress_logged_in_* and wordpress_sec_* cookie values
"""

import json
import logging
import os
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

DEFAULT_COOKIES_PATH = Path.home() / ".feldenkrais_cookies.json"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

S3_BUCKET_URL = "https://feldenkraisproject.s3.us-east-2.amazonaws.com"


@dataclass
class FPLesson:
    """A lesson from feldenkraisproject.com."""
    url: str
    title: str
    audio_url: Optional[str] = None
    duration: Optional[str] = None
    filename: Optional[str] = None


def load_cookies(path: Optional[Path] = None) -> dict:
    """Load cookies from JSON file."""
    if path is None:
        path = Path(os.environ.get("FP_COOKIES_FILE", DEFAULT_COOKIES_PATH))

    if not path.exists():
        raise FileNotFoundError(
            f"Cookies file not found: {path}\n"
            "Create it with your wordpress_logged_in_* and wordpress_sec_* cookies."
        )

    with open(path) as f:
        cookies = json.load(f)

    # Validate we have the required cookies
    has_logged_in = any(k.startswith("wordpress_logged_in_") for k in cookies)
    if not has_logged_in:
        raise ValueError("Cookies file must contain wordpress_logged_in_* cookie")

    logger.info(f"Loaded {len(cookies)} cookies from {path}")
    return cookies


def get_session(cookies: dict) -> requests.Session:
    """Create a requests session with authentication cookies."""
    session = requests.Session()
    session.headers["User-Agent"] = DEFAULT_USER_AGENT
    session.cookies.update(cookies)
    return session


def extract_audio_url(html: str) -> Optional[str]:
    """Extract audio URL from Podlove player config in page HTML."""
    # Podlove uses escaped slashes in JSON
    # Pattern: "audio":[{"url":"https:\/\/...\.mp3",...}]
    pattern = r'"audio"\s*:\s*\[\s*\{\s*"url"\s*:\s*"([^"]+)"'
    match = re.search(pattern, html)

    if match:
        url = match.group(1)
        # Unescape forward slashes
        url = url.replace(r"\\/", "/").replace("\\/", "/")
        return url

    return None


def extract_page_title(soup: BeautifulSoup) -> str:
    """Extract lesson title from page."""
    # Try the main heading first
    h1 = soup.find("h1", class_="entry-title")
    if h1:
        title = h1.get_text(strip=True)
        # Remove "(Patrons)" suffix if present
        title = re.sub(r'\s*\(Patrons\)\s*$', '', title)
        return title

    # Fallback to og:title
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        return og_title["content"]

    # Fallback to <title>
    if soup.title and soup.title.string:
        return soup.title.string.strip()

    return "Untitled"


def extract_duration(soup: BeautifulSoup) -> Optional[str]:
    """Extract lesson duration from page metadata."""
    # Look for duration in various places
    duration_elem = soup.find(class_="lesson-duration")
    if duration_elem:
        return duration_elem.get_text(strip=True)

    # Try to find in meta or structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if "duration" in data:
                return data["duration"]
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def get_filename_from_url(url: str) -> str:
    """Extract filename from S3 URL."""
    parsed = urlparse(url)
    filename = unquote(Path(parsed.path).name)
    # Sanitize
    filename = re.sub(r'[^\w\-_.]', '_', filename)
    return filename


def scrape_lesson(session: requests.Session, url: str) -> FPLesson:
    """
    Scrape a single lesson page.

    Returns FPLesson with audio_url populated if accessible.
    Raises if page cannot be fetched.
    """
    logger.info(f"Fetching lesson: {url}")

    response = session.get(url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    title = extract_page_title(soup)
    duration = extract_duration(soup)

    # Check for paywall
    if "just for Patrons" in response.text or "just for Members" in response.text:
        logger.warning(f"Paywall detected for: {title}")
        return FPLesson(url=url, title=title, duration=duration)

    # Extract audio URL
    audio_url = extract_audio_url(response.text)

    if audio_url:
        filename = get_filename_from_url(audio_url)
        logger.info(f"Found audio: {filename}")
    else:
        filename = None
        logger.warning(f"No audio URL found for: {title}")

    return FPLesson(
        url=url,
        title=title,
        audio_url=audio_url,
        duration=duration,
        filename=filename,
    )


def download_audio(url: str, timeout: int = 300) -> bytes:
    """Download audio from S3 (no auth needed for S3 URLs)."""
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
    lesson: FPLesson,
    content: bytes,
    source_id: int,
    type_id: int,
    tags: Optional[list[str]] = None,
) -> str:
    """
    Stage a lesson's audio file with sidecar JSON.

    Returns the staging key.
    """
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
            "source": "feldenkraisproject.com",
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
    cookies: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    dry_run: bool = False,
) -> dict:
    """
    Scrape a lesson URL and stage the audio.

    Args:
        url: Lesson page URL
        source_id: Document source enum ID
        type_id: Document type enum ID
        storage: Storage instance (required unless dry_run)
        cookies: Auth cookies dict (loads from file if not provided)
        tags: Tags to apply
        dry_run: If True, just extract info without downloading/staging

    Returns dict with results.
    """
    results = {
        "url": url,
        "staged": [],
        "errors": [],
    }

    try:
        # Load cookies if not provided
        if cookies is None:
            cookies = load_cookies()

        session = get_session(cookies)
        lesson = scrape_lesson(session, url)

        results["title"] = lesson.title
        results["audio_url"] = lesson.audio_url
        results["duration"] = lesson.duration

        if not lesson.audio_url:
            results["errors"].append({
                "error": "No audio URL found (may require higher access level)"
            })
            return results

        if dry_run:
            results["filename"] = lesson.filename
            return results

        if not storage:
            raise ValueError("storage required when not in dry_run mode")

        # Download and stage
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


def scrape_multiple(
    urls: list[str],
    source_id: int,
    type_id: int,
    storage: Optional[Storage] = None,
    cookies: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    dry_run: bool = False,
) -> list[dict]:
    """Scrape multiple lesson URLs."""
    if cookies is None:
        cookies = load_cookies()

    results = []
    for url in urls:
        result = scrape(
            url=url,
            source_id=source_id,
            type_id=type_id,
            storage=storage,
            cookies=cookies,
            tags=tags,
            dry_run=dry_run,
        )
        results.append(result)

    return results


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(
        description="Scrape audio from feldenkraisproject.com"
    )
    parser.add_argument("url", help="Lesson URL to scrape")
    parser.add_argument("--source-id", type=int, default=1, help="Source ID")
    parser.add_argument("--type-id", type=int, default=2, help="Type ID (2=audio)")
    parser.add_argument("--tags", help="Comma-separated tags")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract info without downloading/staging")
    parser.add_argument("--bucket", help="S3 bucket (overrides KB_BUCKET)")
    parser.add_argument("--cookies-file", help="Path to cookies JSON file")

    args = parser.parse_args()

    tags = args.tags.split(",") if args.tags else []

    # Load cookies
    cookies_path = Path(args.cookies_file) if args.cookies_file else None
    cookies = load_cookies(cookies_path)

    if args.dry_run:
        results = scrape(
            url=args.url,
            source_id=args.source_id,
            type_id=args.type_id,
            cookies=cookies,
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
            cookies=cookies,
            tags=tags,
            dry_run=False,
        )

    print(json.dumps(results, indent=2))
