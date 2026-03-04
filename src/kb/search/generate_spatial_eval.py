"""
Generate spatial configuration search eval questions from cached spatial extractions.

For each lesson with spatial extraction cached, asks an LLM to generate 1 natural
search query describing a body arrangement from the lesson. Output is merged into
the FP Free eval file alongside existing action/body/experience questions.

Usage:
    .venv/bin/python src/kb/search/generate_spatial_eval.py --dry-run
    .venv/bin/python src/kb/search/generate_spatial_eval.py --apply
    .venv/bin/python src/kb/search/generate_spatial_eval.py --apply --limit 5
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add src/kb to path for infrastructure imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from database import Database
from cache_helpers import get_cache_key
from llm import LLM

DEFAULT_EVAL_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "search_eval_fp_free.json")

SYSTEM_PROMPT = """You generate a realistic search query that a Feldenkrais student might type to find a lesson based on its body arrangement or spatial configuration.

You will be given spatial configuration details from one lesson: positions, holds (body-part-to-body-part contact), constraints (movement restrictions), and config summaries.

Generate exactly 1 search query (5-15 words) that describes a specific body arrangement, hold, or positional constraint from the lesson — the kind of query where knowing the spatial setup would help find the right lesson.

Rules:
- Phrase it as a student would type into a search box (natural language)
- Do NOT copy vocabulary verbatim from the config data — rephrase using synonyms or plain descriptions
- Be specific enough to distinguish this lesson from others
- Focus on the spatial setup: positions, holds, contact points, constraints
- Do NOT mention the lesson title

Return ONLY the query text, no JSON, no quotes, no explanation."""


def format_spatial_for_prompt(spatial_data: dict, title: str) -> str:
    """Format spatial extraction data as readable text for the LLM prompt."""
    parts = [f"Lesson: {title}", ""]

    for group in spatial_data.get("groups", []):
        gnum = group.get("group_number", "?")
        position = group.get("position", "unknown")
        parts.append(f"Group {gnum} — Position: {position}")

        config_detail = group.get("config_detail", "")
        if config_detail:
            parts.append(f"  Config: {config_detail}")

        for hold in group.get("holds", []):
            parts.append(f"  Hold: {hold.get('part_a', '')} holds {hold.get('part_b', '')}")

        for c in group.get("constraints", []):
            parts.append(f"  Constraint: {c.get('part', '')} — {c.get('rule', '')}")

        for contact in group.get("contacts", []):
            qualifier = contact.get("qualifier", "")
            if qualifier:
                parts.append(f"  Contact: {contact.get('part', '')} on {contact.get('surface', '')} ({qualifier})")

        for mv in group.get("primary_movements", []):
            parts.append(f"  Movement: {mv}")

        parts.append("")

    return "\n".join(parts)


def find_fp_free_docs_with_spatial(db: Database, source_id: int | None = None) -> list[dict]:
    """Find FP Free documents that have pass_0b_spatial_v2 cached."""
    source_filter = f" AND d.source_id = {source_id}" if source_id else ""

    with db.get_cursor(commit=False) as cursor:
        cursor.execute(f"""
            SELECT a.document_id::text as doc_id,
                   d.title,
                   a.data->>'text' as text
            FROM analysis a
            JOIN document d ON d.id = a.document_id
            JOIN analysis_type at ON at.id = a.type_id AND at.name = 'TRANSCRIPTION'
            WHERE a.data->>'text' IS NOT NULL{source_filter}
        """)
        docs = [dict(row) for row in cursor.fetchall()]

    results = []
    cache_suffix = ":pass_0b_spatial_v2"

    for doc in docs:
        text = doc["text"].strip()
        if not text:
            continue
        cache_key = get_cache_key(text + cache_suffix)
        cached = db.get_cache(cache_key)
        if cached and cached.get("groups"):
            results.append({
                "doc_id": doc["doc_id"],
                "title": doc["title"],
                "spatial_data": cached,
            })

    return results


def main():
    parser = argparse.ArgumentParser(description="Generate spatial configuration eval questions")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be generated without calling LLM")
    group.add_argument("--apply", action="store_true", help="Generate queries via LLM and merge into eval file")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N lessons")
    parser.add_argument("--source", type=int, default=None, help="Only include lessons from this source_id")
    parser.add_argument("--eval-file", type=str, default=DEFAULT_EVAL_FILE, help="Eval JSON file to merge into")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment", file=sys.stderr)
        sys.exit(1)

    if args.apply and not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    eval_path = os.path.normpath(args.eval_file)

    db = Database(database_url)
    db.initialize()

    docs = find_fp_free_docs_with_spatial(db, source_id=args.source)
    docs.sort(key=lambda d: d["title"])
    print(f"Found {len(docs)} lessons with spatial extraction")

    # If merging into existing eval, filter to only lessons already in eval
    existing_eval = None
    existing_doc_ids = None
    if os.path.exists(eval_path):
        with open(eval_path) as f:
            existing_eval = json.load(f)
        existing_doc_ids = {q["expected_doc_id"] for q in existing_eval["questions"]}
        # Filter to lessons that are in the eval set
        docs = [d for d in docs if d["doc_id"] in existing_doc_ids]
        # Also skip any that already have spatial questions
        existing_spatial_ids = {
            q["expected_doc_id"]
            for q in existing_eval["questions"]
            if q.get("category") == "spatial_configuration"
        }
        docs = [d for d in docs if d["doc_id"] not in existing_spatial_ids]
        print(f"  After filtering to eval set: {len(docs)} (skipped {len(existing_spatial_ids)} already spatial)")

    if args.limit:
        docs = docs[:args.limit]
        print(f"Limited to {len(docs)} lessons")

    if not docs:
        print("Nothing to do.")
        db.close()
        return

    if args.dry_run:
        for doc in docs:
            text = format_spatial_for_prompt(doc["spatial_data"], doc["title"])
            n_groups = len(doc["spatial_data"].get("groups", []))
            print(f"\n  [{doc['title']}] ({doc['doc_id']}) — {n_groups} groups")
            preview = text[:300].replace("\n", " | ")
            print(f"    {preview}...")
        print(f"\nDRY RUN: {len(docs)} lessons would generate {len(docs)} spatial queries")
        db.close()
        return

    # Generate queries via LLM
    llm = LLM(provider="gemini", model="gemini-2.0-flash")
    new_questions = []
    errors = 0

    # Determine starting question number from existing eval
    max_q_num = 0
    if existing_eval:
        for q in existing_eval["questions"]:
            try:
                num = int(q["id"].lstrip("q"))
                max_q_num = max(max_q_num, num)
            except ValueError:
                pass

    q_num = max_q_num

    for i, doc in enumerate(docs):
        prompt_text = format_spatial_for_prompt(doc["spatial_data"], doc["title"])
        print(f"[{i+1}/{len(docs)}] {doc['title']}...", end=" ", flush=True)

        try:
            raw = llm.complete(prompt_text, system=SYSTEM_PROMPT, max_tokens=256)
            query = raw.strip().strip('"').strip("'")

            q_num += 1
            new_questions.append({
                "id": f"q{q_num:04d}",
                "query": query,
                "expected_doc_id": doc["doc_id"],
                "expected_title": doc["title"],
                "category": "spatial_configuration",
            })
            print(f"ok — \"{query}\"")
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            errors += 1

    if not new_questions:
        print("No questions generated.")
        db.close()
        return

    # Merge into existing eval or create new
    if existing_eval:
        existing_eval["questions"].extend(new_questions)
        output = existing_eval
        output["generated_at"] = datetime.now(timezone.utc).isoformat()
    else:
        output = {
            "version": "1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "lesson_count": len(docs),
            "questions": new_questions,
        }

    os.makedirs(os.path.dirname(eval_path), exist_ok=True)
    with open(eval_path, "w") as f:
        json.dump(output, f, indent=2)

    total = len(output["questions"])
    spatial_count = sum(1 for q in output["questions"] if q.get("category") == "spatial_configuration")
    print(f"\nDone: {len(new_questions)} spatial queries generated, {errors} errors")
    print(f"Total eval: {total} questions ({spatial_count} spatial)")
    print(f"Output: {eval_path}")
    db.close()


if __name__ == "__main__":
    main()
