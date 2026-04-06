#!/usr/bin/env python3
"""QuietBones — Regenerate a section of an exploration.

Assembles context (full text, design principles, source analysis) and asks
Claude to regenerate a selected range of lines with an optional reason.

Can be used as a CLI or imported as a library (for the VS Code extension).

Usage:
    python regen.py explorations/001_behind_the_bone.md --lines 45-52
    python regen.py explorations/001_behind_the_bone.md --lines 45-52 --reason "transition feels abrupt"

Output: prints the replacement text to stdout. Original file is not modified.

As a JSON API (for the VS Code extension):
    echo '{"file":"explorations/001.md","start":45,"end":52,"reason":"..."}' | python regen.py --json
"""

import argparse
import json
import os
import sys
from pathlib import Path

from google import genai

DESIGN_PATH = Path(__file__).parent / "DESIGN.md"

SYSTEM_PROMPT = """\
You are a writer for QuietBones, a meditation/relaxation app built on Feldenkrais principles.

You write guided explorations — quiet, unhurried, lying-down sessions that teach \
local inhibition through attentional cues, imagined lines, micromovements, and eye movements.

Your voice:
- Never says "relax." Places attention; the listener discovers release.
- Uses bone/weight/floor/space/direction/line vocabulary. No chakras, energy, or clinical terms.
- Invites rather than instructs. "See if you can..." not "Now feel..."
- Pauses (marked as ... on their own line) are structural, not filler.
- Sentences are short. Breathing room between ideas.

You are regenerating a section of an existing exploration. Maintain consistency \
with the surrounding text in tone, pacing, and body region. Return ONLY the \
replacement lines — no commentary, no markdown fences, no explanation."""


def load_design():
    """Load DESIGN.md for context."""
    if DESIGN_PATH.exists():
        return DESIGN_PATH.read_text()
    return ""


def build_prompt(full_text: str, start: int, end: int, reason: str = "") -> str:
    """Build the user prompt for regeneration."""
    lines = full_text.splitlines()

    # Convert to 0-indexed
    start_idx = max(0, start - 1)
    end_idx = min(len(lines), end)

    before = "\n".join(lines[:start_idx])
    selected = "\n".join(lines[start_idx:end_idx])
    after = "\n".join(lines[end_idx:])

    parts = []
    parts.append("## Full exploration for context\n")
    parts.append(full_text)
    parts.append("\n---\n")
    parts.append(f"## Section to regenerate (lines {start}-{end})\n")
    parts.append(selected)
    parts.append("\n---\n")

    if before:
        parts.append("## Text immediately before this section\n")
        # Last 10 lines for tight context
        before_lines = before.splitlines()
        parts.append("\n".join(before_lines[-10:]))
        parts.append("\n---\n")

    if after:
        parts.append("## Text immediately after this section\n")
        after_lines = after.splitlines()
        parts.append("\n".join(after_lines[:10]))
        parts.append("\n---\n")

    if reason:
        parts.append(f"## Reason for regeneration\n{reason}\n---\n")

    parts.append(
        "Write a replacement for the selected section. "
        "Match the surrounding tone and pacing. "
        "Keep approximately the same length unless the reason suggests otherwise. "
        "Include ... pause markers where appropriate. "
        "Return ONLY the replacement lines."
    )

    return "\n".join(parts)


def regenerate(file_path: str, start: int, end: int, reason: str = "") -> str:
    """Regenerate a section of an exploration file.

    Args:
        file_path: Path to the exploration markdown file.
        start: First line number to regenerate (1-indexed, inclusive).
        end: Last line number to regenerate (1-indexed, inclusive).
        reason: Optional reason/guidance for the regeneration.

    Returns:
        The replacement text for the selected lines.
    """
    text = Path(file_path).read_text()
    design = load_design()

    user_prompt = build_prompt(text, start, end, reason)

    system = SYSTEM_PROMPT
    if design:
        system += "\n\n## Design principles\n\n" + design

    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=user_prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=2048,
        ),
    )

    return response.text


def main():
    parser = argparse.ArgumentParser(description="Regenerate a section of a QuietBones exploration")
    parser.add_argument("--json", action="store_true", help="Read JSON from stdin, write JSON to stdout")
    parser.add_argument("file", nargs="?", help="Path to exploration markdown file")
    parser.add_argument("--lines", help="Line range to regenerate, e.g. 45-52")
    parser.add_argument("--reason", default="", help="Reason for regeneration")
    args = parser.parse_args()

    # Load API key from local .env if available
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

    if args.json:
        req = json.loads(sys.stdin.read())
        result = regenerate(
            file_path=req["file"],
            start=req["start"],
            end=req["end"],
            reason=req.get("reason", ""),
        )
        json.dump({"replacement": result}, sys.stdout)
    else:
        if not args.file or not args.lines:
            parser.error("file and --lines are required (or use --json)")

        parts = args.lines.split("-")
        start = int(parts[0])
        end = int(parts[1]) if len(parts) > 1 else start

        result = regenerate(args.file, start, end, args.reason)
        print(result)


if __name__ == "__main__":
    main()
