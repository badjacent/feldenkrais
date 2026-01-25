"""
Audio preprocessing utilities.

Handles compression and chunking of audio files for transcription.
"""

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 20MB threshold (safe margin under 25MB limit)
DEFAULT_MAX_SIZE_BYTES = 20 * 1024 * 1024
DEFAULT_CHUNK_DURATION_SECONDS = 1200  # 20 minutes


@dataclass
class AudioChunk:
    """Represents a chunk of audio."""
    path: str
    index: int
    offset_seconds: float
    size_bytes: int


def get_audio_duration(file_path: str) -> Optional[float]:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        logger.error(f"Failed to get duration for {file_path}: {e}")
        return None


def compress_audio(
    input_path: str,
    output_path: str,
    bitrate: str = "64k",
    sample_rate: int = 16000,
) -> bool:
    """
    Compress audio to mono MP3 at specified bitrate.

    Returns True on success.
    """
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-ac", "1",  # Mono
                "-ab", bitrate,
                "-ar", str(sample_rate),
                output_path
            ],
            capture_output=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to compress {input_path}: {e.stderr.decode()}")
        return False


def split_audio(
    input_path: str,
    output_dir: str,
    chunk_duration_seconds: int = DEFAULT_CHUNK_DURATION_SECONDS,
    compress: bool = True,
    bitrate: str = "64k",
    sample_rate: int = 16000,
) -> list[AudioChunk]:
    """
    Split audio into chunks of specified duration.

    Optionally compresses while splitting for smaller chunk sizes.

    Returns list of AudioChunk objects.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get input filename without extension
    input_name = Path(input_path).stem

    # Build ffmpeg command
    cmd = ["ffmpeg", "-y", "-i", input_path]

    if compress:
        cmd.extend(["-ac", "1", "-ab", bitrate, "-ar", str(sample_rate)])
    else:
        cmd.extend(["-c", "copy"])

    output_pattern = str(output_dir / f"{input_name}_chunk_%03d.mp3")
    cmd.extend([
        "-f", "segment",
        "-segment_time", str(chunk_duration_seconds),
        "-reset_timestamps", "1",
        output_pattern
    ])

    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to split {input_path}: {e.stderr.decode()}")
        return []

    # Collect chunks
    chunks = []
    for i, chunk_path in enumerate(sorted(output_dir.glob(f"{input_name}_chunk_*.mp3"))):
        size = chunk_path.stat().st_size
        offset = i * chunk_duration_seconds
        chunks.append(AudioChunk(
            path=str(chunk_path),
            index=i,
            offset_seconds=offset,
            size_bytes=size,
        ))

    logger.info(f"Split {input_path} into {len(chunks)} chunks")
    return chunks


def needs_chunking(file_path: str, max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES) -> bool:
    """Check if a file needs to be chunked based on size."""
    size = Path(file_path).stat().st_size
    return size > max_size_bytes


def prepare_audio_for_transcription(
    input_path: str,
    output_dir: Optional[str] = None,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
    chunk_duration_seconds: int = DEFAULT_CHUNK_DURATION_SECONDS,
) -> list[AudioChunk]:
    """
    Prepare audio file for transcription.

    If file is under max_size, returns single chunk (may compress if beneficial).
    If file is over max_size, splits into multiple chunks.

    Returns list of AudioChunk objects ready for transcription.
    """
    input_path = Path(input_path)
    original_size = input_path.stat().st_size

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="audio_chunks_")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if original_size <= max_size_bytes:
        # File is small enough, just return as-is (or compress if helpful)
        logger.info(f"File {input_path} is {original_size} bytes, no chunking needed")
        return [AudioChunk(
            path=str(input_path),
            index=0,
            offset_seconds=0,
            size_bytes=original_size,
        )]

    # Need to split
    logger.info(f"File {input_path} is {original_size} bytes, chunking...")
    return split_audio(
        str(input_path),
        str(output_dir),
        chunk_duration_seconds=chunk_duration_seconds,
        compress=True,  # Always compress when chunking
    )


def reassemble_transcriptions(
    chunk_results: list[dict],
    chunk_offsets: list[float],
    overlap_seconds: float = 0,
) -> dict:
    """
    Reassemble transcriptions from multiple chunks.

    Args:
        chunk_results: List of transcription results (each with 'text' and 'segments')
        chunk_offsets: Offset in seconds for each chunk
        overlap_seconds: Overlap between chunks to deduplicate

    Returns combined transcription with adjusted timestamps.
    """
    full_text_parts = []
    full_segments = []

    for i, (result, offset) in enumerate(zip(chunk_results, chunk_offsets)):
        text = result.get("text", "")
        segments = result.get("segments", [])

        # Adjust segment timestamps
        for segment in segments:
            adjusted_segment = segment.copy()
            adjusted_segment["start"] = segment.get("start", 0) + offset
            adjusted_segment["end"] = segment.get("end", 0) + offset

            # Skip segments in overlap region (except for first chunk)
            if i > 0 and overlap_seconds > 0:
                if adjusted_segment["start"] < offset + overlap_seconds:
                    continue

            full_segments.append(adjusted_segment)

        full_text_parts.append(text.strip())

    return {
        "text": " ".join(full_text_parts),
        "segments": full_segments,
    }
