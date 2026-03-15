"""Agentic search over lesson IRs (intermediate representations).

The agent decomposes a natural language query into structured predicates
and searches over the stored extraction+mapping IRs.

Usage:
    ../../.venv/bin/python -m extract_map.search "lessons where you roll the head using the hand"
    ../../.venv/bin/python -m extract_map.search "lessons that differentiate pelvis from spine"
    ../../.venv/bin/python -m extract_map.search "lessons in sitting that work with breath"
"""

import json
import logging
import os
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
import llm_config
from extract_map.batch import list_stored_irs, load_ir, IR_CACHE_PREFIX

logger = logging.getLogger(__name__)

SEARCH_SYSTEM_PROMPT = """\
You are a Feldenkrais lesson search agent. You have access to structured intermediate
representations (IRs) of Feldenkrais lessons. Each IR contains:

- steps: sequential instructions with raw_text, instruction_type, position, body_parts_mentioned
- body_map: positions, primary_segments, differentiation_pairs, connectivity_patterns,
  effort_qualities, constraint_types, attention_targets, exit_state
- vocabulary_map: teacher terms mapped to canonical ontology terms

Your job is to search these IRs to find lessons matching the user's query.

You will be given the user's query and a JSON array of lesson IRs.

Return a JSON object with:
{
  "matches": [
    {
      "document_id": "...",
      "title": "...",
      "relevance": "high" | "medium" | "low",
      "reasoning": "Why this lesson matches, citing specific steps or body_map fields"
    }
  ],
  "query_interpretation": "How you interpreted the query in terms of structured fields"
}

Rules:
- Only return lessons that actually match. An empty matches array is fine.
- Cite specific evidence: step IDs, body_map fields, exact raw_text excerpts.
- Rank by relevance (high = core theme of lesson, medium = present but not central, low = tangential).
- For compositional queries (multiple conditions), ALL conditions must be met for high relevance.
"""

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "title": {"type": "string"},
                    "relevance": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reasoning": {"type": "string"}
                },
                "required": ["document_id", "title", "relevance", "reasoning"]
            }
        },
        "query_interpretation": {"type": "string"}
    },
    "required": ["matches", "query_interpretation"]
}


def load_all_irs(db):
    """Load all stored IRs."""
    ir_ids = list_stored_irs(db)
    irs = []
    for doc_id in ir_ids:
        ir = load_ir(db, doc_id)
        if ir:
            irs.append(ir)
    return irs


def compact_ir(ir):
    """Create a compact version of an IR for the search prompt.

    Keeps body_map and vocabulary_map in full, but summarizes steps
    to reduce token count.
    """
    step_summary = []
    for s in ir.get("steps", []):
        step_summary.append({
            "id": s["step_id"],
            "type": s["instruction_type"],
            "pos": s["position"],
            "parts": s["body_parts_mentioned"],
            "text": s["raw_text"][:120],
        })

    return {
        "document_id": ir["document_id"],
        "title": ir["title"],
        "body_map": ir.get("body_map", {}),
        "vocabulary_map": ir.get("vocabulary_map", []),
        "steps": step_summary,
    }


def search(query: str, db, provider_key: str | None = None) -> dict:
    """Search lesson IRs with a natural language query."""
    irs = load_all_irs(db)
    if not irs:
        return {"error": "No IRs stored. Run batch first.", "matches": []}

    compact_irs = [compact_ir(ir) for ir in irs]

    prompt = f"""## User Query
{query}

## Available Lessons ({len(compact_irs)} total)
{json.dumps(compact_irs, indent=1)}"""

    llm = llm_config.get_llm(provider_key)
    t0 = time.time()

    result = llm.complete_structured_json(
        prompt=prompt,
        schema=SEARCH_SCHEMA,
        system=SEARCH_SYSTEM_PROMPT,
    )

    result["elapsed"] = round(time.time() - t0, 1)
    result["lessons_searched"] = len(compact_irs)
    return result


def main():
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env'))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    query = " ".join(sys.argv[1:])
    if not query:
        print("Usage: python -m extract_map.search \"your query here\"")
        sys.exit(1)

    db = Database(os.environ['DATABASE_URL'])
    llm_config.init(db)

    print(f"Searching {len(list_stored_irs(db))} lessons for: {query}")
    print()

    result = search(query, db)

    print(f"Query interpretation: {result.get('query_interpretation', '?')}")
    print(f"Searched {result['lessons_searched']} lessons in {result['elapsed']}s")
    print()

    matches = result.get("matches", [])
    if not matches:
        print("No matches found.")
        return

    print(f"{len(matches)} matches:")
    for m in matches:
        print(f"\n  [{m['relevance'].upper()}] {m['title']}")
        print(f"  ID: {m['document_id']}")
        print(f"  {m['reasoning']}")


if __name__ == "__main__":
    main()
