"""Extract position sequences from lessons — zero-LLM and LLM-cleaned versions.

Compares raw pass_a extraction with LLM-cleaned distillation for
the OpenATM Reference group (50 lessons).

Usage:
    .venv/bin/python scripts/extract_positions.py [--llm] [--limit N] [--document DOC_ID]

Output: saves to data/position_extracts/ as individual markdown files.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kb"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from database import Database
import cache_helpers

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "position_extracts"


def get_reference_docs(db):
    """Get the 50 OpenATM Reference lessons."""
    with db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT d.id, d.title FROM document d
            JOIN lesson_group_member lgm ON d.id = lgm.document_id
            WHERE lgm.group_id = 110
            ORDER BY d.title
        """)
        return cur.fetchall()


def get_pass_a(db, doc_id):
    """Load pass_a data for a document."""
    with db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT data->>'text' as text FROM analysis WHERE document_id = %s AND type_id = 1",
            (doc_id,),
        )
        row = cur.fetchone()
        if not row or not row["text"]:
            return None
        text = row["text"].strip()

    pa_key = cache_helpers.get_cache_key(text + ":decompose_v3")
    return cache_helpers.load_cached_analysis(pa_key)


def extract_raw(pa_data):
    """Zero-LLM extraction: group labels + step instructions."""
    groups = pa_data.get("groups", [])
    steps = pa_data.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps}

    lines = []
    for g in groups:
        label = g.get("label", "")
        lines.append(f"[{label}]")
        for sn in g.get("steps", []):
            s = step_lookup.get(sn, {})
            inst = s.get("instruction", "")
            if inst:
                lines.append(f"  {sn}. {inst}")
        lines.append("")

    return "\n".join(lines).strip()


def extract_with_llm(pa_data, title):
    """LLM-cleaned extraction: distill into position-action sequence."""
    from google import genai

    groups = pa_data.get("groups", [])
    steps = pa_data.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps}

    # Build the raw input for the LLM
    raw_lines = []
    for g in groups:
        label = g.get("label", "")
        desc = g.get("description", "")
        raw_lines.append(f"## {label}")
        if desc:
            raw_lines.append(f"{desc}")
        for sn in g.get("steps", []):
            s = step_lookup.get(sn, {})
            inst = s.get("instruction", "")
            tags = s.get("tags", [])
            if inst:
                raw_lines.append(f"Step {sn} [{','.join(tags)}]: {inst}")
        raw_lines.append("")
    raw_text = "\n".join(raw_lines)

    prompt = f"""Below is the decomposed structure of a Feldenkrais lesson called "{title}".

Distill this into a **position sequence** — a list of body configurations and movements in plain language. The goal is to make this searchable by someone describing a position they remember.

Rules:
- Start each new body configuration with [brackets] describing the position
- List the movements/actions done in that position as short bullet points
- Use consistent, plain language (not clinical jargon)
- "Stand the feet" or "feet standing" means feet flat on floor, knees bent (while lying down)
- Note which side (left/right) when specified
- Include transitions between positions
- Keep it concise — one line per action, no elaboration

Example format:
[Lying on back, knees bent, feet flat on floor]
- Tilt both knees to the right
- Tilt both knees to the left
- Rest
[Lying on back, right foot standing, left leg long]
- Arms reach to ceiling, palms together
- Tilt arms to the left
...

Lesson structure:
{raw_text}

Write the position sequence now."""

    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    resp = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt,
        config={
            "max_output_tokens": 4096,
            "temperature": 0.0,
        },
    )
    return resp.text.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="Also run LLM extraction")
    parser.add_argument("--limit", type=int, help="Limit number of lessons")
    parser.add_argument("--document", help="Single document ID")
    args = parser.parse_args()

    db = Database(os.environ["DATABASE_URL"])
    cache_helpers.init(db)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "raw").mkdir(exist_ok=True)
    if args.llm:
        (OUTPUT_DIR / "llm").mkdir(exist_ok=True)

    if args.document:
        with db.get_cursor(commit=False) as cur:
            cur.execute("SELECT id, title FROM document WHERE id = %s", (args.document,))
            docs = cur.fetchall()
    else:
        docs = get_reference_docs(db)

    if args.limit:
        docs = docs[:args.limit]

    print(f"Extracting positions for {len(docs)} lessons")

    for i, doc in enumerate(docs):
        doc_id = str(doc["id"])
        title = doc["title"]
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:60].strip()

        pa_data = get_pass_a(db, doc_id)
        if not pa_data:
            print(f"  [{i+1}] SKIP {title[:50]} — no pass_a")
            continue

        # Raw extraction
        raw = extract_raw(pa_data)
        raw_path = OUTPUT_DIR / "raw" / f"{safe_title}.md"
        raw_path.write_text(f"# {title}\n\n{raw}\n", encoding="utf-8")

        # LLM extraction
        if args.llm:
            try:
                llm_text = extract_with_llm(pa_data, title)
                llm_path = OUTPUT_DIR / "llm" / f"{safe_title}.md"
                llm_path.write_text(f"# {title}\n\n{llm_text}\n", encoding="utf-8")
                print(f"  [{i+1}] {title[:50]} — raw: {len(raw)} chars, llm: {len(llm_text)} chars")
            except Exception as e:
                print(f"  [{i+1}] {title[:50]} — raw OK, LLM error: {e}")
        else:
            print(f"  [{i+1}] {title[:50]} — {len(raw)} chars")

    db.close()
    print(f"\nDone. Output in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
