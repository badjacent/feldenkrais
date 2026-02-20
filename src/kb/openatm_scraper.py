"""
Scraper for openatm.org ATM lessons (public content, no auth needed).

The site has a single DataTables page with all lessons in a table.
Each row has: author, date, title, source (AY# etc), duration, download link, notes.

Usage:
    # Dry run (list all lessons, no download):
    uv run python src/kb/openatm_scraper.py --dry-run

    # Stage all lessons to S3 (skips already-ingested):
    uv run python src/kb/openatm_scraper.py

    # Stage a single lesson by URL:
    uv run python src/kb/openatm_scraper.py --url "https://openatm.org/atm/sharon1998/sharon_runner1_1998.mp3"
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
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

OPENATM_URL = "https://openatm.org"
CURATED_PAGES = [
    ("https://www.openatm.org/curated/olena.html", "Olena Nitefor"),
    ("https://www.openatm.org/curated/donna.html", "Donna Wood"),
]
DEFAULT_SOURCE_ID = 6  # OPENATM
DEFAULT_TYPE_ID = 2    # AUDIO

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class OpenATMLesson:
    """A lesson from openatm.org."""
    audio_url: str
    title: str
    author: str
    date: Optional[str] = None
    source_ref: Optional[str] = None
    duration: Optional[str] = None
    notes: Optional[str] = None
    filename: Optional[str] = None
    page_url: str = OPENATM_URL


def get_filename_from_url(url: str) -> str:
    """Extract filename from audio URL."""
    parsed = urlparse(url)
    filename = unquote(Path(parsed.path).name)
    filename = re.sub(r'[^\w\-_.]', '_', filename)
    return filename


def crawl_lessons() -> list[OpenATMLesson]:
    """Crawl the openatm.org main page and extract all lessons from the table."""
    logger.info(f"Fetching {OPENATM_URL}")

    response = requests.get(
        OPENATM_URL,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    lessons = []

    # Find all tables on the page
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            # openatm.org uses <th> for data cells, not <td>
            cells = tr.find_all("td") or tr.find_all("th")
            if len(cells) < 6:
                continue

            # Find download link in the expected column (index 5)
            link = cells[5].find("a", href=True) if len(cells) > 5 else None
            if not link:
                for cell in cells:
                    a = cell.find("a", href=True)
                    if a and a["href"].endswith((".mp3", ".m4a", ".ogg", ".wav")):
                        link = a
                        break

            if not link:
                continue

            audio_url = link["href"]
            if not audio_url.startswith("http"):
                audio_url = OPENATM_URL + "/" + audio_url.lstrip("/")

            # Skip non-audio links
            if not any(audio_url.endswith(ext) for ext in (".mp3", ".m4a", ".ogg", ".wav")):
                continue

            author = cells[0].get_text(strip=True) if len(cells) > 0 else ""
            date = cells[1].get_text(strip=True) if len(cells) > 1 else None
            title = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            source_ref = cells[3].get_text(strip=True) if len(cells) > 3 else None
            duration = cells[4].get_text(strip=True) if len(cells) > 4 else None
            notes = cells[6].get_text(strip=True) if len(cells) > 6 else None

            # Clean up dash-only values
            if source_ref in ("-", "–", "—", ""):
                source_ref = None
            if date in ("-", "–", "—", ""):
                date = None
            if notes in ("-", "–", "—", ""):
                notes = None

            filename = get_filename_from_url(audio_url)

            lessons.append(OpenATMLesson(
                audio_url=audio_url,
                title=title or filename,
                author=author,
                date=date,
                source_ref=source_ref,
                duration=duration,
                notes=notes,
                filename=filename,
            ))

    logger.info(f"Found {len(lessons)} lessons")
    return lessons


def crawl_curated_lessons() -> list[OpenATMLesson]:
    """Crawl the curated teacher pages and extract all lessons."""
    lessons = []

    for page_url, teacher_name in CURATED_PAGES:
        logger.info(f"Fetching curated page: {page_url}")
        response = requests.get(
            page_url,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = tr.find_all("td")
                if not cells:
                    continue

                # Find download link
                link = None
                for cell in cells:
                    a = cell.find("a", href=True)
                    if a and a["href"].endswith((".mp3", ".m4a", ".ogg", ".wav")):
                        link = a
                        break

                if not link:
                    continue

                audio_url = link["href"]
                if not audio_url.startswith("http"):
                    audio_url = OPENATM_URL + "/" + audio_url.lstrip("/")

                # Curated tables: Title | Source | Time | Download
                # Some tables have extra leading columns (row number or date),
                # so find the download column index and work backwards
                download_idx = next(
                    (i for i, c in enumerate(cells) if c.find("a", href=True)), -1
                )
                if download_idx >= 3:
                    # Standard layout: columns before download are Title, Source, Time
                    title = cells[download_idx - 3].get_text(strip=True)
                    source_ref = cells[download_idx - 2].get_text(strip=True)
                    duration = cells[download_idx - 1].get_text(strip=True)
                else:
                    title = cells[0].get_text(strip=True) if len(cells) > 0 else ""
                    source_ref = cells[1].get_text(strip=True) if len(cells) > 1 else None
                    duration = cells[2].get_text(strip=True) if len(cells) > 2 else None

                # Clean up placeholder values
                if source_ref in ("-", "–", "—", "--", ""):
                    source_ref = None
                if duration in ("-", "–", "—", "--", ""):
                    duration = None

                # Extract AY references from title if present (e.g. "AY#52" in filename)
                if not source_ref:
                    ay_match = re.search(r'AY#?\d+', title) or re.search(r'AY#?\d+', audio_url)
                    if ay_match:
                        source_ref = ay_match.group()

                filename = get_filename_from_url(audio_url)

                lessons.append(OpenATMLesson(
                    audio_url=audio_url,
                    title=title or filename,
                    author=teacher_name,
                    source_ref=source_ref,
                    duration=duration,
                    notes=None,
                    filename=filename,
                    page_url=page_url,
                ))

    logger.info(f"Found {len(lessons)} curated lessons")
    return lessons


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
    lesson: OpenATMLesson,
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
        "source_url": lesson.audio_url,
        "source_id": source_id,
        "type_id": type_id,
        "tags": tags or [],
        "scraper_metadata": {
            "audio_url": lesson.audio_url,
            "original_filename": lesson.filename,
            "duration": lesson.duration,
            "author": lesson.author,
            "source_ref": lesson.source_ref,
            "date": lesson.date,
            "notes": lesson.notes,
            "source": "openatm.org",
        }
    }

    logger.info(f"Staging: {staging_key} ({len(content) / 1024 / 1024:.1f} MB)")
    storage.put_object(sidecar_key, json.dumps(sidecar, indent=2).encode())
    storage.put_object(staging_key, content)

    return staging_key


def scrape_all(
    source_id: int = DEFAULT_SOURCE_ID,
    type_id: int = DEFAULT_TYPE_ID,
    storage: Optional[Storage] = None,
    tags: Optional[list[str]] = None,
    dry_run: bool = False,
    curated: bool = False,
) -> dict:
    """Crawl all lessons and stage them. Skips already-ingested."""
    lessons = crawl_curated_lessons() if curated else crawl_lessons()
    existing_urls = get_existing_source_urls(source_id)

    new_lessons = [l for l in lessons if l.audio_url not in existing_urls]
    skipped = len(lessons) - len(new_lessons)
    logger.info(f"Skipping {skipped} already-ingested, {len(new_lessons)} to process")

    summary = {
        "total_found": len(lessons),
        "skipped_existing": skipped,
        "to_process": len(new_lessons),
        "staged": [],
        "errors": [],
    }

    if dry_run:
        summary["lessons"] = [
            {
                "title": l.title,
                "author": l.author,
                "source_ref": l.source_ref,
                "duration": l.duration,
                "audio_url": l.audio_url,
            }
            for l in new_lessons
        ]
        return summary

    if not storage:
        raise ValueError("storage required when not in dry_run mode")

    for i, lesson in enumerate(new_lessons):
        logger.info(f"[{i+1}/{len(new_lessons)}] {lesson.author} - {lesson.title}")
        try:
            content = download_audio(lesson.audio_url)
            staging_key = stage_lesson(
                storage=storage,
                lesson=lesson,
                content=content,
                source_id=source_id,
                type_id=type_id,
                tags=tags,
            )
            summary["staged"].append({
                "filename": lesson.filename,
                "staging_key": staging_key,
                "size": len(content),
                "title": lesson.title,
                "author": lesson.author,
            })
        except Exception as e:
            logger.error(f"Failed: {lesson.audio_url}: {e}")
            summary["errors"].append({
                "url": lesson.audio_url,
                "title": lesson.title,
                "error": str(e),
            })

        if i < len(new_lessons) - 1:
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
        description="Scrape audio from openatm.org"
    )
    parser.add_argument("--url", help="Single audio URL to stage")
    parser.add_argument("--source-id", type=int, default=DEFAULT_SOURCE_ID,
                        help="Source ID (6=OPENATM)")
    parser.add_argument("--type-id", type=int, default=DEFAULT_TYPE_ID,
                        help="Type ID (2=audio)")
    parser.add_argument("--tags", help="Comma-separated tags")
    parser.add_argument("--dry-run", action="store_true",
                        help="List lessons without downloading/staging")
    parser.add_argument("--curated", action="store_true",
                        help="Crawl curated teacher pages instead of main table")
    parser.add_argument("--bucket", help="S3 bucket (overrides KB_BUCKET)")

    args = parser.parse_args()
    tags = args.tags.split(",") if args.tags else []

    from dotenv import load_dotenv
    load_dotenv()

    if args.dry_run:
        results = scrape_all(
            source_id=args.source_id,
            type_id=args.type_id,
            tags=tags,
            dry_run=True,
            curated=args.curated,
        )
    elif args.url:
        # Single URL mode — create a minimal lesson and stage it
        config = Config.from_env()
        bucket = args.bucket or config.bucket
        if not bucket:
            print("Error: bucket required (--bucket or KB_BUCKET env var)")
            exit(1)
        storage = Storage(bucket)
        filename = get_filename_from_url(args.url)
        lesson = OpenATMLesson(
            audio_url=args.url,
            title=filename,
            author="Unknown",
            filename=filename,
        )
        content = download_audio(args.url)
        staging_key = stage_lesson(
            storage=storage,
            lesson=lesson,
            content=content,
            source_id=args.source_id,
            type_id=args.type_id,
            tags=tags,
        )
        results = {"staged": staging_key, "size": len(content)}
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
            curated=args.curated,
        )

    print(json.dumps(results, indent=2))
