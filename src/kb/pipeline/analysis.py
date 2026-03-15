"""
Analysis pipeline runner: executes gated analysis steps for documents.

Reads the document_analysis_gate table to find runnable (document, step) pairs
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
    run_pass_0b_condensed,
    get_cache_key,
    load_cached_analysis,
)

logger = logging.getLogger(__name__)

# Default LLM provider for analysis steps
DEFAULT_PROVIDER = "gemini-flash"


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
        gate = db.get_gate(document_id, dep)
        cv = gate.get("completed_version") if gate else None
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
    from search.build_embeddings import build_embedding_for_document
    return build_embedding_for_document(db, document_id, transcript)


def _run_concept_index(db: Database, document_id: str, transcript: str) -> bool:
    """Build concept index entries from concepts analysis."""
    from search.build_concept_index import build_concepts_for_document
    return build_concepts_for_document(db, document_id, transcript)


def _run_spatial_index(db: Database, document_id: str, transcript: str) -> bool:
    """Build spatial index entries from spatial analysis."""
    from search.build_spatial_index import build_spatial_for_document
    return build_spatial_for_document(db, document_id, transcript)


def _run_extraction(db: Database, document_id: str, transcript: str) -> bool:
    from extract_map.pipeline import run_extraction
    result = run_extraction(transcript, DEFAULT_PROVIDER)
    return result is not None


def _run_mapping(db: Database, document_id: str, transcript: str) -> bool:
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
    Updates the gate table accordingly.
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


def run_pipeline(db: Database, document_id: str | None = None, dry_run: bool = False) -> dict:
    """Run all runnable steps, respecting dependency order.

    Returns summary dict with counts.
    """
    runnable = db.get_runnable_steps(document_id)

    if not runnable:
        print("Nothing to run.")
        return {"total": 0, "success": 0, "failed": 0}

    # Group by document for display
    by_doc = {}
    for r in runnable:
        doc_id = str(r["document_id"])
        if doc_id not in by_doc:
            by_doc[doc_id] = {"title": r["document_title"], "steps": []}
        by_doc[doc_id]["steps"].append(r["step_name"])

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Runnable steps: {len(runnable)}")
    for doc_id, info in by_doc.items():
        print(f"  {info['title'][:50]:50s} → {', '.join(info['steps'])}")

    if dry_run:
        return {"total": len(runnable), "success": 0, "failed": 0, "dry_run": True}

    # Execute in dependency order: steps with no deps first, then their dependents
    # Simple approach: keep running passes until nothing is runnable
    success = 0
    failed = 0
    executed = set()

    for _pass in range(10):  # max 10 passes to avoid infinite loops
        runnable = db.get_runnable_steps(document_id)
        # Filter out already-attempted steps
        runnable = [r for r in runnable
                    if (str(r["document_id"]), r["step_name"]) not in executed]
        if not runnable:
            break

        for r in runnable:
            doc_id = str(r["document_id"])
            step = r["step_name"]
            key = (doc_id, step)
            executed.add(key)

            print(f"  Running {step} for {r['document_title'][:40]}...", end=" ", flush=True)
            t0 = time.time()
            ok = run_step(db, doc_id, step)
            elapsed = time.time() - t0

            if ok:
                success += 1
                print(f"OK ({elapsed:.1f}s)")
            else:
                failed += 1
                print(f"FAILED ({elapsed:.1f}s)")

    total = success + failed
    print(f"\nDone: {success}/{total} succeeded, {failed} failed")
    return {"total": total, "success": success, "failed": failed}


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

    run_parser = sub.add_parser("run", help="Run analysis steps")
    run_parser.add_argument("--document", help="Run for a specific document ID")
    run_parser.add_argument("--dry-run", action="store_true", help="Show what would run")

    sub.add_parser("status", help="Show pipeline status")

    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    db = Database(db_url)
    db.initialize()

    if args.command == "run":
        run_pipeline(db, document_id=args.document, dry_run=args.dry_run)
    elif args.command == "status":
        show_status(db)
    else:
        parser.print_help()

    db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
