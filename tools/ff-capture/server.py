#!/usr/bin/env python3
"""
Feldenkrais Capture Server — Local HTTP server for receiving captures from the
Chrome extension and downloading audio via ffmpeg (SoundCloud), yt-dlp (Vimeo),
or direct HTTP download (Feldenkrais Project).

Usage:
    python tools/ff-capture/server.py

Listens on http://localhost:7890
Output directory: ~/feldenkrais-inbox/
"""

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import urlopen, Request

import boto3

HOST = "localhost"
PORT = 7890
OUTPUT_DIR = Path.home() / "feldenkrais-inbox"
S3_BUCKET = "feldenkrais-prod"

# Tool availability (checked at startup)
FFMPEG_OK = False
FFPROBE_OK = False
YTDLP_OK = False
S3_OK = False
s3_client = None


def check_ffmpeg():
    global FFMPEG_OK, FFPROBE_OK
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5
        )
        version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
        FFMPEG_OK = result.returncode == 0
        if FFMPEG_OK:
            print(f"  ffmpeg: {version_line}")
    except FileNotFoundError:
        print("  ffmpeg: NOT FOUND — SoundCloud download will fail")
        print("  Install with: brew install ffmpeg")

    try:
        result = subprocess.run(
            ["ffprobe", "-version"], capture_output=True, text=True, timeout=5
        )
        FFPROBE_OK = result.returncode == 0
        if FFPROBE_OK:
            print("  ffprobe: OK")
    except FileNotFoundError:
        print("  ffprobe: not found (duration detection disabled)")


def check_ytdlp():
    global YTDLP_OK
    try:
        result = subprocess.run(
            ["yt-dlp", "--version"], capture_output=True, text=True, timeout=5
        )
        version = result.stdout.strip() if result.stdout else "unknown"
        YTDLP_OK = result.returncode == 0
        if YTDLP_OK:
            print(f"  yt-dlp: {version}")
    except FileNotFoundError:
        print("  yt-dlp: NOT FOUND — Vimeo download will fail")
        print("  Install with: brew install yt-dlp")


def check_s3():
    global S3_OK, s3_client
    try:
        s3_client = boto3.client("s3")
        s3_client.head_bucket(Bucket=S3_BUCKET)
        S3_OK = True
        print(f"  S3: {S3_BUCKET} (staging enabled)")
    except Exception as e:
        print(f"  S3: NOT AVAILABLE — {e}")
        print("  Files will be saved locally only")


def upload_to_staging(audio_path, sidecar, track_id):
    """Upload audio + sidecar JSON to S3 staging/ for loader Lambda."""
    if not S3_OK:
        print("  S3: skipped (not available)")
        return

    filename = f"ff-{track_id}.mp3"
    staging_key = f"staging/{filename}"
    sidecar_key = f"staging/{filename}.json"

    try:
        print(f"  S3: uploading {staging_key}")
        s3_client.upload_file(str(audio_path), S3_BUCKET, staging_key)
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=sidecar_key,
            Body=json.dumps(sidecar).encode(),
            ContentType="application/json",
        )
        print(f"  S3: staged → loader Lambda will pick it up")
    except Exception as e:
        print(f"  S3: upload failed — {e}")


# --- ID extraction ---

def extract_track_id(manifest_url):
    """Extract the SoundCloud track ID from an HLS manifest URL.

    Example URL path: /playlist/zbugQTrGGaUi.128.mp3/playlist.m3u8
    Returns: zbugQTrGGaUi
    """
    path = urlparse(manifest_url).path
    match = re.search(r"/playlist/([^./]+)\.", path)
    return match.group(1) if match else None


def extract_vimeo_id(vimeo_url):
    """Extract the Vimeo video ID from an embed or player URL.

    Example: https://player.vimeo.com/video/123456789?h=abc123
    Returns: 123456789
    """
    path = urlparse(vimeo_url).path
    match = re.search(r"/video/(\d+)", path)
    return match.group(1) if match else None


# --- Audio duration ---

def get_audio_duration(filepath):
    """Get audio duration in seconds using ffprobe, or None if unavailable."""
    if not FFPROBE_OK:
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(filepath),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return round(float(result.stdout.strip()), 1)
    except Exception:
        pass
    return None


# --- Download handlers ---

def download_and_stage(manifest_url, metadata):
    """Download SoundCloud HLS audio via ffmpeg and write sidecar files."""
    track_id = extract_track_id(manifest_url) or "unknown"
    timestamp = datetime.now(timezone.utc)
    dir_name = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_{track_id}"
    capture_dir = OUTPUT_DIR / dir_name
    capture_dir.mkdir(parents=True, exist_ok=True)

    audio_path = capture_dir / "audio.mp3"
    sidecar_path = capture_dir / "audio.mp3.json"
    info_path = capture_dir / "capture_info.json"
    text_path = capture_dir / "lesson_text.txt"

    title = metadata.get("title") or f"Untitled ({track_id})"
    page_url = metadata.get("page_url") or ""
    instructor = metadata.get("instructor")
    description = metadata.get("description")
    lesson_text = metadata.get("lesson_text")

    print(f"  Downloading: {title}")
    print(f"  Track ID:    {track_id}")
    print(f"  Output:      {capture_dir}")

    # Download audio
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i", manifest_url,
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                "-vn",
                "-y",
                str(audio_path),
            ],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            print(f"  ERROR: ffmpeg failed (exit {result.returncode})")
            if result.stderr:
                for line in result.stderr.strip().split("\n")[-5:]:
                    print(f"    {line}")
            return
    except subprocess.TimeoutExpired:
        print("  ERROR: ffmpeg timed out (5 min)")
        return

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        print("  ERROR: audio file is empty or missing")
        return

    file_size = audio_path.stat().st_size
    duration = get_audio_duration(audio_path)

    print(f"  Downloaded:  {file_size / 1_000_000:.1f} MB", end="")
    if duration:
        print(f" ({duration:.0f}s)")
    else:
        print()

    # Save lesson text if available
    has_lesson_text = bool(lesson_text and lesson_text.strip())
    if has_lesson_text:
        text_path.write_text(lesson_text.strip(), encoding="utf-8")
        print(f"  Lesson text: {len(lesson_text)} chars")

    # Write sidecar JSON (pipeline-compatible format)
    sidecar = {
        "title": title,
        "source_url": page_url,
        "source_id": 9,  # FELDENKRAIS_FIRST
        "type_id": 2,    # AUDIO
        "tags": ["feldenkrais-first"],
        "scraper_metadata": {
            "instructor": instructor,
            "description": description,
            "soundcloud_track_id": track_id,
            "manifest_url": manifest_url,
            "captured_at": timestamp.isoformat(),
            "source": "app.feldenkraisfirst.com",
            "page_url": page_url,
            "has_lesson_text": has_lesson_text,
            "lesson_text_file": "lesson_text.txt" if has_lesson_text else None,
            "audio_duration_seconds": duration,
            "audio_file_size_bytes": file_size,
        },
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    # Write raw capture info for debugging
    info = {
        "manifest_url": manifest_url,
        "metadata": metadata,
        "captured_at": timestamp.isoformat(),
        "track_id": track_id,
    }
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    upload_to_staging(audio_path, sidecar, track_id)
    print(f"  Done: {dir_name}")


def download_vimeo_and_stage(vimeo_url, metadata):
    """Download Vimeo video audio via yt-dlp and write sidecar files."""
    video_id = extract_vimeo_id(vimeo_url) or "unknown"
    timestamp = datetime.now(timezone.utc)
    dir_name = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_vimeo_{video_id}"
    capture_dir = OUTPUT_DIR / dir_name
    capture_dir.mkdir(parents=True, exist_ok=True)

    audio_path = capture_dir / "audio.mp3"
    sidecar_path = capture_dir / "audio.mp3.json"
    info_path = capture_dir / "capture_info.json"
    text_path = capture_dir / "lesson_text.txt"

    title = metadata.get("title") or f"Untitled (vimeo-{video_id})"
    page_url = metadata.get("page_url") or ""
    instructor = metadata.get("instructor")
    description = metadata.get("description")
    lesson_text = metadata.get("lesson_text")

    print(f"  Downloading: {title}")
    print(f"  Vimeo ID:    {video_id}")
    print(f"  Output:      {capture_dir}")

    # Download audio using yt-dlp
    # Output template uses %(ext)s — yt-dlp will produce audio.mp3 after conversion
    output_template = str(capture_dir / "audio.%(ext)s")
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "192K",
                "--no-playlist",
                "--referer", "https://app.feldenkraisfirst.com",
                "-o", output_template,
                vimeo_url,
            ],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            print(f"  ERROR: yt-dlp failed (exit {result.returncode})")
            if result.stderr:
                for line in result.stderr.strip().split("\n")[-5:]:
                    print(f"    {line}")
            return
    except subprocess.TimeoutExpired:
        print("  ERROR: yt-dlp timed out (10 min)")
        return

    # yt-dlp may name the file slightly differently; find the mp3
    if not audio_path.exists():
        mp3_files = list(capture_dir.glob("*.mp3"))
        if mp3_files:
            mp3_files[0].rename(audio_path)

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        print("  ERROR: audio file is empty or missing")
        return

    file_size = audio_path.stat().st_size
    duration = get_audio_duration(audio_path)

    print(f"  Downloaded:  {file_size / 1_000_000:.1f} MB", end="")
    if duration:
        print(f" ({duration:.0f}s)")
    else:
        print()

    # Save lesson text if available
    has_lesson_text = bool(lesson_text and lesson_text.strip())
    if has_lesson_text:
        text_path.write_text(lesson_text.strip(), encoding="utf-8")
        print(f"  Lesson text: {len(lesson_text)} chars")

    # Write sidecar JSON (pipeline-compatible format)
    sidecar = {
        "title": title,
        "source_url": page_url,
        "source_id": 9,  # FELDENKRAIS_FIRST
        "type_id": 2,    # AUDIO (extracted from video)
        "tags": ["feldenkrais-first"],
        "scraper_metadata": {
            "instructor": instructor,
            "description": description,
            "vimeo_video_id": video_id,
            "vimeo_url": vimeo_url,
            "captured_at": timestamp.isoformat(),
            "source": "app.feldenkraisfirst.com",
            "page_url": page_url,
            "has_lesson_text": has_lesson_text,
            "lesson_text_file": "lesson_text.txt" if has_lesson_text else None,
            "audio_duration_seconds": duration,
            "audio_file_size_bytes": file_size,
        },
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    # Write raw capture info for debugging
    info = {
        "vimeo_url": vimeo_url,
        "metadata": metadata,
        "captured_at": timestamp.isoformat(),
        "video_id": video_id,
    }
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    upload_to_staging(audio_path, sidecar, f"vimeo-{video_id}")
    print(f"  Done: {dir_name}")


def download_direct_and_stage(audio_url, metadata):
    """Download a direct MP3 URL (Feldenkrais Project) and write sidecar files."""
    # Extract filename from URL for ID
    parsed = urlparse(audio_url)
    filename = unquote(Path(parsed.path).name)
    file_id = re.sub(r'[^\w\-_.]', '_', Path(filename).stem) or "unknown"

    timestamp = datetime.now(timezone.utc)
    dir_name = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_fp_{file_id}"
    capture_dir = OUTPUT_DIR / dir_name
    capture_dir.mkdir(parents=True, exist_ok=True)

    audio_path = capture_dir / "audio.mp3"
    sidecar_path = capture_dir / "audio.mp3.json"
    info_path = capture_dir / "capture_info.json"

    title = metadata.get("title") or f"Untitled ({file_id})"
    page_url = metadata.get("page_url") or ""
    description = metadata.get("description")
    duration_text = metadata.get("duration")

    print(f"  Downloading: {title}")
    print(f"  Audio URL:   {audio_url}")
    print(f"  Output:      {capture_dir}")

    # Download MP3 via plain HTTP GET
    try:
        req = Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=300) as resp:
            content = resp.read()
        audio_path.write_bytes(content)
    except Exception as e:
        print(f"  ERROR: download failed — {e}")
        return

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        print("  ERROR: audio file is empty or missing")
        return

    file_size = audio_path.stat().st_size
    duration = get_audio_duration(audio_path)

    print(f"  Downloaded:  {file_size / 1_000_000:.1f} MB", end="")
    if duration:
        print(f" ({duration:.0f}s)")
    else:
        print()

    # Write sidecar JSON (pipeline-compatible format)
    sidecar = {
        "title": title,
        "source_url": page_url,
        "source_id": 4,  # FELDENKRAIS_PROJECT
        "type_id": 2,    # AUDIO
        "tags": ["feldenkrais-project"],
        "scraper_metadata": {
            "description": description,
            "audio_url": audio_url,
            "original_filename": filename,
            "duration_text": duration_text,
            "captured_at": timestamp.isoformat(),
            "source": "feldenkraisproject.com",
            "page_url": page_url,
            "audio_duration_seconds": duration,
            "audio_file_size_bytes": file_size,
        },
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    # Write raw capture info for debugging
    info = {
        "audio_url": audio_url,
        "metadata": metadata,
        "captured_at": timestamp.isoformat(),
        "file_id": file_id,
    }
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    upload_to_staging(audio_path, sidecar, f"fp-{file_id}")
    print(f"  Done: {dir_name}")


# --- HTTP Handler ---

class CaptureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {
                "status": "ok",
                "ffmpeg": FFMPEG_OK,
                "ytdlp": YTDLP_OK,
                "s3": S3_OK,
            })
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/capture":
            self.handle_capture()
        else:
            self.send_json(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def handle_capture(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self.send_json(400, {"error": f"invalid JSON: {e}"})
            return

        manifest_url = data.get("manifest_url")
        vimeo_url = data.get("vimeo_url")
        audio_url = data.get("audio_url")
        metadata = data.get("metadata", {})
        title = metadata.get("title", "(unknown)")

        if vimeo_url:
            # Vimeo video → extract audio with yt-dlp
            if not YTDLP_OK:
                self.send_json(503, {"error": "yt-dlp not available"})
                return

            print(f"\n[CAPTURE:VIMEO] {title}")
            self.send_json(202, {"status": "accepted", "type": "vimeo", "title": title})

            thread = threading.Thread(
                target=download_vimeo_and_stage,
                args=(vimeo_url, metadata),
                daemon=True,
            )
            thread.start()

        elif audio_url:
            # Direct MP3 URL (Feldenkrais Project)
            print(f"\n[CAPTURE:FP] {title}")
            self.send_json(202, {"status": "accepted", "type": "fp", "title": title})

            thread = threading.Thread(
                target=download_direct_and_stage,
                args=(audio_url, metadata),
                daemon=True,
            )
            thread.start()

        elif manifest_url:
            # SoundCloud HLS → download with ffmpeg
            if not FFMPEG_OK:
                self.send_json(503, {"error": "ffmpeg not available"})
                return

            print(f"\n[CAPTURE:SOUNDCLOUD] {title}")
            self.send_json(202, {"status": "accepted", "type": "soundcloud", "title": title})

            thread = threading.Thread(
                target=download_and_stage,
                args=(manifest_url, metadata),
                daemon=True,
            )
            thread.start()

        else:
            self.send_json(400, {"error": "missing manifest_url, vimeo_url, or audio_url"})

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format, *args):
        if len(args) >= 1 and "GET /health" in str(args[0]):
            return
        super().log_message(format, *args)


def main():
    print(f"Feldenkrais Capture Server")
    print(f"  Listen:  http://{HOST}:{PORT}")
    print(f"  Output:  {OUTPUT_DIR}")
    print()

    check_ffmpeg()
    check_ytdlp()
    check_s3()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print(f"Ready — waiting for captures...")
    print()

    server = HTTPServer((HOST, PORT), CaptureHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
