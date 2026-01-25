"""
Scraper for Feldenkrais Manhattan Training videos.

Logs into the training site and extracts YouTube video URLs from lesson pages.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://feldenkraismanhattantraining.com"
LOGIN_URL = f"{BASE_URL}/login/"
COURSE_URL = f"{BASE_URL}/courses/nyc-training-program/"


@dataclass
class VideoInfo:
    """Information about a video found on the site."""
    youtube_id: str
    youtube_url: str
    lesson_url: str
    lesson_title: str
    day: Optional[str] = None
    chapter: Optional[str] = None


def create_session(email: str, password: str) -> requests.Session:
    """Create an authenticated session."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    })

    # Get login page (for cookies)
    session.get(LOGIN_URL)

    # Login
    login_data = {
        'log': email,
        'pwd': password,
        'rememberme': 'forever',
        'wp-submit': 'Log In',
        'redirect_to': COURSE_URL,
        'mepr_process_login_form': 'true',
        'mepr_is_login_page': 'true',
    }

    response = session.post(LOGIN_URL, data=login_data, allow_redirects=True)

    if response.status_code != 200:
        raise Exception(f"Login failed with status {response.status_code}")

    # Verify we can access the course
    if 'unauthorized' in response.text.lower():
        raise Exception("Login failed - unauthorized")

    logger.info("Successfully logged in")
    return session


def get_lesson_urls(session: requests.Session) -> list[tuple[str, str]]:
    """Get all lesson URLs from the course page."""
    response = session.get(COURSE_URL)
    soup = BeautifulSoup(response.text, 'html.parser')

    lessons = []
    links = soup.find_all('a', href=True)

    for link in links:
        href = link.get('href', '')
        text = link.text.strip()

        # Filter for lesson links
        if '/lessons/' in href and BASE_URL in href:
            # Skip duplicates
            if (href, text) not in lessons:
                lessons.append((href, text))

    # Remove duplicates by URL
    seen_urls = set()
    unique_lessons = []
    for url, title in lessons:
        if url not in seen_urls:
            seen_urls.add(url)
            unique_lessons.append((url, title))

    logger.info(f"Found {len(unique_lessons)} lesson pages")
    return unique_lessons


def extract_youtube_ids(html: str) -> list[str]:
    """Extract YouTube video IDs from HTML."""
    patterns = [
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'"videoId":\s*"([a-zA-Z0-9_-]{11})"',
    ]

    ids = set()
    for pattern in patterns:
        matches = re.findall(pattern, html)
        ids.update(matches)

    return list(ids)


def parse_lesson_title(title: str) -> tuple[Optional[str], Optional[str]]:
    """Extract day and chapter from lesson title."""
    day = None
    chapter = None

    # Match patterns like "Day 1: Jan 17, Chapter 1" or "Day 1  2/7/25 Chapter 1"
    day_match = re.search(r'Day\s*(\d+)', title, re.IGNORECASE)
    if day_match:
        day = day_match.group(1)

    chapter_match = re.search(r'Chapter\s*(\d+)', title, re.IGNORECASE)
    if chapter_match:
        chapter = chapter_match.group(1)

    return day, chapter


def scrape_videos(
    email: str,
    password: str,
    delay_seconds: float = 0.5,
) -> list[VideoInfo]:
    """
    Scrape all video URLs from the training site.

    Args:
        email: Login email
        password: Login password
        delay_seconds: Delay between requests to be polite

    Returns:
        List of VideoInfo objects
    """
    session = create_session(email, password)
    lessons = get_lesson_urls(session)

    videos = []

    for lesson_url, lesson_title in lessons:
        logger.info(f"Checking: {lesson_title}")

        try:
            response = session.get(lesson_url)
            youtube_ids = extract_youtube_ids(response.text)

            day, chapter = parse_lesson_title(lesson_title)

            for yt_id in youtube_ids:
                video = VideoInfo(
                    youtube_id=yt_id,
                    youtube_url=f"https://www.youtube.com/watch?v={yt_id}",
                    lesson_url=lesson_url,
                    lesson_title=lesson_title,
                    day=day,
                    chapter=chapter,
                )
                videos.append(video)
                logger.info(f"  Found video: {yt_id}")

            if delay_seconds > 0:
                time.sleep(delay_seconds)

        except Exception as e:
            logger.error(f"Error processing {lesson_url}: {e}")

    logger.info(f"Total videos found: {len(videos)}")
    return videos


def main():
    """CLI interface."""
    import argparse
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="Scrape Feldenkrais Manhattan training videos")
    parser.add_argument("--email", default=os.environ.get("FM_EMAIL"), help="Login email")
    parser.add_argument("--password", default=os.environ.get("FM_PASSWORD"), help="Login password")
    parser.add_argument("--output", "-o", default="feldenkrais_manhattan_videos.json", help="Output file")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between requests")
    parser.add_argument("--urls-only", action="store_true", help="Output just YouTube URLs (one per line)")

    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error("Email and password required (use --email/--password or FM_EMAIL/FM_PASSWORD env vars)")

    videos = scrape_videos(args.email, args.password, args.delay)

    if args.urls_only:
        # Output just the YouTube URLs
        urls = sorted(set(v.youtube_url for v in videos))
        with open(args.output, 'w') as f:
            for url in urls:
                f.write(url + '\n')
        print(f"Wrote {len(urls)} unique URLs to {args.output}")
    else:
        # Output full JSON
        output = [asdict(v) for v in videos]
        with open(args.output, 'w') as f:
            json.dump(output, f, indent=2)
        print(f"Wrote {len(videos)} videos to {args.output}")

        # Also print summary
        unique_ids = set(v.youtube_id for v in videos)
        print(f"Unique YouTube videos: {len(unique_ids)}")


if __name__ == "__main__":
    main()
