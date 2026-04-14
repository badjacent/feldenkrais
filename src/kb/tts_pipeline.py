#!/usr/bin/env python3
"""TTS Pipeline — Generate lesson audio from text using ElevenLabs.

Parses lesson text into instruction segments, generates speech for each,
and assembles with appropriate silences into a complete lesson audio file.

Usage:
    .venv/bin/python src/kb/tts_pipeline.py <document_id> [--dry-run] [--target-minutes 35]

Environment:
    ELEVENLABS_API_KEY — API key
    ELEVENLABS_VOICE_ID — Voice ID to use
    DATABASE_URL — Neon PostgreSQL connection
"""

import argparse
import io
import json
import logging
import os
import re
import struct
import sys
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Defaults ──

DEFAULT_SPEED = 0.7
DEFAULT_STABILITY = 0.6
DEFAULT_SIMILARITY = 0.8
DEFAULT_MODEL = "eleven_multilingual_v2"
SAMPLE_RATE = 44100

# Pause durations (seconds)
PAUSE_REST = 45          # "Lie on your back and rest"
PAUSE_STEP = 15          # Between major numbered steps
PAUSE_SUBSTEP = 8        # Between sub-steps (1a, 1b)
PAUSE_OBSERVATION = 5    # After "Notice...", "Observe..."
PAUSE_SENTENCE = 1.5     # Between sentences within a segment


# ── Segment types ──

class SegType:
    REST = "rest"
    INSTRUCTION = "instruction"
    SUBSTEP = "substep"
    OBSERVATION = "observation"
    META = "meta"  # page numbers, headers — skip


def classify_line(line: str, prev_type: str = "") -> str:
    """Classify a line of lesson text by its function."""
    stripped = line.strip()
    if not stripped:
        return SegType.META

    # Page numbers / headers
    if re.match(r'^Mia Segal.*\d{4}\s+\d+$', stripped):
        return SegType.META

    # Rest markers
    rest_patterns = [
        r'(?:lie|roll)\s+(?:on\s+your\s+back|onto?\s+your\s+back)\s+(?:and\s+)?rest',
        r'^rest\b',
        r'rest\s+(?:on|for)\s+',
        r'lengthen\s+(?:your\s+)?(?:arms\s+and\s+legs|legs\s+and\s+arms)\s+and\s+rest',
        r'stretch\s+out.*rest',
    ]
    for pat in rest_patterns:
        if re.search(pat, stripped, re.IGNORECASE):
            return SegType.REST

    # Sub-step (1a, 2b, etc.)
    if re.match(r'^\d+[a-z]\.', stripped):
        return SegType.SUBSTEP

    # Major step (1., 2., etc.)
    if re.match(r'^\d+\.', stripped):
        return SegType.INSTRUCTION

    # Observation prompts
    obs_starters = ['notice', 'observe', 'how is', 'how are', 'how does',
                    'what happens', 'what is happening', 'what do you',
                    'where do you', 'where would', 'can you feel',
                    'is there', 'is it', 'do you feel', 'feel the',
                    'check your', 'which parts']
    lower = stripped.lower()
    for starter in obs_starters:
        if lower.startswith(starter):
            return SegType.OBSERVATION

    # Questions mid-paragraph
    if '?' in stripped and len(stripped) < 120:
        return SegType.OBSERVATION

    # Default: continuation of previous type
    return prev_type or SegType.INSTRUCTION


def parse_lesson_segments(text: str) -> list[dict]:
    """Parse lesson text into segments with type classification.

    Returns list of dicts: {type, text, pause_after}
    """
    lines = text.split('\n')
    segments = []
    current_text = []
    current_type = SegType.INSTRUCTION
    skip_header_lines = 0

    # Skip header lines (title, source, date)
    for i, line in enumerate(lines):
        if re.match(r'^\d+[\.\s]', line.strip()):
            skip_header_lines = i
            break
        skip_header_lines = i + 1

    for line in lines[skip_header_lines:]:
        line_type = classify_line(line, current_type)

        if line_type == SegType.META:
            continue

        # Type change = new segment boundary
        if line_type != current_type and current_text:
            seg_text = ' '.join(current_text).strip()
            if seg_text:
                segments.append({
                    'type': current_type,
                    'text': seg_text,
                    'pause_after': _pause_for_type(current_type),
                })
            current_text = []

        current_type = line_type
        stripped = line.strip()
        if stripped:
            current_text.append(stripped)

    # Last segment
    if current_text:
        seg_text = ' '.join(current_text).strip()
        if seg_text:
            segments.append({
                'type': current_type,
                'text': seg_text,
                'pause_after': _pause_for_type(current_type),
            })

    return segments


def _pause_for_type(seg_type: str) -> float:
    return {
        SegType.REST: PAUSE_REST,
        SegType.INSTRUCTION: PAUSE_STEP,
        SegType.SUBSTEP: PAUSE_SUBSTEP,
        SegType.OBSERVATION: PAUSE_OBSERVATION,
    }.get(seg_type, PAUSE_SENTENCE)


def add_ellipsis_pauses(text: str) -> str:
    """Add ellipsis pauses to text for more natural TTS delivery.

    Adds '...' after periods and before question words to create
    breathing room in the synthesized speech.
    """
    # Add pauses after sentence-ending periods (but not abbreviations)
    text = re.sub(r'\.(\s+)([A-Z])', r'.\1... \2', text)
    # Add pause before observation questions
    text = re.sub(r'(\.\s+)((?:Notice|Observe|How|What|Where|Which|Can you|Do you|Is there))',
                  r'.\1... \2', text)
    return text


# ── ElevenLabs TTS ──

def generate_speech(text: str, api_key: str, voice_id: str,
                    speed: float = DEFAULT_SPEED,
                    stability: float = DEFAULT_STABILITY,
                    similarity: float = DEFAULT_SIMILARITY,
                    model: str = DEFAULT_MODEL) -> bytes:
    """Generate speech audio from text using ElevenLabs API.

    Returns MP3 bytes.
    """
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    resp = requests.post(url,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": stability,
                "similarity_boost": similarity,
                "speed": speed,
            },
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ElevenLabs API error {resp.status_code}: {resp.text[:200]}")
    return resp.content


def generate_silence_mp3(duration_seconds: float) -> bytes:
    """Generate silent MP3 audio of specified duration.

    Uses ffmpeg to create silence.
    """
    import subprocess
    result = subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', f'anullsrc=r={SAMPLE_RATE}:cl=mono',
        '-t', str(duration_seconds),
        '-c:a', 'libmp3lame', '-b:a', '64k',
        '-f', 'mp3', '-'
    ], capture_output=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg silence generation failed: {result.stderr[:200]}")
    return result.stdout


def concatenate_mp3_files(mp3_chunks: list[bytes], output_path: str):
    """Concatenate MP3 chunks into a single file using ffmpeg."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write chunks to temp files
        file_list = []
        for i, chunk in enumerate(mp3_chunks):
            chunk_path = os.path.join(tmpdir, f"chunk_{i:04d}.mp3")
            with open(chunk_path, 'wb') as f:
                f.write(chunk)
            file_list.append(f"file '{chunk_path}'")

        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, 'w') as f:
            f.write('\n'.join(file_list))

        result = subprocess.run([
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
            '-i', list_path,
            '-c:a', 'libmp3lame', '-b:a', '192k',
            '-ar', str(SAMPLE_RATE), '-ac', '1',
            output_path,
        ], capture_output=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr.decode()[:300]}")


# ── Pipeline step ──

def generate_lesson_audio(
    document_id: str,
    db: Database,
    api_key: str,
    voice_id: str,
    target_minutes: Optional[float] = None,
    output_path: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[str]:
    """Generate audio for a text lesson.

    Returns the output file path, or None if lesson already has audio.
    """
    # Check if document already has audio
    doc = db.get_document(document_id)
    if not doc:
        logger.error(f"Document {document_id} not found")
        return None

    if doc.get('s3_key') and not doc['s3_key'].endswith('.txt'):
        logger.info(f"Document already has audio: {doc['s3_key']}")
        return None

    # Get transcription text
    text = db.get_document_transcription(document_id)
    if not text:
        logger.error(f"No transcription for {document_id}")
        return None

    title = doc.get('title', 'Untitled')
    logger.info(f"Generating audio for: {title}")
    logger.info(f"Text: {len(text)} chars, {len(text.split())} words")

    # Parse into segments
    segments = parse_lesson_segments(text)
    logger.info(f"Parsed {len(segments)} segments:")
    for seg in segments:
        type_label = seg['type'][:5].upper()
        pause = seg['pause_after']
        preview = seg['text'][:60].replace('\n', ' ')
        logger.info(f"  [{type_label:5s}] +{pause:4.0f}s  {preview}...")

    # Calculate timing
    word_count = sum(len(seg['text'].split()) for seg in segments)
    speaking_seconds = word_count / 100 * 60  # ~100 WPM at speed 0.7
    pause_seconds = sum(seg['pause_after'] for seg in segments)
    total_seconds = speaking_seconds + pause_seconds
    total_minutes = total_seconds / 60

    logger.info(f"Estimated: {speaking_seconds/60:.1f}min speech + {pause_seconds/60:.1f}min pauses = {total_minutes:.1f}min")

    # Scale pauses to hit target duration
    if target_minutes and target_minutes > 0:
        target_seconds = target_minutes * 60
        if total_seconds < target_seconds:
            scale = (target_seconds - speaking_seconds) / pause_seconds if pause_seconds > 0 else 1
            logger.info(f"Scaling pauses by {scale:.2f}x to hit {target_minutes}min target")
            for seg in segments:
                seg['pause_after'] *= scale
            total_minutes = target_minutes

    if dry_run:
        logger.info("DRY RUN — would generate audio for these segments")
        return None

    # Generate audio
    output_path = output_path or f"/tmp/lesson_{document_id[:8]}.mp3"
    mp3_chunks = []
    total_chars = 0

    for i, seg in enumerate(segments):
        logger.info(f"  Generating segment {i+1}/{len(segments)} ({len(seg['text'])} chars)...")

        # Add ellipsis pauses to text
        tts_text = add_ellipsis_pauses(seg['text'])
        total_chars += len(tts_text)

        # Generate speech
        try:
            audio = generate_speech(tts_text, api_key, voice_id)
            mp3_chunks.append(audio)
        except Exception as e:
            logger.error(f"  TTS failed for segment {i+1}: {e}")
            continue

        # Generate silence after segment
        if seg['pause_after'] > 0:
            silence = generate_silence_mp3(seg['pause_after'])
            mp3_chunks.append(silence)

        # Rate limit: ElevenLabs allows ~2-3 requests/sec
        time.sleep(0.5)

    if not mp3_chunks:
        logger.error("No audio generated")
        return None

    # Concatenate
    logger.info(f"Concatenating {len(mp3_chunks)} chunks...")
    concatenate_mp3_files(mp3_chunks, output_path)

    # Get duration
    import subprocess
    result = subprocess.run([
        'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
        '-of', 'csv=p=0', output_path
    ], capture_output=True, text=True)
    duration = float(result.stdout.strip()) if result.stdout.strip() else 0

    logger.info(f"Done: {output_path} ({duration/60:.1f} min, {os.path.getsize(output_path)/1e6:.1f} MB)")
    logger.info(f"ElevenLabs characters used: {total_chars}")

    return output_path


# ── CLI ──

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate lesson audio from text via ElevenLabs")
    parser.add_argument("document_id", help="Document UUID")
    parser.add_argument("--dry-run", action="store_true", help="Parse and plan only, don't generate")
    parser.add_argument("--target-minutes", type=float, default=None, help="Target lesson duration in minutes")
    parser.add_argument("--output", type=str, default=None, help="Output file path")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED, help=f"Speech speed (default {DEFAULT_SPEED})")
    args = parser.parse_args()

    # Load env
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
    db_url = os.environ.get("DATABASE_URL")

    if not api_key:
        print("Error: ELEVENLABS_API_KEY not set in .env")
        sys.exit(1)
    if not voice_id:
        print("Error: ELEVENLABS_VOICE_ID not set in .env")
        sys.exit(1)
    if not db_url:
        print("Error: DATABASE_URL not set in .env")
        sys.exit(1)

    DEFAULT_SPEED = args.speed
    db = Database(db_url)

    result = generate_lesson_audio(
        document_id=args.document_id,
        db=db,
        api_key=api_key,
        voice_id=voice_id,
        target_minutes=args.target_minutes,
        output_path=args.output,
        dry_run=args.dry_run,
    )

    if result:
        print(f"\nOutput: {result}")
