"""
Generate search evaluation questions from condensed lesson cards.

For each lesson with a condensed analysis, asks an LLM to generate 3 natural
search queries (action, body, experience) that a student might use to find
that lesson. Output is a JSON file usable by run_search_eval.py.

Usage:
    .venv/bin/python src/kb/generate_search_eval.py --dry-run
    .venv/bin/python src/kb/generate_search_eval.py --apply
    .venv/bin/python src/kb/generate_search_eval.py --apply --limit 10
    .venv/bin/python src/kb/generate_search_eval.py --apply --force
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from database import Database
from llm import LLM

DEFAULT_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "search_eval_questions.json")

SYSTEM_PROMPT = """You generate realistic search queries that a Feldenkrais student or teacher might type to find a specific lesson. You will be given a condensed analysis card for one lesson. Generate exactly 3 search queries, one for each category:

1. "action" — about the movement or physical action (what you do in the lesson)
2. "body" — about body knowledge, anatomy, or biomechanics (what body parts or relationships are involved)
3. "experience" — about the felt experience, attention, sensation, or imagery (what you notice or feel)

Rules:
- Each query should be 5-15 words, natural language (as someone would type into a search box)
- Queries must be specific enough to distinguish this lesson from others
- Do NOT quote the card text verbatim — rephrase in a searcher's voice
- Do NOT mention the lesson title or number in the query
- Avoid jargon that only appears in this specific card — use terms a student would actually search for

Return a JSON array of exactly 3 objects:
[
  {"category": "action", "query": "..."},
  {"category": "body", "query": "..."},
  {"category": "experience", "query": "..."}
]"""


def format_condensed_for_prompt(condensed: dict, title: str) -> str:
    """Format a condensed card as readable text for the LLM prompt."""
    parts = [f"Lesson: {title}"]
    for seg in condensed.get("segments", []):
        setting = seg.get("setting", {})
        if setting:
            if setting.get("title"):
                parts.append(f"\nSetting: {setting['title']}")
            if setting.get("somatic_intent"):
                parts.append(f"Intent: {setting['somatic_intent']}")
            for attr in ["space", "teacher_presence", "arc"]:
                val = setting.get(attr, "")
                if val:
                    parts.append(f"  {attr}: {val}")
        for card in seg.get("group_cards", []):
            label = card.get("group_label", "")
            card_title = card.get("title", "")
            parts.append(f"\nGroup {card.get('group_number', '?')}: {label}")
            if card_title:
                parts.append(f"  Title: {card_title}")
            if card.get("the_action"):
                parts.append(f"  Action: {card['the_action']}")
            for attr in ["the_body", "the_attention", "the_intention", "the_voice"]:
                val = card.get(attr, "")
                if val and val.lower() != "none":
                    parts.append(f"  {attr.replace('the_', '').capitalize()}: {val}")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Generate search evaluation questions from condensed cards")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be generated without calling LLM")
    group.add_argument("--apply", action="store_true", help="Actually generate queries via LLM")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N lessons")
    parser.add_argument("--force", action="store_true", help="Regenerate even if output file exists")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT, help="Output JSON file path")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment", file=sys.stderr)
        sys.exit(1)

    if args.apply and not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    # Check if output already exists
    output_path = os.path.normpath(args.output)
    if os.path.exists(output_path) and not args.force and args.apply:
        print(f"Output file already exists: {output_path}")
        print("Use --force to overwrite")
        sys.exit(1)

    db = Database(database_url)
    db.initialize()

    docs = db.get_docs_with_condensed()
    docs.sort(key=lambda d: d["title"])
    print(f"Found {len(docs)} lessons with condensed cards")

    if args.limit:
        docs = docs[:args.limit]
        print(f"Limited to {len(docs)} lessons")

    if not docs:
        print("Nothing to do.")
        db.close()
        return

    if args.dry_run:
        for doc in docs:
            condensed = db.get_cache(doc["cache_key"])
            if not condensed or not condensed.get("segments"):
                continue
            text = format_condensed_for_prompt(condensed, doc["title"])
            print(f"\n  [{doc['title']}] ({doc['doc_id']})")
            preview = text[:300].replace("\n", " | ")
            print(f"    {preview}...")
        print(f"\nDRY RUN: {len(docs)} lessons would generate {len(docs) * 3} queries")
        db.close()
        return

    # Generate queries via LLM
    llm = LLM(provider="gemini", model="gemini-2.0-flash")
    questions = []
    errors = 0
    q_num = 0

    for i, doc in enumerate(docs):
        condensed = db.get_cache(doc["cache_key"])
        if not condensed or not condensed.get("segments"):
            continue

        prompt_text = format_condensed_for_prompt(condensed, doc["title"])
        print(f"[{i+1}/{len(docs)}] {doc['title']}...", end=" ", flush=True)

        try:
            raw = llm.complete(prompt_text, system=SYSTEM_PROMPT, max_tokens=512)
            # Parse JSON from response (handle markdown code blocks)
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                text = text.rsplit("```", 1)[0]
            items = json.loads(text)

            for item in items:
                q_num += 1
                questions.append({
                    "id": f"q{q_num:04d}",
                    "query": item["query"],
                    "expected_doc_id": doc["doc_id"],
                    "expected_title": doc["title"],
                    "category": item["category"],
                })
            print(f"ok ({len(items)} queries)")
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            errors += 1

    # Write output
    output = {
        "version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lesson_count": len(docs),
        "questions": questions,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone: {len(questions)} queries for {len(docs)} lessons, {errors} errors")
    print(f"Output: {output_path}")
    db.close()


if __name__ == "__main__":
    main()
