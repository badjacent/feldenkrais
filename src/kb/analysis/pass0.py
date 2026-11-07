"""Pass 0: Text Segmentation.

Rule-based segmentation of lesson text into sentence-level chunks.
"""

import re

# Whisper sometimes returns long stretches with no punctuation at all. A
# segment is the smallest unit a step can own, so one huge segment would be
# shared by many steps. Segments longer than LONG_SEGMENT_CHARS are broken at
# spaces into pieces of about SPLIT_TARGET_CHARS.
LONG_SEGMENT_CHARS = 600
SPLIT_TARGET_CHARS = 250


def _split_long(segment: dict, cleaned: str) -> list[dict]:
    """Break one over-long segment at spaces; joined with spaces it is unchanged."""
    text = segment["text"]
    if len(text) <= LONG_SEGMENT_CHARS:
        return [segment]
    raw = cleaned[segment["start"]:segment["end"]]
    base = segment["start"] + (len(raw) - len(raw.lstrip()))
    pieces, start = [], 0
    while len(text) - start > LONG_SEGMENT_CHARS:
        cut = text.find(" ", start + SPLIT_TARGET_CHARS)
        if cut < 0 or len(text) - cut < SPLIT_TARGET_CHARS // 2:
            break
        pieces.append((start, cut))
        start = cut + 1
    pieces.append((start, len(text)))
    return [{"id": 0, "text": text[a:b], "start": base + a, "end": base + b} for a, b in pieces]


def run_pass_0(text: str) -> dict:
    """
    Pass 0: Text Segmentation.

    Segments lesson text into sentence-level chunks with character offsets.
    Uses rule-based segmentation optimized for Feldenkrais lesson text patterns.

    Returns:
        {
            "segments": [{"id": 1, "text": "...", "start": 0, "end": 50}, ...],
            "original_length": 1234
        }
    """
    # Clean the text - normalize whitespace but preserve structure
    cleaned = text.strip()

    # Feldenkrais lessons often use "..." as instruction separators
    # Split on: sentence endings (. ! ?) followed by space/newline, or "..." patterns

    segments = []
    segment_id = 1

    # Use regex to find sentence boundaries
    sentence_pattern = re.compile(
        r'([.!?](?:\s|$))|'  # Standard sentence endings
        r'(\.{2,}\s*)|'       # Ellipsis (2+ dots)
        r'(\n\s*\n)|'         # Paragraph breaks
        r'(\n(?=\d+\.\s))'    # Newline before numbered step
    )

    last_end = 0

    for match in sentence_pattern.finditer(cleaned):
        # Get the text from last_end to end of this match
        end_pos = match.end()
        segment_text = cleaned[last_end:end_pos].strip()

        if segment_text and len(segment_text) > 2:  # Skip tiny fragments
            segments.append({
                "id": segment_id,
                "text": segment_text,
                "start": last_end,
                "end": end_pos
            })
            segment_id += 1

        last_end = end_pos

    # Don't forget the last segment if text doesn't end with punctuation
    if last_end < len(cleaned):
        remaining = cleaned[last_end:].strip()
        if remaining and len(remaining) > 2:
            segments.append({
                "id": segment_id,
                "text": remaining,
                "start": last_end,
                "end": len(cleaned)
            })

    # If segmentation produced too few segments, fall back to simpler approach
    if len(segments) < 3 and len(cleaned) > 200:
        # Fall back: split on any sentence-ending punctuation
        segments = []
        segment_id = 1
        last_end = 0

        for match in re.finditer(r'[.!?]+\s*', cleaned):
            end_pos = match.end()
            segment_text = cleaned[last_end:end_pos].strip()
            if segment_text:
                segments.append({
                    "id": segment_id,
                    "text": segment_text,
                    "start": last_end,
                    "end": end_pos
                })
                segment_id += 1
            last_end = end_pos

        if last_end < len(cleaned):
            remaining = cleaned[last_end:].strip()
            if remaining:
                segments.append({
                    "id": segment_id,
                    "text": remaining,
                    "start": last_end,
                    "end": len(cleaned)
                })

    segments = [piece for segment in segments for piece in _split_long(segment, cleaned)]
    for number, segment in enumerate(segments, start=1):
        segment["id"] = number

    return {
        "segments": segments,
        "original_length": len(cleaned)
    }


def format_text_with_segments(pass_0_result: dict) -> str:
    """
    Format text with segment markers for LLM consumption.

    Returns text like:
    [1] Lie on your back on the floor.
    [2] Bend your knees so your feet are standing.
    ...
    """
    lines = []
    for seg in pass_0_result.get("segments", []):
        lines.append(f"[{seg['id']}] {seg['text']}")
    return "\n".join(lines)
