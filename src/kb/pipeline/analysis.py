"""
Analysis pipeline runner: executes gated analysis steps for documents.

Reads group gates and document_analysis_state to find runnable (document, step) pairs
and executes them in dependency order.

Usage:
    # Run all runnable steps
    python -m kb.pipeline.analysis run

    # Run for a specific document
    python -m kb.pipeline.analysis run --document <doc_id>

    # Show what would run (dry run)
    python -m kb.pipeline.analysis run --dry-run

    # Show dashboard summary
    python -m kb.pipeline.analysis status
"""

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

# Add parent to path for local dev
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database
from analysis import (
    run_pass_a,
    run_pass_0b,
    run_pass_0b_lang,
    run_pass_0b_word_lang,
    run_pass_0b_concepts,
    run_pass_0b_spatial,
    run_pass_0b_spatial_v3,
    run_pass_0b_condensed,
    get_cache_key,
    load_cached_analysis,
)

logger = logging.getLogger(__name__)

# Default LLM provider for analysis steps
DEFAULT_PROVIDER = "gemini"


def _get_transcript(db: Database, document_id: str) -> str | None:
    """Get the transcript text for a document."""
    with db.get_cursor(commit=False) as cursor:
        cursor.execute(
            "SELECT data->>'text' as text FROM analysis WHERE document_id = %s AND type_id = 1",
            (document_id,),
        )
        row = cursor.fetchone()
        return row["text"].strip() if row and row["text"] else None


def _compute_upstream_hash(db: Database, document_id: str, depends_on: list[str]) -> str:
    """Compute a hash of upstream completed versions for staleness tracking."""
    parts = []
    for dep in sorted(depends_on):
        state = db.get_document_state(document_id, dep)
        cv = state.get("completed_version") if state else None
        parts.append(f"{dep}:{cv}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# Step execution functions — each takes (db, document_id, transcript) and returns success bool

def _run_pass_a(db: Database, document_id: str, transcript: str) -> bool:
    result = run_pass_a(transcript, DEFAULT_PROVIDER)
    return result is not None


def _run_pass_0b(db: Database, document_id: str, transcript: str) -> bool:
    result = run_pass_0b(transcript, DEFAULT_PROVIDER)
    return result is not None


def _run_pass_0b_lang(db: Database, document_id: str, transcript: str) -> bool:
    result = run_pass_0b_lang(transcript, DEFAULT_PROVIDER)
    return result is not None


def _run_pass_0b_word_lang(db: Database, document_id: str, transcript: str) -> bool:
    result = run_pass_0b_word_lang(transcript, DEFAULT_PROVIDER)
    return result is not None


def _run_pass_0b_concepts(db: Database, document_id: str, transcript: str) -> bool:
    result = run_pass_0b_concepts(transcript, DEFAULT_PROVIDER)
    return result is not None


def _run_pass_0b_spatial(db: Database, document_id: str, transcript: str) -> bool:
    result = run_pass_0b_spatial(transcript, DEFAULT_PROVIDER)
    return result is not None


def _run_pass_0b_spatial_v3(db: Database, document_id: str, transcript: str) -> bool:
    result = run_pass_0b_spatial_v3(transcript, DEFAULT_PROVIDER)
    if result is not None:
        # Mark embed_positions as stale so it rebuilds with the richer v3 data
        with db.get_cursor() as cur:
            cur.execute("""
                UPDATE document_analysis_state SET stale = TRUE, updated_at = NOW()
                WHERE document_id = %s AND step_name = 'embed_positions'
            """, (document_id,))
    return result is not None


def _run_pass_0b_condensed(db: Database, document_id: str, transcript: str) -> bool:
    # Condensed needs somatic + lang results as input
    somatic_key = get_cache_key(transcript.strip() + ":somatic_v2")
    lang_key = get_cache_key(transcript.strip() + ":language_v1")
    somatic = load_cached_analysis(somatic_key)
    lang = load_cached_analysis(lang_key)
    if not somatic or not lang:
        logger.error(f"Missing upstream for condensed: somatic={bool(somatic)}, lang={bool(lang)}")
        return False
    result = run_pass_0b_condensed(somatic, lang, transcript)
    return result is not None


def _run_embeddings(db: Database, document_id: str, transcript: str) -> bool:
    """Build embeddings from condensed analysis."""
    import hashlib
    from llm import get_embeddings
    from cache_helpers import build_summary_from_condensed

    condensed_key = get_cache_key(transcript.strip() + ":condense_v4")
    condensed = load_cached_analysis(condensed_key)
    if not condensed or not condensed.get("segments"):
        logger.error(f"No condensed data for embedding: {document_id[:8]}")
        return False

    doc = db.get_document(document_id)
    title = doc.get("title", "") if doc else ""
    summary = build_summary_from_condensed(condensed, title)
    summary_hash = hashlib.sha256(summary.encode()).hexdigest()[:16]

    embeddings, _ = get_embeddings([summary], task_type="RETRIEVAL_DOCUMENT")
    db.upsert_embedding(document_id, embeddings[0], summary, summary_hash)
    return True


def _run_embed_positions(db: Database, document_id: str, transcript: str) -> bool:
    """Build position embedding from spatial_v3 data, falling back to v2."""
    import hashlib
    from llm import get_embeddings

    # Try v3 first
    v3_key = get_cache_key(transcript.strip() + ":spatial_v3")
    v3_data = load_cached_analysis(v3_key)

    if v3_data:
        # Build embedding text from v3 position summary + step positions
        parts = []
        doc = db.get_document(document_id)
        if doc:
            parts.append(doc.get("title", ""))
        for p in v3_data.get("position_summary", []):
            if isinstance(p, dict):
                parts.append(p["position"])
                parts.extend(p.get("alternates", []))
            else:
                parts.append(p)
        for s in v3_data.get("steps", []):
            if not s.get("is_rest"):
                parts.append(f"{s['position']}: {s['action']}")
        summary = "\n".join(parts)
    else:
        # Fall back to v2
        v2_key = get_cache_key(transcript.strip() + ":spatial_v2")
        v2_data = load_cached_analysis(v2_key)
        if not v2_data:
            logger.error(f"No spatial data (v2 or v3) for position embedding: {document_id[:8]}")
            return False

        parts = []
        doc = db.get_document(document_id)
        if doc:
            parts.append(doc.get("title", ""))
        for g in v2_data.get("groups", []):
            detail = g.get("config_detail", "")
            if detail:
                parts.append(detail)
            for h in g.get("holds", []):
                parts.append(f"{h.get('part_a', '')} on {h.get('part_b', '')}")
            for m in g.get("primary_movements", []):
                parts.append(m)
        summary = "\n".join(parts)

    summary_hash = hashlib.sha256(summary.encode()).hexdigest()[:16]
    embeddings, _ = get_embeddings([summary], task_type="RETRIEVAL_DOCUMENT")
    db.upsert_embedding(document_id, embeddings[0], summary, summary_hash, method="positions")
    return True


def _run_concept_index(db: Database, document_id: str, transcript: str) -> bool:
    """Build concept index entries from concepts analysis."""
    from search.build_concept_index import build_concept_rows, build_concept_summary

    concepts_key = get_cache_key(transcript.strip() + ":concept_tags_v2")
    concepts_data = load_cached_analysis(concepts_key)
    if not concepts_data or not concepts_data.get("concepts"):
        logger.error(f"No concepts data for indexing: {document_id[:8]}")
        return False

    # Upsert concept rows to lesson_concept table
    all_rows = build_concept_rows(concepts_data)
    db.upsert_concepts(document_id, all_rows)

    # Build concept embedding
    import hashlib
    from llm import get_embeddings

    # Check for spatial data too
    spatial_key = get_cache_key(transcript.strip() + ":spatial_v2")
    spatial_data = load_cached_analysis(spatial_key)

    doc = db.get_document(document_id)
    title = doc.get("title", "") if doc else ""
    summary = build_concept_summary(concepts_data, title, spatial_data)
    summary_hash = hashlib.sha256(summary.encode()).hexdigest()[:16]

    embeddings, _ = get_embeddings([summary], task_type="RETRIEVAL_DOCUMENT")
    db.upsert_embedding(document_id, embeddings[0], summary, summary_hash, method="concepts")
    return True


def _run_spatial_index(db: Database, document_id: str, transcript: str) -> bool:
    """Build spatial index entries from spatial analysis."""
    from search.build_spatial_index import build_spatial_concept_rows

    spatial_key = get_cache_key(transcript.strip() + ":spatial_v2")
    spatial_data = load_cached_analysis(spatial_key)
    if not spatial_data or not spatial_data.get("groups"):
        logger.error(f"No spatial data for indexing: {document_id[:8]}")
        return False

    concept_rows = build_spatial_concept_rows(spatial_data)
    db.upsert_spatial_concepts(document_id, concept_rows)
    return True


def _init_extract_map(db: Database):
    """Ensure extract_map module is initialized."""
    import extract_map.pipeline as em
    if em._db is None:
        import llm_config
        llm_config.init(db)
        em.init(db, llm_config.get_llm)


def _run_extraction(db: Database, document_id: str, transcript: str) -> bool:
    _init_extract_map(db)
    from extract_map.pipeline import run_extraction
    result = run_extraction(transcript, DEFAULT_PROVIDER)
    return result is not None


def _run_mapping(db: Database, document_id: str, transcript: str) -> bool:
    _init_extract_map(db)
    from extract_map.pipeline import run_mapping
    result = run_mapping(transcript, DEFAULT_PROVIDER)
    return result is not None


# Registry: step name → executor function
STEP_EXECUTORS = {
    "decompose": _run_pass_a,
    "somatic": _run_pass_0b,
    "language": _run_pass_0b_lang,
    "language_patterns": _run_pass_0b_word_lang,
    "concept_tags": _run_pass_0b_concepts,
    "spatial": _run_pass_0b_spatial,
    "spatial_v3": _run_pass_0b_spatial_v3,
    "embed_positions": _run_embed_positions,
    "condense": _run_pass_0b_condensed,
    "embed": _run_embeddings,
    "index_concepts": _run_concept_index,
    "index_spatial": _run_spatial_index,
    "extract_steps": _run_extraction,
    "map_body_parts": _run_mapping,
}


def run_step(db: Database, document_id: str, step_name: str) -> bool:
    """Execute a single analysis step for a document.

    Returns True if successful, False on failure.
    Designed to run in its own process — no shared state.
    """
    executor = STEP_EXECUTORS.get(step_name)
    if not executor:
        logger.error(f"No executor for step: {step_name}")
        return False

    transcript = _get_transcript(db, document_id)
    if not transcript:
        db.mark_step_error(document_id, step_name, "No transcript available")
        return False

    step = db.get_analysis_step(step_name)
    if not step:
        logger.error(f"Unknown step: {step_name}")
        return False

    depends_on = step.get("depends_on") or []

    try:
        success = executor(db, document_id, transcript)
        if success:
            upstream_hash = _compute_upstream_hash(db, document_id, depends_on)
            db.mark_step_completed(document_id, step_name, step["current_version"], upstream_hash)
            return True
        else:
            db.mark_step_error(document_id, step_name, "Step returned no result")
            return False
    except Exception as e:
        logger.exception(f"Error running {step_name} for {document_id[:8]}")
        db.mark_step_error(document_id, step_name, str(e)[:500])
        return False


def run_one(document_id: str, step_name: str) -> bool:
    """Run a single step in its own process. Called via CLI.

    Exits with code 0 on success, 1 on failure.
    Prints JSON result to stdout.
    """
    import os
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print(json.dumps({"ok": False, "error": "DATABASE_URL not set"}))
        return False

    db = Database(db_url)

    # Initialize modules that analysis steps depend on
    import cache_helpers
    import llm_config
    import analysis as analysis_mod

    cache_helpers.init(db)
    llm_config.init(db)

    data_dir = Path(__file__).parent.parent.parent.parent / "data"
    analysis_mod.init(db, data_dir, llm_config.get_llm)

    t0 = time.time()
    ok = run_step(db, document_id, step_name)
    elapsed = time.time() - t0
    db.close()

    print(json.dumps({"ok": ok, "elapsed": round(elapsed, 1)}))
    return ok


def show_status(db: Database):
    """Print analysis pipeline status summary."""
    steps = db.get_analysis_steps()
    print(f"\nAnalysis Steps ({len(steps)}):")
    print(f"  {'Name':<20s} {'Version':>7s}  {'Depends On'}")
    print(f"  {'─'*20} {'─'*7}  {'─'*30}")
    for s in steps:
        deps = ", ".join(s.get("depends_on") or []) or "—"
        print(f"  {s['name']:<20s} v{s['current_version']:>5d}  {deps}")

    runnable = db.get_runnable_steps()
    print(f"\nRunnable steps: {len(runnable)}")
    if runnable:
        for r in runnable[:20]:
            print(f"  {r['document_title'][:40]:40s} → {r['step_name']}")
        if len(runnable) > 20:
            print(f"  ... and {len(runnable) - 20} more")


def main():
    import os
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

    parser = argparse.ArgumentParser(description="Analysis pipeline runner")
    sub = parser.add_subparsers(dest="command")

    one_parser = sub.add_parser("run-one", help="Run a single step (for subprocess use)")
    one_parser.add_argument("document_id", help="Document UUID")
    one_parser.add_argument("step_name", help="Analysis step name")

    sub.add_parser("status", help="Show pipeline status")

    args = parser.parse_args()

    if args.command == "run-one":
        ok = run_one(args.document_id, args.step_name)
        sys.exit(0 if ok else 1)
    elif args.command == "status":
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            print("ERROR: DATABASE_URL not set")
            sys.exit(1)
        db = Database(db_url)
        show_status(db)
        db.close()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
