"""
Feldenkrais Manhattan Training Loader.

Scrapes the training site for video IDs, matches with local files,
fetches YouTube metadata, and stages for processing.

Optimized to only scrape detail pages for NEW lessons (caches lesson_url → youtube_id).
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

try:
    from config import Config
    from database import Database
except ImportError:
    from .config import Config
    from .database import Database

# Lazy imports
Storage = None
yt_dlp = None


def get_storage(bucket: str):
    global Storage
    if Storage is None:
        try:
            from storage import Storage as StorageClass
        except ImportError:
            from .storage import Storage as StorageClass
        Storage = StorageClass
    return Storage(bucket)


def get_yt_dlp():
    global yt_dlp
    if yt_dlp is None:
        import yt_dlp as ytdlp_module
        yt_dlp = ytdlp_module
    return yt_dlp


logger = logging.getLogger(__name__)

BASE_URL = "https://feldenkraismanhattantraining.com"
LOGIN_URL = f"{BASE_URL}/login/"
COURSE_URL = f"{BASE_URL}/courses/nyc-training-program/"

FM_SOURCE_ID = 3
AUDIO_TYPE_ID = 2


@dataclass
class LessonVideo:
    """Video found on the training site."""
    youtube_id: str
    lesson_url: str
    lesson_title: str
    segment: Optional[str] = None
    day: Optional[str] = None
    chapter: Optional[str] = None
    local_path: Optional[str] = None
    youtube_title: Optional[str] = None
    youtube_channel: Optional[str] = None
    duration_seconds: Optional[int] = None


def create_session(email: str, password: str) -> requests.Session:
    """Create an authenticated session."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    })

    session.get(LOGIN_URL)

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

    if response.status_code != 200 or 'unauthorized' in response.text.lower():
        raise Exception("Login failed")

    logger.info("Successfully logged in to Feldenkrais Manhattan")
    return session


def get_lesson_urls_from_course_page(session: requests.Session) -> list[tuple[str, str]]:
    """Get all lesson URLs from the course page (single request)."""
    response = session.get(COURSE_URL)
    soup = BeautifulSoup(response.text, 'html.parser')

    lessons = []
    seen_urls = set()

    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        text = ' '.join(link.text.split())  # Clean whitespace

        if '/lessons/' in href and BASE_URL in href and href not in seen_urls:
            if text and text != 'Start':
                seen_urls.add(href)
                lessons.append((href, text))

    logger.info(f"Found {len(lessons)} lessons on course page")
    return lessons


def extract_youtube_id_from_page(html: str) -> Optional[str]:
    """Extract YouTube video ID from lesson page HTML."""
    patterns = [
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
    ]

    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None


def parse_lesson_title(title: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract segment, day, and chapter from lesson title."""
    segment = None
    day = None
    chapter = None

    seg_match = re.search(r'Segment\s*(\d+)', title, re.IGNORECASE)
    if seg_match:
        segment = seg_match.group(1)

    day_match = re.search(r'Day\s*(\d+)', title, re.IGNORECASE)
    if day_match:
        day = day_match.group(1)

    chapter_match = re.search(r'Chapter\s*(\d+)', title, re.IGNORECASE)
    if chapter_match:
        chapter = chapter_match.group(1)

    return segment, day, chapter


def scrape_and_update(
    email: str,
    password: str,
    db: Database,
    delay: float = 0.3,
) -> list[LessonVideo]:
    """
    Scrape the training site, only fetching detail pages for NEW lessons.

    Returns list of all videos (from cache + newly scraped).
    """
    session = create_session(email, password)

    # Step 1: Get all lesson URLs from course page (1 request)
    all_lessons = get_lesson_urls_from_course_page(session)

    # Step 2: Get cached lesson → youtube_id mappings from DB
    cached_mappings = db.get_fm_lesson_youtube_ids()
    logger.info(f"Have {len(cached_mappings)} cached lesson mappings")

    # Step 3: Identify new lessons (not in cache)
    new_lessons = [(url, title) for url, title in all_lessons if url not in cached_mappings]
    logger.info(f"Found {len(new_lessons)} new lessons to scrape")

    # Step 4: Scrape detail pages for new lessons only
    for lesson_url, lesson_title in new_lessons:
        logger.info(f"Scraping new: {lesson_title}")

        try:
            response = session.get(lesson_url)
            youtube_id = extract_youtube_id_from_page(response.text)

            if youtube_id:
                # Save to DB
                db.upsert_fm_lesson(
                    lesson_url=lesson_url,
                    lesson_title=lesson_title,
                    youtube_id=youtube_id,
                    status='scraped',
                )
                cached_mappings[lesson_url] = youtube_id
                logger.info(f"  Found: {youtube_id}")
            else:
                logger.warning(f"  No video found on page")
                db.upsert_fm_lesson(
                    lesson_url=lesson_url,
                    lesson_title=lesson_title,
                    youtube_id=None,
                    status='no_video',
                )

            if delay > 0:
                time.sleep(delay)

        except Exception as e:
            logger.error(f"Error scraping {lesson_url}: {e}")

    # Step 5: Build video list from all lessons (cached + new)
    videos = []
    for lesson_url, lesson_title in all_lessons:
        youtube_id = cached_mappings.get(lesson_url)
        if youtube_id:
            segment, day, chapter = parse_lesson_title(lesson_title)
            videos.append(LessonVideo(
                youtube_id=youtube_id,
                lesson_url=lesson_url,
                lesson_title=lesson_title,
                segment=segment,
                day=day,
                chapter=chapter,
            ))

    logger.info(f"Total videos: {len(videos)}")
    return videos


def find_local_files(videos: list[LessonVideo], local_dir: str) -> list[LessonVideo]:
    """Match videos with local files."""
    local_path = Path(local_dir)

    found = 0
    for video in videos:
        mp3_path = local_path / f"{video.youtube_id}.mp3"
        if mp3_path.exists():
            video.local_path = str(mp3_path)
            found += 1

    logger.info(f"Found {found}/{len(videos)} videos locally")
    return videos


def get_youtube_metadata(video_id: str) -> dict:
    """Get video metadata from YouTube using yt-dlp."""
    try:
        ydl = get_yt_dlp()
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }

        url = f"https://www.youtube.com/watch?v={video_id}"

        with ydl.YoutubeDL(ydl_opts) as ydl_instance:
            info = ydl_instance.extract_info(url, download=False)
            return {
                'title': info.get('title', ''),
                'channel': info.get('channel', info.get('uploader', '')),
                'channel_id': info.get('channel_id', ''),
                'duration': info.get('duration', 0),
                'upload_date': info.get('upload_date'),
            }
    except Exception as e:
        logger.warning(f"Could not get YouTube metadata for {video_id}: {e}")
        return {}


def download_video(video_id: str, output_dir: str) -> Optional[str]:
    """Download audio from YouTube video. Returns path to downloaded file."""
    try:
        ydl = get_yt_dlp()
        output_template = str(Path(output_dir) / "%(id)s.%(ext)s")

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '0',
            }],
            'quiet': True,
            'no_warnings': True,
        }

        url = f"https://www.youtube.com/watch?v={video_id}"

        with ydl.YoutubeDL(ydl_opts) as ydl_instance:
            ydl_instance.download([url])

        # Find the downloaded file
        mp3_path = Path(output_dir) / f"{video_id}.mp3"
        if mp3_path.exists():
            return str(mp3_path)

        # Check for other formats
        for ext in ['m4a', 'opus', 'webm']:
            path = Path(output_dir) / f"{video_id}.{ext}"
            if path.exists():
                return str(path)

        return None

    except Exception as e:
        logger.error(f"Failed to download {video_id}: {e}")
        return None


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    sanitized = re.sub(r'\s+', '_', sanitized)
    sanitized = sanitized.strip('._')
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
    return sanitized


def stage_video(storage, video: LessonVideo) -> str:
    """Stage a video file with sidecar JSON."""
    parts = ['FM']
    if video.segment:
        parts.append(f"S{video.segment}")
    if video.day:
        parts.append(f"D{video.day}")
    if video.chapter:
        parts.append(f"C{video.chapter}")
    parts.append(video.youtube_id)

    filename = f"{'_'.join(parts)}.mp3"
    staging_key = f"staging/{filename}"
    sidecar_key = f"{staging_key}.json"

    title = video.lesson_title
    if video.youtube_title:
        title = f"{video.lesson_title} - {video.youtube_title}"

    sidecar = {
        "title": title,
        "source_url": video.lesson_url,
        "source_id": FM_SOURCE_ID,
        "type_id": AUDIO_TYPE_ID,
        "tags": ["feldenkrais-manhattan", "training"],
        "youtube_metadata": {
            "video_id": video.youtube_id,
            "channel_name": video.youtube_channel,
            "duration_seconds": video.duration_seconds,
            "youtube_title": video.youtube_title,
        },
        "lesson_metadata": {
            "segment": video.segment,
            "day": video.day,
            "chapter": video.chapter,
            "lesson_url": video.lesson_url,
            "lesson_title": video.lesson_title,
        }
    }

    with open(video.local_path, 'rb') as f:
        content = f.read()

    logger.info(f"Staging: {staging_key} ({len(content)} bytes)")
    storage.put_object(sidecar_key, json.dumps(sidecar, indent=2).encode())
    storage.put_object(staging_key, content)

    return staging_key


def process_videos(
    videos: list[LessonVideo],
    bucket: str,
    db: Database,
    local_dir: Optional[str] = None,
    download_missing: bool = False,
    limit: Optional[int] = None,
    fetch_metadata: bool = True,
) -> dict:
    """
    Stage videos to S3.

    If download_missing=True, downloads videos not found locally.
    """
    storage = get_storage(bucket)

    # Separate into local and missing
    local_videos = [v for v in videos if v.local_path]
    missing_videos = [v for v in videos if not v.local_path]

    to_process = list(local_videos)

    # Download missing if requested
    if download_missing and missing_videos:
        download_dir = local_dir or "/tmp/fm_downloads"
        Path(download_dir).mkdir(parents=True, exist_ok=True)

        download_limit = limit - len(local_videos) if limit else len(missing_videos)
        to_download = missing_videos[:max(0, download_limit)]

        logger.info(f"Downloading {len(to_download)} missing videos")

        for video in to_download:
            logger.info(f"Downloading: {video.youtube_id} ({video.lesson_title})")
            path = download_video(video.youtube_id, download_dir)
            if path:
                video.local_path = path
                to_process.append(video)
                logger.info(f"  Downloaded: {path}")
            else:
                logger.error(f"  Failed to download")

    if limit:
        to_process = to_process[:limit]

    logger.info(f"Processing {len(to_process)} videos")

    # Fetch YouTube metadata if needed
    if fetch_metadata:
        for video in to_process:
            if not video.youtube_title:
                metadata = get_youtube_metadata(video.youtube_id)
                video.youtube_title = metadata.get('title')
                video.youtube_channel = metadata.get('channel')
                video.duration_seconds = metadata.get('duration')

    # Stage videos
    results = []
    for video in to_process:
        try:
            staging_key = stage_video(storage, video)

            # Mark lesson as complete
            db.mark_fm_lesson_complete(video.lesson_url)

            results.append({
                'youtube_id': video.youtube_id,
                'lesson_title': video.lesson_title,
                'staging_key': staging_key,
                'status': 'staged',
            })
        except Exception as e:
            logger.error(f"Error staging {video.youtube_id}: {e}")
            results.append({
                'youtube_id': video.youtube_id,
                'lesson_title': video.lesson_title,
                'status': 'error',
                'error': str(e),
            })

    staged = [r for r in results if r['status'] == 'staged']
    errors = [r for r in results if r['status'] == 'error']

    return {
        'total_on_site': len(videos),
        'local_available': len(local_videos),
        'missing': len(missing_videos),
        'processed': len(to_process),
        'staged': len(staged),
        'errors': len(errors),
        'results': results,
    }


def main():
    """CLI interface."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="Load Feldenkrais Manhattan training videos")
    parser.add_argument("--email", default=os.environ.get("FM_EMAIL"), help="Login email")
    parser.add_argument("--password", default=os.environ.get("FM_PASSWORD"), help="Login password")
    parser.add_argument("--local-dir", help="Directory with local video files")
    parser.add_argument("--bucket", default=os.environ.get("KB_BUCKET"), help="S3 bucket")
    parser.add_argument("--db-path", default=os.environ.get("KB_DB_PATH", "/tmp/knowledge.db"), help="Database path")
    parser.add_argument("--limit", type=int, help="Limit number of videos to process")
    parser.add_argument("--download-missing", action="store_true", help="Download videos not found locally")
    parser.add_argument("--scrape-only", action="store_true", help="Only scrape, don't stage")
    parser.add_argument("--skip-metadata", action="store_true", help="Skip YouTube metadata fetch")
    parser.add_argument("--output", "-o", help="Output JSON file for video data")

    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error("Email and password required (use --email/--password or FM_EMAIL/FM_PASSWORD env vars)")

    # Initialize database
    db = Database(args.db_path)
    db.initialize()

    # Scrape (uses cache for known lessons)
    videos = scrape_and_update(args.email, args.password, db)

    # Find local files
    if args.local_dir:
        videos = find_local_files(videos, args.local_dir)

    # Save output if requested
    if args.output:
        with open(args.output, 'w') as f:
            json.dump([asdict(v) for v in videos], f, indent=2)
        logger.info(f"Saved {len(videos)} videos to {args.output}")

    if args.scrape_only:
        local_count = sum(1 for v in videos if v.local_path)
        print(f"Total videos on site: {len(videos)}")
        print(f"Available locally: {local_count}")
        print(f"Missing: {len(videos) - local_count}")
        db.close()
        return

    # Stage videos
    if not args.bucket:
        db.close()
        parser.error("Bucket required for staging (use --bucket or KB_BUCKET env var)")

    result = process_videos(
        videos=videos,
        bucket=args.bucket,
        db=db,
        local_dir=args.local_dir,
        download_missing=args.download_missing,
        limit=args.limit,
        fetch_metadata=not args.skip_metadata,
    )

    print(json.dumps(result, indent=2))
    db.close()


if __name__ == "__main__":
    main()
