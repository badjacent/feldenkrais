"""
YouTube Importer: downloads videos from YouTube and stages them for processing.

Uses yt-dlp to download audio from YouTube videos.
Manages a queue of URLs in the database.
"""

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from config import Config
    from database import Database
except ImportError:
    from ..config import Config
    from ..database import Database

# Defer storage import until needed (requires boto3)
Storage = None

def get_storage(bucket: str):
    """Lazy import of Storage to avoid boto3 dependency when not needed."""
    global Storage
    if Storage is None:
        try:
            from storage import Storage as StorageClass
        except ImportError:
            from ..storage import Storage as StorageClass
        Storage = StorageClass
    return Storage(bucket)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Fallback YouTube source ID (used only if channel source can't be resolved)
YOUTUBE_SOURCE_ID = 2
AUDIO_TYPE_ID = 2

# Cache of channel_name -> source_id (populated at runtime)
_channel_source_cache: dict[str, int] = {}


def resolve_channel_source(db: Database, channel_name: str) -> int:
    """Resolve a YouTube channel to a document_source, creating one if needed.

    Source name format: {CHANNEL_NAME}_YOUTUBE (e.g. RUTH_KNILL_YOUTUBE)
    Category name format: {Channel Name} (YouTube)
    """
    if channel_name in _channel_source_cache:
        return _channel_source_cache[channel_name]

    source_name = sanitize_filename(channel_name).upper().replace(" ", "_") + "_YOUTUBE"
    category_display = f"{channel_name} (YouTube)"

    with db.get_cursor() as cursor:
        # Try to find existing source
        cursor.execute(
            "SELECT id FROM document_source WHERE name = %s", (source_name,)
        )
        row = cursor.fetchone()
        if row:
            source_id = row["id"]
        else:
            # Create new source for this channel
            cursor.execute(
                "INSERT INTO document_source (name, category_name, visible) "
                "VALUES (%s, %s, TRUE) RETURNING id",
                (source_name, category_display),
            )
            source_id = cursor.fetchone()["id"]
            logger.info(f"Created source {source_name} (id={source_id}) for channel: {channel_name}")

    _channel_source_cache[channel_name] = source_id
    return source_id


@dataclass
class VideoInfo:
    """Metadata extracted from a YouTube video."""
    video_id: str
    title: str
    channel_name: str
    channel_id: str
    duration_seconds: int
    upload_date: Optional[str] = None
    description: Optional[str] = None


def extract_video_id(url: str) -> Optional[str]:
    """Extract video ID from various YouTube URL formats."""
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_video_info(url: str) -> VideoInfo:
    """
    Get video metadata using yt-dlp.

    Uses yt-dlp Python library when available, falls back to subprocess.
    """
    try:
        import yt_dlp

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(url, download=False)

    except ImportError:
        # Fall back to subprocess for local CLI use
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)

    return VideoInfo(
        video_id=data.get("id", ""),
        title=data.get("title", "Unknown"),
        channel_name=data.get("channel", data.get("uploader", "Unknown")),
        channel_id=data.get("channel_id", data.get("uploader_id", "")),
        duration_seconds=data.get("duration", 0),
        upload_date=data.get("upload_date"),
        description=data.get("description"),
    )


def download_audio(url: str, output_dir: str) -> tuple[str, str]:
    """
    Download audio from a YouTube video.

    Returns (file_path, format) of the downloaded file.
    Uses yt-dlp Python library when available, falls back to subprocess.
    """
    output_template = str(Path(output_dir) / "%(id)s.%(ext)s")

    try:
        import yt_dlp

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

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info['id']

        # Find the downloaded file (yt-dlp adds .mp3 extension after conversion)
        mp3_path = str(Path(output_dir) / f"{video_id}.mp3")
        if Path(mp3_path).exists():
            return mp3_path, "mp3"

        # If mp3 doesn't exist, look for any audio file
        for ext in ['mp3', 'm4a', 'opus', 'webm']:
            path = str(Path(output_dir) / f"{video_id}.{ext}")
            if Path(path).exists():
                return path, ext

        raise FileNotFoundError(f"Downloaded audio file not found for {video_id}")

    except ImportError:
        # Fall back to subprocess for local CLI use
        result = subprocess.run(
            [
                "yt-dlp",
                "-x",  # Extract audio
                "--audio-format", "mp3",
                "--audio-quality", "0",  # Best quality
                "-o", output_template,
                "--print", "after_move:filepath",  # Print final path
                url,
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        # Get the output file path from stdout
        file_path = result.stdout.strip().split('\n')[-1]
        return file_path, "mp3"


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    # Remove or replace problematic characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    sanitized = re.sub(r'\s+', '_', sanitized)
    sanitized = sanitized.strip('._')
    # Limit length
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
    return sanitized


def stage_video(
    storage,  # Storage instance
    video_info: VideoInfo,
    audio_path: str,
    source_url: str,
    tags: Optional[list[str]] = None,
    db: Optional[Database] = None,
) -> str:
    """
    Stage a downloaded video's audio file with sidecar JSON.

    Returns the staging key.
    """
    # Resolve per-channel source (falls back to generic YOUTUBE if no db)
    if db and video_info.channel_name:
        source_id = resolve_channel_source(db, video_info.channel_name)
    else:
        source_id = YOUTUBE_SOURCE_ID

    # Create a filename from channel and title
    filename = f"{sanitize_filename(video_info.channel_name)}_{sanitize_filename(video_info.title)}.mp3"
    staging_key = f"staging/{filename}"
    sidecar_key = f"{staging_key}.json"

    sidecar = {
        "title": video_info.title,
        "source_url": source_url,
        "source_id": source_id,
        "type_id": AUDIO_TYPE_ID,
        "tags": tags or [],
        "youtube_metadata": {
            "video_id": video_info.video_id,
            "channel_name": video_info.channel_name,
            "channel_id": video_info.channel_id,
            "duration_seconds": video_info.duration_seconds,
            "upload_date": video_info.upload_date,
        }
    }

    # Read and upload the audio file
    with open(audio_path, "rb") as f:
        content = f.read()

    logger.info(f"Staging: {staging_key} ({len(content)} bytes)")
    storage.put_object(sidecar_key, json.dumps(sidecar, indent=2).encode())
    storage.put_object(staging_key, content)

    return staging_key


def process_url(
    db: Database,
    storage,  # Storage instance
    queue_id: str,
    url: str,
    tags: Optional[list[str]] = None,
) -> dict:
    """
    Process a single YouTube URL: download, extract metadata, and stage.

    Returns a result dict with status and details.
    """
    result = {
        "queue_id": queue_id,
        "url": url,
        "status": "error",
    }

    try:
        # Get video info
        logger.info(f"Getting info for: {url}")
        video_info = get_video_info(url)

        # Update queue with metadata
        db.update_youtube_queue(
            queue_id=queue_id,
            video_id=video_info.video_id,
            video_title=video_info.title,
            channel_name=video_info.channel_name,
            channel_id=video_info.channel_id,
            duration_seconds=video_info.duration_seconds,
        )

        result["video_info"] = {
            "video_id": video_info.video_id,
            "title": video_info.title,
            "channel": video_info.channel_name,
            "duration": video_info.duration_seconds,
        }

        # Download audio
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"Downloading audio: {video_info.title}")
            audio_path, audio_format = download_audio(url, temp_dir)

            # Stage the file
            staging_key = stage_video(
                storage=storage,
                video_info=video_info,
                audio_path=audio_path,
                source_url=url,
                tags=tags,
                db=db,
            )

            result["staging_key"] = staging_key

        # Mark as completed
        db.update_youtube_queue(queue_id=queue_id, status="completed")
        result["status"] = "completed"
        logger.info(f"Successfully processed: {video_info.title}")

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr if e.stderr else str(e)
        logger.error(f"yt-dlp error for {url}: {error_msg}")
        db.update_youtube_queue(queue_id=queue_id, status="failed", error=error_msg[:500])
        result["error"] = error_msg

    except Exception as e:
        logger.error(f"Error processing {url}: {e}")
        db.update_youtube_queue(queue_id=queue_id, status="failed", error=str(e)[:500])
        result["error"] = str(e)

    return result


def add_urls(db: Database, urls: list[str]) -> dict:
    """
    Add multiple YouTube URLs to the queue.

    Returns dict with counts of added and skipped URLs.
    """
    added = []
    skipped = []

    for url in urls:
        url = url.strip()
        if not url:
            continue

        queue_id = db.add_youtube_url(url)
        if queue_id:
            added.append({"url": url, "queue_id": queue_id})
        else:
            skipped.append({"url": url, "reason": "already_exists"})

    return {
        "added": added,
        "skipped": skipped,
        "added_count": len(added),
        "skipped_count": len(skipped),
    }


def process_queue(
    db: Database,
    storage,  # Storage instance
    limit: int = 10,
    tags: Optional[list[str]] = None,
) -> dict:
    """
    Process pending URLs from the queue.

    Returns summary of processing results.
    """
    pending = db.get_pending_youtube_urls(limit=limit)

    if not pending:
        logger.info("No pending YouTube URLs")
        return {"processed": 0, "results": []}

    logger.info(f"Processing {len(pending)} YouTube URLs")

    results = []
    for item in pending:
        queue_id = item["id"]
        url = item["url"]

        # Try to claim this URL
        if not db.claim_youtube_url(queue_id):
            logger.info(f"URL already claimed: {url}")
            continue

        result = process_url(
            db=db,
            storage=storage,
            queue_id=queue_id,
            url=url,
            tags=tags,
        )
        results.append(result)

    completed = [r for r in results if r["status"] == "completed"]
    failed = [r for r in results if r["status"] == "error"]

    logger.info(f"Processed {len(results)}: {len(completed)} completed, {len(failed)} failed")

    return {
        "processed": len(results),
        "completed": len(completed),
        "failed": len(failed),
        "results": results,
    }


# =============================================================================
# Lambda Handler
# =============================================================================

def handler(event, context):
    """
    Lambda handler for YouTube importer.

    Supported actions:
    - add: Add URLs to the queue
      {"action": "add", "urls": ["https://youtube.com/watch?v=..."]}

    - process: Process pending URLs from queue (default limit=1 for Lambda)
      {"action": "process", "limit": 1, "tags": ["tag1", "tag2"]}

    - status: Get queue statistics
      {"action": "status"}

    - list: List queue entries
      {"action": "list", "status": "pending"}
    """
    logger.info(f"Received event: {json.dumps(event)}")

    config = Config.from_env()
    storage = get_storage(config.bucket)

    action = event.get("action", "process")

    # Initialize DB with PostgreSQL connection
    db = Database(config.database_url)
    db.initialize()

    # Check LLM kill switch — stall (skip) rather than fail
    if action == "process":
        settings = db.get_cache("settings:llm_enabled")
        if not settings or not settings.get("enabled", False):
            logger.info("LLM disabled, skipping YouTube processing")
            db.close()
            return {"statusCode": 200, "body": json.dumps({"message": "Skipped - LLM disabled"})}

    # For process action, acquire advisory lock to prevent concurrent runs
    lock_acquired = False
    if action == "process":
        lock_acquired = db.try_acquire_lock(Database.LOCK_YOUTUBE)
        if not lock_acquired:
            logger.info("Another YouTube Lambda is processing, skipping")
            db.close()
            return {"statusCode": 200, "body": json.dumps({"message": "Skipped - another instance running"})}
        logger.info("Acquired YouTube lock")

    try:
        if action == "add":
            urls = event.get("urls", [])
            if not urls:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "No URLs provided"}),
                }
            result = add_urls(db, urls)
            return {
                "statusCode": 200,
                "body": json.dumps(result),
            }

        elif action == "process":
            # Default to 1 URL at a time in Lambda to avoid timeouts
            limit = event.get("limit", 1)
            tags = event.get("tags")

            result = process_queue(db, storage, limit=limit, tags=tags)

            return {
                "statusCode": 200,
                "body": json.dumps(result),
            }

        elif action == "status":
            stats = db.get_youtube_queue_stats()
            return {
                "statusCode": 200,
                "body": json.dumps(stats),
            }

        elif action == "list":
            status_filter = event.get("status")
            items = db.list_youtube_queue(status=status_filter)
            # Convert to serializable format
            result = []
            for item in items:
                result.append({
                    "id": str(item["id"]),
                    "url": item["url"],
                    "status": item["status"],
                    "video_id": item.get("video_id"),
                    "video_title": item.get("video_title"),
                    "channel_name": item.get("channel_name"),
                    "error": item.get("error"),
                })
            return {
                "statusCode": 200,
                "body": json.dumps(result),
            }

        else:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": f"Unknown action: {action}"}),
            }

    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }

    finally:
        if lock_acquired:
            db.release_lock(Database.LOCK_YOUTUBE)
            logger.info("Released YouTube lock")
        db.close()


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="YouTube Importer")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Add URLs command
    add_parser = subparsers.add_parser("add", help="Add URLs to queue")
    add_parser.add_argument("urls", nargs="*", help="YouTube URLs to add")
    add_parser.add_argument("--file", "-f", help="File containing URLs (one per line)")

    # Process command
    process_parser = subparsers.add_parser("process", help="Process pending URLs")
    process_parser.add_argument("--limit", "-l", type=int, default=10, help="Max URLs to process")
    process_parser.add_argument("--tags", help="Comma-separated tags")
    process_parser.add_argument("--bucket", help="S3 bucket (overrides KB_BUCKET)")

    # Status command
    status_parser = subparsers.add_parser("status", help="Show queue status")

    # List command
    list_parser = subparsers.add_parser("list", help="List queue entries")
    list_parser.add_argument("--status", "-s", help="Filter by status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Initialize database
    config = Config.from_env()
    db = Database(config.database_url)
    db.initialize()

    lock_acquired = False
    try:
        if args.command == "add":
            urls = list(args.urls) if args.urls else []

            if args.file:
                with open(args.file) as f:
                    urls.extend(line.strip() for line in f if line.strip())

            if not urls:
                print("No URLs provided")
                sys.exit(1)

            result = add_urls(db, urls)
            print(json.dumps(result, indent=2))

        elif args.command == "process":
            lock_acquired = db.try_acquire_lock(Database.LOCK_YOUTUBE)
            if not lock_acquired:
                print("Another YouTube worker holds the database lease; skipping")
                sys.exit(0)
            bucket = args.bucket or config.bucket
            if not bucket:
                print("Error: bucket required (--bucket or KB_BUCKET env var)")
                sys.exit(1)

            storage = get_storage(bucket)
            tags = args.tags.split(",") if args.tags else None

            result = process_queue(db, storage, limit=args.limit, tags=tags)
            print(json.dumps(result, indent=2))

        elif args.command == "status":
            stats = db.get_youtube_queue_stats()
            print(json.dumps(stats, indent=2))

        elif args.command == "list":
            items = db.list_youtube_queue(status=args.status)
            for item in items:
                print(f"{item['status']:12} {item['url']}")
                if item.get('video_title'):
                    print(f"             {item['video_title']} ({item['channel_name']})")
                if item.get('error'):
                    print(f"             Error: {item['error'][:80]}")

    finally:
        if lock_acquired:
            db.release_lock(Database.LOCK_YOUTUBE)
        db.close()
