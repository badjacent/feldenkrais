#!/usr/bin/env python3
"""
Scrape Alexander Yanai lesson data from feldynotebook.com.

Scrapes volume listing pages for lesson numbers/titles/page numbers,
then scrapes individual lesson pages for outlines, synopses, tags, etc.
Stores everything in the ay_lesson / ay_lesson_tag / ay_lesson_theme tables.

Usage:
    .venv/bin/python src/kb/ingest/scrape_feldy_notebook.py                    # full run
    .venv/bin/python src/kb/ingest/scrape_feldy_notebook.py --volumes-only     # just scrape volume listings
    .venv/bin/python src/kb/ingest/scrape_feldy_notebook.py --dry-run          # scrape but don't write to DB
    .venv/bin/python src/kb/ingest/scrape_feldy_notebook.py --volume 1         # scrape only vol 1
    .venv/bin/python src/kb/ingest/scrape_feldy_notebook.py --limit 5          # only scrape 5 lesson pages
    .venv/bin/python src/kb/ingest/scrape_feldy_notebook.py --delay 2.0        # seconds between requests
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

# Add src/kb to path for infrastructure imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

from database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://feldynotebook.com"
VOLUME_URL_TEMPLATE = BASE_URL + "/alexander-yanai-vol-{n}/"
NUM_VOLUMES = 11
REQUEST_DELAY = 2.0  # seconds between requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Known section headings (normalized lowercase) -> field names
SECTION_MAP = {
    "source": "source_info",
    "synopsis": "synopsis",
    "lesson outline": "outline",
    "focus of moshe\u2019s teaching": "teaching_focus",
    "focus of moshe's teaching": "teaching_focus",
    "focus of the teaching": "teaching_focus",
    "related atms": "related_atms",
    "resources": "resources",
}

# Sections we skip (user-contributed content, not stable)
SKIP_SECTIONS = {"share your insights", "discussion"}

# Schema for the AY tables
AY_SCHEMA = """
CREATE TABLE IF NOT EXISTS ay_lesson (
    id SERIAL PRIMARY KEY,
    ay_number TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    iff_page INTEGER,
    volume INTEGER,
    source_info TEXT,
    synopsis TEXT,
    outline TEXT,
    teaching_focus TEXT,
    source_url TEXT,
    extras JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ay_lesson_tag (
    lesson_id INTEGER REFERENCES ay_lesson(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (lesson_id, tag)
);

CREATE TABLE IF NOT EXISTS ay_lesson_theme (
    lesson_id INTEGER REFERENCES ay_lesson(id) ON DELETE CASCADE,
    theme TEXT NOT NULL,
    PRIMARY KEY (lesson_id, theme)
);

CREATE TABLE IF NOT EXISTS ay_related_lesson (
    lesson_id INTEGER REFERENCES ay_lesson(id) ON DELETE CASCADE,
    related_ay_number TEXT,
    related_title TEXT,
    commentary TEXT
);
"""


def _get(url: str, session: requests.Session, max_retries: int = 3) -> BeautifulSoup:
    """Fetch a URL and return parsed BeautifulSoup, with retry on connection errors."""
    for attempt in range(max_retries):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5  # 5s, 10s, 15s
                logger.warning(f"  Connection error (attempt {attempt + 1}), retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                raise


def _extract_text_from_element(el) -> str:
    """Extract text from an element, joining list items with newlines."""
    if el is None:
        return ""
    if el.name in ("ul", "ol"):
        items = []
        for li in el.find_all("li", recursive=False):
            text = li.get_text(separator=" ", strip=True)
            # Check for nested lists
            nested = li.find(["ul", "ol"])
            if nested:
                # Get the li text without nested list
                li_copy = li.__copy__() if hasattr(li, '__copy__') else li
                for n in li.find_all(["ul", "ol"]):
                    n.extract()
                text = li.get_text(separator=" ", strip=True)
                # Get nested items
                for sub_li in nested.find_all("li"):
                    items.append("  - " + sub_li.get_text(separator=" ", strip=True))
            items.append("- " + text if text else "")
        return "\n".join(items).strip()
    return el.get_text(separator=" ", strip=True)


def _collect_section_content(heading: Tag) -> str:
    """Collect all content between this heading and the next heading."""
    parts = []
    for sibling in heading.next_siblings:
        if isinstance(sibling, Tag):
            if sibling.name == "h2":
                break
            parts.append(_extract_text_from_element(sibling))
        elif isinstance(sibling, str) and sibling.strip():
            parts.append(sibling.strip())
    return "\n".join(p for p in parts if p).strip()


def _is_placeholder(text: str) -> bool:
    """Check if text is a placeholder/template rather than real content."""
    placeholders = [
        "write a couple of sentences",
        "add your thoughts",
        "add links",
        "in addition to the movements, what theme or ideas",
        "describe how the lesson develops",
    ]
    lower = text.lower()
    return any(p in lower for p in placeholders)


# ── Volume listing scraper ───────────────────────────────────────────────


def scrape_volume_listing(volume_num: int, session: requests.Session) -> list[dict]:
    """Scrape a volume listing page. Returns list of lesson stubs."""
    url = VOLUME_URL_TEMPLATE.format(n=volume_num)
    logger.info(f"Scraping volume {volume_num}: {url}")

    soup = _get(url, session)
    content = soup.find("div", class_="page-content")
    if not content:
        logger.warning(f"No page-content div found for volume {volume_num}")
        return []

    lessons = []
    # Lessons are in <p> tags as: NUMBER. <a>TITLE</a>. (Page NNN)
    for p in content.find_all("p"):
        # Get the full text and links
        text = p.decode_contents()
        # Split on <br/> or <br> tags
        lines = re.split(r'<br\s*/?\s*>', text)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse: NUMBER. <a href="URL">TITLE</a>. (Page NNN)
            # or: NUMBER <a href="URL">TITLE</a> (Page NNN)
            # The period after the number is optional (fractionals like 0.1, 24.2 omit it)
            match = re.match(
                r'(\d+(?:\.\d+)?)\.?\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.+?)</a>'
                r'[.)]*\s*(?:\(Page\s+(\d+)\))?',
                line,
            )
            if match:
                ay_num, href, title, page = match.groups()
                # Normalize URL
                if href.startswith("/"):
                    href = BASE_URL + href
                elif not href.startswith("http"):
                    href = BASE_URL + "/" + href

                lessons.append({
                    "ay_number": ay_num,
                    "title": BeautifulSoup(title, "html.parser").get_text(strip=True),
                    "iff_page": int(page) if page else None,
                    "volume": volume_num,
                    "source_url": href,
                })

    logger.info(f"  Found {len(lessons)} lessons in volume {volume_num}")
    return lessons


# ── Individual lesson page scraper ───────────────────────────────────────


def scrape_lesson_page(lesson: dict, session: requests.Session) -> dict:
    """Scrape an individual lesson page for detailed content."""
    url = lesson["source_url"]
    logger.info(f"  Scraping AY {lesson['ay_number']}: {url}")

    try:
        soup = _get(url, session)
    except requests.HTTPError as e:
        logger.warning(f"  HTTP error for AY {lesson['ay_number']}: {e}")
        return lesson

    # Extract tags from <main> class attribute
    main = soup.find("main", id="content")
    tags = []
    if main:
        classes = main.get("class", [])
        tags = [c.replace("tag-", "") for c in classes if c.startswith("tag-")]

    # Also get tags from post-tags div (more reliable text)
    tag_div = soup.find("div", class_="post-tags")
    if tag_div:
        for a in tag_div.find_all("a", rel="tag"):
            tag_text = a.get_text(strip=True)
            if tag_text and tag_text not in tags:
                tags.append(tag_text)

    lesson["tags"] = tags

    # Parse sections from page-content
    content = soup.find("div", class_="page-content")
    if not content:
        return lesson

    extras = {}
    related_atms = []

    for h2 in content.find_all("h2"):
        heading_text = h2.get_text(strip=True)
        heading_lower = heading_text.lower()

        # Skip user insights sections
        if any(skip in heading_lower for skip in SKIP_SECTIONS):
            continue

        section_content = _collect_section_content(h2)

        # Skip placeholder content
        if _is_placeholder(section_content):
            continue

        # Map to known field
        field_name = None
        for pattern, field in SECTION_MAP.items():
            if pattern in heading_lower:
                field_name = field
                break

        if field_name == "related_atms":
            # Parse related ATMs specially — extract links and commentary
            for sibling in h2.next_siblings:
                if isinstance(sibling, Tag):
                    if sibling.name == "h2":
                        break
                    if sibling.name in ("ul", "ol"):
                        for li in sibling.find_all("li", recursive=False):
                            link = li.find("a")
                            text = li.get_text(separator=" ", strip=True)
                            related_num = None
                            related_title = None
                            if link:
                                link_text = link.get_text(strip=True)
                                # Try to extract AY number from link text
                                num_match = re.search(r'AY\s*(\d+(?:\.\d+)?)', link_text, re.IGNORECASE)
                                if num_match:
                                    related_num = num_match.group(1)
                                related_title = link_text
                                # Commentary is text after the link
                                commentary = text.replace(link_text, "").strip().strip("-–—").strip()
                            else:
                                commentary = text
                            # Skip tag links and theme references in the related section
                            if commentary and commentary.lower() in ("tag", "see"):
                                continue
                            if related_title and related_title.lower().startswith("theme "):
                                continue
                            if related_num or related_title or commentary:
                                related_atms.append({
                                    "related_ay_number": related_num,
                                    "related_title": related_title,
                                    "commentary": commentary or None,
                                })
        elif field_name == "resources":
            # Store resources in extras (external links that may rot)
            if section_content:
                extras["resources"] = section_content
        elif field_name:
            lesson[field_name] = section_content
        elif section_content:
            # Unknown section — store in extras
            extras[heading_text] = section_content

    lesson["related_atms"] = related_atms
    if extras:
        lesson["extras"] = extras

    return lesson


# ── Database operations ──────────────────────────────────────────────────


def ensure_schema(db: Database):
    """Create AY tables if they don't exist."""
    with db.get_cursor() as cursor:
        cursor.execute(AY_SCHEMA)
    logger.info("AY schema ensured")


def upsert_lesson(db: Database, lesson: dict):
    """Insert or update an AY lesson and its tags/related lessons."""
    extras_json = json.dumps(lesson.get("extras")) if lesson.get("extras") else None

    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ay_lesson (ay_number, title, iff_page, volume, source_info,
                                   synopsis, outline, teaching_focus, source_url, extras)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (ay_number) DO UPDATE SET
                title = EXCLUDED.title,
                iff_page = COALESCE(EXCLUDED.iff_page, ay_lesson.iff_page),
                volume = COALESCE(EXCLUDED.volume, ay_lesson.volume),
                source_info = COALESCE(EXCLUDED.source_info, ay_lesson.source_info),
                synopsis = COALESCE(EXCLUDED.synopsis, ay_lesson.synopsis),
                outline = COALESCE(EXCLUDED.outline, ay_lesson.outline),
                teaching_focus = COALESCE(EXCLUDED.teaching_focus, ay_lesson.teaching_focus),
                source_url = COALESCE(EXCLUDED.source_url, ay_lesson.source_url),
                extras = COALESCE(EXCLUDED.extras, ay_lesson.extras)
            RETURNING id
            """,
            (
                lesson["ay_number"],
                lesson["title"],
                lesson.get("iff_page"),
                lesson.get("volume"),
                lesson.get("source_info"),
                lesson.get("synopsis"),
                lesson.get("outline"),
                lesson.get("teaching_focus"),
                lesson.get("source_url"),
                extras_json,
            ),
        )
        lesson_id = cursor.fetchone()["id"]

        # Upsert tags
        tags = lesson.get("tags", [])
        if tags:
            cursor.execute("DELETE FROM ay_lesson_tag WHERE lesson_id = %s", (lesson_id,))
            from psycopg2.extras import execute_values
            execute_values(
                cursor,
                "INSERT INTO ay_lesson_tag (lesson_id, tag) VALUES %s ON CONFLICT DO NOTHING",
                [(lesson_id, tag) for tag in tags],
            )

        # Upsert related lessons
        related = lesson.get("related_atms", [])
        if related:
            cursor.execute("DELETE FROM ay_related_lesson WHERE lesson_id = %s", (lesson_id,))
            from psycopg2.extras import execute_values
            execute_values(
                cursor,
                "INSERT INTO ay_related_lesson (lesson_id, related_ay_number, related_title, commentary) VALUES %s",
                [(lesson_id, r.get("related_ay_number"), r.get("related_title"), r.get("commentary"))
                 for r in related],
            )


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Scrape AY lessons from feldynotebook.com")
    parser.add_argument("--volumes-only", action="store_true", help="Only scrape volume listings, skip lesson pages")
    parser.add_argument("--dry-run", action="store_true", help="Scrape but don't write to DB")
    parser.add_argument("--volume", type=int, help="Scrape only this volume number")
    parser.add_argument("--limit", type=int, help="Max number of lesson pages to scrape")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="Seconds between requests")
    parser.add_argument("--resume", action="store_true", help="Skip lessons already scraped (have outline/synopsis in DB)")
    parser.add_argument("--dump-json", type=str, help="Dump scraped data to JSON file")
    args = parser.parse_args()

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    # Initialize DB early (for resume + incremental writes)
    db = None
    if not args.dry_run:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            logger.error("DATABASE_URL not set, cannot write to DB")
            sys.exit(1)
        db = Database(db_url)
        ensure_schema(db)

    # Phase 1: Scrape volume listings
    all_lessons = []
    volumes = [args.volume] if args.volume else range(1, NUM_VOLUMES + 1)

    for vol in volumes:
        lessons = scrape_volume_listing(vol, session)
        all_lessons.extend(lessons)
        time.sleep(args.delay)

    logger.info(f"Total lessons from volume listings: {len(all_lessons)}")

    # Write volume stubs to DB immediately (so we have titles even if detail scrape crashes)
    if db:
        for lesson in all_lessons:
            upsert_lesson(db, lesson)
        logger.info(f"Wrote {len(all_lessons)} lesson stubs to database")

    # Phase 2: Scrape individual lesson pages
    if not args.volumes_only:
        to_scrape = all_lessons[:args.limit] if args.limit else all_lessons

        # Resume mode: skip lessons already in DB with content
        if args.resume and db:
            with db.get_cursor(commit=False) as cursor:
                cursor.execute("SELECT ay_number FROM ay_lesson WHERE outline IS NOT NULL OR synopsis IS NOT NULL")
                already_scraped = {row["ay_number"] for row in cursor.fetchall()}
            before = len(to_scrape)
            to_scrape = [l for l in to_scrape if l["ay_number"] not in already_scraped]
            logger.info(f"Resume mode: skipping {before - len(to_scrape)} already-scraped lessons")

        logger.info(f"Scraping {len(to_scrape)} lesson pages...")

        for i, lesson in enumerate(to_scrape):
            scrape_lesson_page(lesson, session)
            # Write to DB incrementally
            if db:
                upsert_lesson(db, lesson)
            if (i + 1) % 10 == 0:
                logger.info(f"  Progress: {i + 1}/{len(to_scrape)}")
            time.sleep(args.delay)

        logger.info(f"Scraped {len(to_scrape)} lesson pages")

    # Dump to JSON if requested
    if args.dump_json:
        with open(args.dump_json, "w") as f:
            json.dump(all_lessons, f, indent=2, default=str)
        logger.info(f"Dumped {len(all_lessons)} lessons to {args.dump_json}")
    else:
        # Print summary in dry-run mode
        with_outline = sum(1 for l in all_lessons if l.get("outline"))
        with_synopsis = sum(1 for l in all_lessons if l.get("synopsis"))
        with_tags = sum(1 for l in all_lessons if l.get("tags"))
        with_related = sum(1 for l in all_lessons if l.get("related_atms"))
        with_extras = sum(1 for l in all_lessons if l.get("extras"))

        logger.info(f"Dry run summary:")
        logger.info(f"  Total lessons: {len(all_lessons)}")
        logger.info(f"  With outline: {with_outline}")
        logger.info(f"  With synopsis: {with_synopsis}")
        logger.info(f"  With tags: {with_tags}")
        logger.info(f"  With related ATMs: {with_related}")
        logger.info(f"  With extras: {with_extras}")

        # Show a sample
        if all_lessons:
            sample = next((l for l in all_lessons if l.get("outline")), all_lessons[0])
            logger.info(f"\nSample lesson (AY {sample['ay_number']}):")
            for k, v in sample.items():
                if k == "outline" and v:
                    logger.info(f"  {k}: {v[:200]}...")
                elif k == "extras" and v:
                    logger.info(f"  {k}: {json.dumps(v, default=str)[:200]}...")
                elif k == "related_atms" and v:
                    logger.info(f"  {k}: {len(v)} related lessons")
                elif k == "tags" and v:
                    logger.info(f"  {k}: {v}")
                else:
                    val = str(v)[:100] if v else None
                    logger.info(f"  {k}: {val}")


if __name__ == "__main__":
    main()
