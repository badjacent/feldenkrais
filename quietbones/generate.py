#!/usr/bin/env python3
"""QuietBones — Generate exploration audio from markdown.

Parses an exploration markdown file into spoken segments and silences,
generates speech via ElevenLabs, and assembles into a single audio file.

Usage:
    python generate.py explorations/001_behind_the_bone.md [--dry-run] [--output out.mp3]

Environment:
    ELEVENLABS_API_KEY — API key
    ELEVENLABS_VOICE_ID — Voice ID (browse https://elevenlabs.io/voice-library/meditation)
"""

import argparse
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Voice settings ──

DEFAULT_SPEED = 0.78
DEFAULT_STABILITY = 0.78
DEFAULT_SIMILARITY = 0.75
DEFAULT_MODEL = "eleven_multilingual_v2"
SAMPLE_RATE = 44100

# ── Pause durations (seconds) ──
# The `...` markers in exploration files become silence.
# Longer pauses at structural boundaries.

PAUSE_DEFAULT = 8        # Standard pause between lines (the `...` marker)
PAUSE_LONG = 12          # After a section that needs settling time
PAUSE_ENDING = 20        # Final rest


# ── Parsing ──

def parse_exploration(text: str) -> list[dict]:
    """Parse exploration markdown into segments.

    Returns list of dicts: {type: "speech"|"silence", text?, duration?}

    Rules:
    - Lines starting with #, ##, or --- are skipped (metadata)
    - Lines starting with *...* (italic) are stage directions, skipped
    - `...` on its own line = silence
    - Everything else = speech
    - Consecutive speech lines are joined into one segment
    """
    lines = text.split('\n')
    segments = []
    speech_buffer = []

    def flush_speech():
        if speech_buffer:
            joined = ' '.join(speech_buffer).strip()
            if joined:
                segments.append({'type': 'speech', 'text': joined})
            speech_buffer.clear()

    for line in lines:
        stripped = line.strip()

        # Skip metadata
        if not stripped:
            continue
        if stripped.startswith('#') or stripped == '---':
            flush_speech()
            continue
        if stripped.startswith('*') and stripped.endswith('*'):
            flush_speech()
            continue

        # Silence marker
        if stripped == '...':
            flush_speech()
            segments.append({'type': 'silence', 'duration': PAUSE_DEFAULT})
            continue

        # Speech
        speech_buffer.append(stripped)

    flush_speech()

    # Make the last silence longer (settling time)
    for i in range(len(segments) - 1, -1, -1):
        if segments[i]['type'] == 'silence':
            segments[i]['duration'] = PAUSE_ENDING
            break

    return segments


def estimate_duration(segments: list[dict]) -> dict:
    """Estimate total duration of the exploration."""
    speech_words = sum(
        len(seg['text'].split()) for seg in segments if seg['type'] == 'speech'
    )
    # At speed ~0.78, roughly 110 WPM
    speech_seconds = speech_words / 110 * 60
    silence_seconds = sum(
        seg['duration'] for seg in segments if seg['type'] == 'silence'
    )
    return {
        'speech_seconds': speech_seconds,
        'silence_seconds': silence_seconds,
        'total_seconds': speech_seconds + silence_seconds,
        'total_minutes': (speech_seconds + silence_seconds) / 60,
        'speech_segments': sum(1 for s in segments if s['type'] == 'speech'),
        'silence_segments': sum(1 for s in segments if s['type'] == 'silence'),
        'word_count': speech_words,
        'char_count': sum(len(seg['text']) for seg in segments if seg['type'] == 'speech'),
    }


# ── ElevenLabs TTS ──

def generate_speech(
    text: str,
    api_key: str,
    voice_id: str,
    speed: float = DEFAULT_SPEED,
    stability: float = DEFAULT_STABILITY,
    similarity: float = DEFAULT_SIMILARITY,
    model: str = DEFAULT_MODEL,
    previous_text: str | None = None,
    next_text: str | None = None,
) -> bytes:
    """Generate speech audio via ElevenLabs. Returns MP3 bytes."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = {
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity,
            "speed": speed,
        },
    }
    # Request stitching for prosody continuity
    if previous_text:
        body["previous_text"] = previous_text
    if next_text:
        body["next_text"] = next_text

    resp = requests.post(
        url,
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ElevenLabs API error {resp.status_code}: {resp.text[:200]}")
    return resp.content


def generate_silence(duration_seconds: float) -> bytes:
    """Generate silent MP3 of specified duration via ffmpeg."""
    result = subprocess.run(
        [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'anullsrc=r={SAMPLE_RATE}:cl=mono',
            '-t', str(duration_seconds),
            '-c:a', 'libmp3lame', '-b:a', '64k',
            '-f', 'mp3', '-',
        ],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg silence failed: {result.stderr[:200]}")
    return result.stdout


def concatenate_mp3(chunks: list[bytes], output_path: str):
    """Concatenate MP3 chunks into a single file via ffmpeg."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_list = []
        for i, chunk in enumerate(chunks):
            path = os.path.join(tmpdir, f"chunk_{i:04d}.mp3")
            with open(path, 'wb') as f:
                f.write(chunk)
            file_list.append(f"file '{path}'")

        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, 'w') as f:
            f.write('\n'.join(file_list))

        result = subprocess.run(
            [
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', list_path,
                '-c:a', 'libmp3lame', '-b:a', '192k',
                '-ar', str(SAMPLE_RATE), '-ac', '1',
                output_path,
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr.decode()[:300]}")


# ── Main pipeline ──

def slow_down_audio(input_path: str, output_path: str, factor: float):
    """Slow down audio by a factor (e.g. 0.3 = 30% slower) using ffmpeg atempo.

    Only slows speech segments — silences are already the right length.
    This is a simple whole-file slowdown as a post-processing step.
    """
    atempo = 1.0 - factor  # 0.3 slower -> atempo=0.7
    # atempo filter only accepts 0.5-2.0, so chain if needed
    if atempo < 0.5:
        # Chain two filters: e.g. 0.4 -> 0.63 * 0.63
        import math
        single = math.sqrt(atempo)
        af = f"atempo={single:.4f},atempo={single:.4f}"
    else:
        af = f"atempo={atempo:.4f}"

    result = subprocess.run(
        [
            'ffmpeg', '-y', '-i', input_path,
            '-filter:a', af,
            '-c:a', 'libmp3lame', '-b:a', '192k',
            output_path,
        ],
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg slowdown failed: {result.stderr.decode()[:300]}")


def generate_exploration(
    markdown_path: str,
    api_key: str,
    voice_id: str,
    output_path: str | None = None,
    dry_run: bool = False,
    slow_factor: float = 0.0,
) -> str | None:
    """Generate audio for an exploration markdown file.

    Returns output file path, or None for dry run.
    """
    md_path = Path(markdown_path)
    if not md_path.exists():
        logger.error(f"File not found: {md_path}")
        return None

    text = md_path.read_text()
    segments = parse_exploration(text)
    est = estimate_duration(segments)

    logger.info(f"Exploration: {md_path.stem}")
    logger.info(f"  {est['speech_segments']} speech segments, {est['silence_segments']} silences")
    logger.info(f"  {est['word_count']} words, {est['char_count']} chars")
    logger.info(f"  Estimated: {est['speech_seconds']/60:.1f}min speech + {est['silence_seconds']/60:.1f}min silence = {est['total_minutes']:.1f}min")

    if dry_run:
        logger.info("DRY RUN — segments:")
        for i, seg in enumerate(segments):
            if seg['type'] == 'speech':
                preview = seg['text'][:70].replace('\n', ' ')
                logger.info(f"  [{i:2d}] SPEECH  {preview}...")
            else:
                logger.info(f"  [{i:2d}] SILENCE {seg['duration']}s")
        return None

    output_path = output_path or f"output/{md_path.stem}.mp3"
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    # Collect speech texts for request stitching context
    speech_texts = [seg['text'] for seg in segments if seg['type'] == 'speech']

    mp3_chunks = []
    speech_idx = 0

    for i, seg in enumerate(segments):
        if seg['type'] == 'silence':
            logger.info(f"  Silence: {seg['duration']}s")
            mp3_chunks.append(generate_silence(seg['duration']))
            continue

        # Speech segment with request stitching
        prev_text = speech_texts[speech_idx - 1] if speech_idx > 0 else None
        next_text = speech_texts[speech_idx + 1] if speech_idx < len(speech_texts) - 1 else None

        logger.info(f"  Generating speech {speech_idx + 1}/{len(speech_texts)}: {seg['text'][:50]}...")
        audio = generate_speech(
            seg['text'],
            api_key,
            voice_id,
            previous_text=prev_text,
            next_text=next_text,
        )
        mp3_chunks.append(audio)
        speech_idx += 1

        # Rate limit
        time.sleep(0.5)

    if not mp3_chunks:
        logger.error("No audio generated")
        return None

    logger.info(f"Concatenating {len(mp3_chunks)} chunks...")
    if slow_factor > 0:
        # Concatenate to temp, then slow down to final output
        temp_path = output_path + ".tmp.mp3"
        concatenate_mp3(mp3_chunks, temp_path)
        logger.info(f"Slowing down by {slow_factor:.0%}...")
        slow_down_audio(temp_path, output_path, slow_factor)
        os.remove(temp_path)
    else:
        concatenate_mp3(mp3_chunks, output_path)

    # Report duration
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', output_path],
        capture_output=True, text=True,
    )
    duration = float(result.stdout.strip()) if result.stdout.strip() else 0
    size_mb = os.path.getsize(output_path) / 1e6

    logger.info(f"Done: {output_path} ({duration/60:.1f}min, {size_mb:.1f}MB)")
    logger.info(f"ElevenLabs chars used: {est['char_count']}")

    return output_path


# ── CLI ──

def _load_env():
    """Load .env from project root."""
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate QuietBones exploration audio")
    parser.add_argument("exploration", help="Path to exploration markdown file")
    parser.add_argument("--dry-run", action="store_true", help="Parse and estimate only")
    parser.add_argument("--output", type=str, default=None, help="Output file path")
    parser.add_argument("--voice", type=str, default=None, help="ElevenLabs voice ID (overrides .env)")
    parser.add_argument("--slow", type=float, default=0.0, help="Slow speech down by this fraction (e.g. 0.3 = 30%% slower)")
    args = parser.parse_args()

    _load_env()

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = args.voice or os.environ.get("ELEVENLABS_VOICE_ID")

    if not api_key:
        print("Error: ELEVENLABS_API_KEY not set")
        sys.exit(1)
    if not voice_id:
        print("Error: ELEVENLABS_VOICE_ID not set")
        sys.exit(1)

    result = generate_exploration(
        markdown_path=args.exploration,
        api_key=api_key,
        voice_id=voice_id,
        output_path=args.output,
        dry_run=args.dry_run,
        slow_factor=args.slow,
    )
    if result:
        print(f"\nOutput: {result}")
