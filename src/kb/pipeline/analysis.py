"""
Analysis pipeline runner: executes gated analysis steps for documents.

Work is claimed as (document, profile, step): one document carries one analysis
per model profile. A triple is runnable when a group containing the document
permits that step for that profile, the profile's global switch is on, and its
dependencies are complete for the same profile.

Usage:
    # Run a bounded, leased worker over everything runnable
    python -m kb.pipeline.analysis run

    # Show what would run (dry run)
    python -m kb.pipeline.analysis run --dry-run

    # Run one step (the worker runs each step in its own process this way)
    python -m kb.pipeline.analysis run-one <doc_id> <step> --profile <profile_key>

    # Show dashboard summary
    python -m kb.pipeline.analysis status
"""

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# Add parent to path for local dev
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database
from db.analysis_profiles import profile_cache_suffix, profile_provider_key
from analysis import (
    run_pass_a,
    run_pass_0b,
    run_pass_0b_lang,
    run_pass_0b_word_lang,
    run_pass_0b_concepts,
    run_pass_0b_spatial,
    run_pass_0b_spatial_v3,
    run_pass_0b_condensed,
    run_print_prep,
    run_motifs,
    run_raw_motifs,
    run_axes,
    get_cache_key,
    load_cached_analysis,
    save_cached_analysis,
)

logger = logging.getLogger(__name__)

ANALYZER_LOCK = "feldenkrais:analysis-worker"
# How often the worker re-reads runnable work while other steps are in flight.
RUNNABLE_REFRESH_SECONDS = 15
# run-one exit code when the profile's switch was off by the time the step started.
EXIT_PROFILE_DISABLED = 3


def _result_key(transcript: str, suffix: str, profile: dict) -> str:
    """Cache key of a step result produced under a profile."""
    return get_cache_key(transcript.strip() + suffix + profile_cache_suffix(profile))


def _get_transcript(db: Database, document_id: str) -> str | None:
    """Get the transcript text for a document."""
    with db.get_cursor(commit=False) as cursor:
        cursor.execute(
            "SELECT data->>'text' as text FROM analysis WHERE document_id = %s AND type_id = 1",
            (document_id,),
        )
        row = cursor.fetchone()
        return row["text"].strip() if row and row["text"] else None


def _compute_upstream_hash(db: Database, document_id: str, depends_on: list[str],
                           profile_key: str) -> str:
    """Compute a hash of upstream completed versions for staleness tracking."""
    parts = []
    for dep in sorted(depends_on):
        state = db.get_document_state(document_id, dep, profile_key)
        cv = state.get("completed_version") if state else None
        parts.append(f"{dep}:{cv}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# Step execution functions — each takes (db, document_id, transcript, profile)
# and returns a success bool. ``profile`` is an analysis_profile row.

def _run_pass_a(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_pass_a(transcript, profile_provider_key(profile))
    return result is not None


def _run_pass_0b(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_pass_0b(transcript, profile_provider_key(profile))
    return result is not None


def _run_pass_0b_lang(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_pass_0b_lang(transcript, profile_provider_key(profile))
    return result is not None


def _run_pass_0b_word_lang(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_pass_0b_word_lang(transcript, profile_provider_key(profile))
    return result is not None


def _run_pass_0b_concepts(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_pass_0b_concepts(transcript, profile_provider_key(profile))
    return result is not None


def _run_pass_0b_spatial(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_pass_0b_spatial(transcript, profile_provider_key(profile))
    return result is not None


def _run_pass_0b_spatial_v3(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_pass_0b_spatial_v3(transcript, profile_provider_key(profile))
    if result is not None:
        # Mark this profile's embed_positions stale so it rebuilds with the richer v3 data
        with db.get_cursor() as cur:
            cur.execute("""
                UPDATE document_analysis_state SET stale = TRUE, updated_at = NOW()
                WHERE document_id = %s AND profile_key = %s AND step_name = 'embed_positions'
            """, (document_id, profile["key"]))
    return result is not None


def _run_print_prep(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_print_prep(transcript, profile_provider_key(profile))
    rejected = len(result.get("validation", {}).get("rejected_quotes", []))
    if rejected:
        logger.info(f"print_prep {document_id[:8]}: dropped {rejected} quotes that were not exact excerpts")
    return True


def _run_pass_0b_condensed(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    # Condensed needs somatic + lang results as input
    somatic = load_cached_analysis(_result_key(transcript, ":somatic_v2", profile))
    lang = load_cached_analysis(_result_key(transcript, ":language_v1", profile))
    if not somatic or not lang:
        logger.error(f"Missing upstream for condensed: somatic={bool(somatic)}, lang={bool(lang)}")
        return False
    # Ensure somatic has pass_a data (groups/steps) — the condense formatter
    # needs this to produce per-group input. Load from decompose cache if missing.
    if not somatic.get("pass_a", {}).get("groups"):
        decompose = load_cached_analysis(_result_key(transcript, ":decompose_v3", profile))
        if decompose:
            somatic["pass_a"] = decompose
    result = run_pass_0b_condensed(somatic, lang, transcript, provider_key=profile_provider_key(profile))
    return result is not None


def _run_motifs(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_motifs(transcript, profile_provider_key(profile))
    return result is not None


def _run_raw_motifs(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_raw_motifs(transcript, profile_provider_key(profile))
    return result is not None


def _run_axes(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    result = run_axes(transcript, profile_provider_key(profile))
    return result is not None


def _run_embeddings(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    """Build embeddings from condensed analysis."""
    from llm import get_embeddings
    from cache_helpers import build_summary_from_condensed

    condensed = load_cached_analysis(_result_key(transcript, ":condense_v4", profile))
    if not condensed or not condensed.get("segments"):
        logger.error(f"No condensed data for embedding: {document_id[:8]}")
        return False

    doc = db.get_document(document_id)
    title = doc.get("title", "") if doc else ""
    summary = build_summary_from_condensed(condensed, title)
    summary_hash = hashlib.sha256(summary.encode()).hexdigest()[:16]

    embeddings, _ = get_embeddings([summary], task_type="RETRIEVAL_DOCUMENT")
    db.upsert_embedding(document_id, embeddings[0], summary, summary_hash,
                        profile_key=profile["key"])
    return True


def _run_embed_positions(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    """Build position embedding from spatial_v3 data, falling back to v2."""
    from llm import get_embeddings

    # Try v3 first
    v3_data = load_cached_analysis(_result_key(transcript, ":spatial_v3", profile))

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
        v2_data = load_cached_analysis(_result_key(transcript, ":spatial_v2", profile))
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
    db.upsert_embedding(document_id, embeddings[0], summary, summary_hash, method="positions",
                        profile_key=profile["key"])

    # Write position alternates as concept rows for tag search
    position_concepts = []
    seen = set()
    source_data = v3_data if v3_data else None
    if source_data:
        for p in source_data.get("position_summary", []):
            if isinstance(p, dict):
                val = p["position"]
                if val not in seen:
                    position_concepts.append({"category": "position_v3", "value": val})
                    seen.add(val)
                for alt in p.get("alternates", []):
                    if alt not in seen:
                        position_concepts.append({"category": "position_alt", "value": alt})
                        seen.add(alt)
    if position_concepts:
        # Remove this profile's old position_v3/position_alt rows, then insert new ones
        with db.get_cursor() as cur:
            cur.execute(
                "DELETE FROM lesson_concept WHERE document_id = %s AND profile_key = %s "
                "AND category IN ('position_v3', 'position_alt')",
                (document_id, profile["key"]),
            )
            for c in position_concepts:
                cur.execute(
                    "INSERT INTO lesson_concept (document_id, profile_key, category, value) "
                    "VALUES (%s, %s, %s, %s)",
                    (document_id, profile["key"], c["category"], c["value"]),
                )
        logger.info(f"  Wrote {len(position_concepts)} position concept rows")

    return True


def _run_concept_index(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    """Build concept index entries from concepts analysis."""
    from search.build_concept_index import build_concept_rows, build_concept_summary

    concepts_data = load_cached_analysis(_result_key(transcript, ":concept_tags_v2", profile))
    if not concepts_data or not concepts_data.get("concepts"):
        logger.error(f"No concepts data for indexing: {document_id[:8]}")
        return False

    # Upsert concept rows to lesson_concept table
    all_rows = build_concept_rows(concepts_data)
    db.upsert_concepts(document_id, all_rows, profile_key=profile["key"])

    # Build concept embedding
    from llm import get_embeddings

    # Check for spatial data too
    spatial_data = load_cached_analysis(_result_key(transcript, ":spatial_v2", profile))

    doc = db.get_document(document_id)
    title = doc.get("title", "") if doc else ""
    summary = build_concept_summary(concepts_data, title, spatial_data)
    summary_hash = hashlib.sha256(summary.encode()).hexdigest()[:16]

    embeddings, _ = get_embeddings([summary], task_type="RETRIEVAL_DOCUMENT")
    db.upsert_embedding(document_id, embeddings[0], summary, summary_hash, method="concepts",
                        profile_key=profile["key"])
    return True


def _run_spatial_index(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    """Build spatial index entries from spatial analysis."""
    from search.build_spatial_index import build_spatial_concept_rows

    spatial_data = load_cached_analysis(_result_key(transcript, ":spatial_v2", profile))
    if not spatial_data or not spatial_data.get("groups"):
        logger.error(f"No spatial data for indexing: {document_id[:8]}")
        return False

    concept_rows = build_spatial_concept_rows(spatial_data)
    db.upsert_spatial_concepts(document_id, concept_rows, profile_key=profile["key"])
    return True


def _init_extract_map(db: Database):
    """Ensure extract_map module is initialized."""
    import extract_map.pipeline as em
    if em._db is None:
        import llm_config
        llm_config.init(db)
        em.init(db, llm_config.get_llm)


def _run_extraction(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    _init_extract_map(db)
    from extract_map.pipeline import run_extraction
    result = run_extraction(transcript, profile_provider_key(profile))
    return result is not None


def _run_mapping(db: Database, document_id: str, transcript: str, profile: dict) -> bool:
    _init_extract_map(db)
    from extract_map.pipeline import run_mapping
    result = run_mapping(transcript, profile_provider_key(profile))
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
    "print_prep": _run_print_prep,
    "embed_positions": _run_embed_positions,
    "condense": _run_pass_0b_condensed,
    "embed": _run_embeddings,
    "index_concepts": _run_concept_index,
    "index_spatial": _run_spatial_index,
    "extract_steps": _run_extraction,
    "map_body_parts": _run_mapping,
    "motifs": _run_motifs,
    "raw_motifs": _run_raw_motifs,
    "axes": _run_axes,
}


def _record_run(db: Database, transcript: str, step: dict, profile: dict, elapsed: float) -> None:
    """Stamp a freshly produced result with the profile that produced it.

    Only results written by this run are stamped; a result that already carries
    a ``_run`` record came from an earlier run and keeps it. Steps without a
    cache suffix (embeddings, indexes) have no single result to stamp.
    """
    suffix = step.get("cache_suffix")
    if not suffix:
        return
    key = _result_key(transcript, suffix, profile)
    result = load_cached_analysis(key)
    if not isinstance(result, dict) or "_run" in result:
        return
    from llm import process_usage
    from llm_config import describe_llm

    # The model that actually ran: a one-off run may override the profile's.
    ran = describe_llm(profile_provider_key(profile))
    overridden = ran["overridden"] and ran["model"]
    result["_run"] = {
        "profile": profile["key"],
        "provider": ran["provider"] if overridden else profile["provider"],
        "model": ran["model"] if overridden else profile["model"],
        "params": ({"reasoning_effort": ran["reasoning_effort"]} if overridden
                   else profile.get("params") or {}),
        "llm_override": ran["llm_key"] if overridden else None,
        "contract_version": profile["contract_version"],
        "elapsed_seconds": round(elapsed, 1),
        "usage": process_usage(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    save_cached_analysis(key, result)


def run_step(db: Database, document_id: str, step_name: str, profile_key: str) -> bool | None:
    """Execute a single analysis step for a document under one profile.

    Returns True on success, False on failure, and None when the profile's
    switch is off or the step is retired (nothing is run or recorded). Designed to run in its own
    process — no shared state.
    """
    executor = STEP_EXECUTORS.get(step_name)
    if not executor:
        logger.error(f"No executor for step: {step_name}")
        return False

    profile = db.get_analysis_profile(profile_key)
    if not profile:
        logger.error(f"Unknown analysis profile: {profile_key}")
        return False
    if not profile["enabled"]:
        logger.info(f"Profile {profile_key} is switched off; not running {step_name}")
        return None

    step = db.get_analysis_step(step_name)
    if not step:
        logger.error(f"Unknown step: {step_name}")
        return False
    if step.get("retired"):
        logger.info(f"Step {step_name} is retired; not running it")
        return None

    transcript = _get_transcript(db, document_id)
    if not transcript:
        db.mark_step_error(document_id, step_name, "No transcript available", profile_key=profile_key)
        return False

    depends_on = step.get("depends_on") or []

    try:
        started = time.monotonic()
        success = executor(db, document_id, transcript, profile)
        if success:
            _record_run(db, transcript, step, profile, time.monotonic() - started)
            upstream_hash = _compute_upstream_hash(db, document_id, depends_on, profile_key)
            db.mark_step_completed(
                document_id, step_name, step["current_version"], upstream_hash,
                profile_key=profile_key, contract_version=profile["contract_version"],
            )
            return True
        else:
            db.mark_step_error(document_id, step_name, "Step returned no result", profile_key=profile_key)
            return False
    except Exception as e:
        logger.exception(f"Error running {step_name} for {document_id[:8]} ({profile_key})")
        db.mark_step_error(document_id, step_name, str(e)[:500], profile_key=profile_key)
        return False


def run_one(document_id: str, step_name: str, profile_key: str) -> bool | None:
    """Run a single step in its own process. Called via CLI.

    Prints a JSON result to stdout.
    """
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
    ok = run_step(db, document_id, step_name, profile_key)
    elapsed = time.time() - t0
    db.close()

    print(json.dumps({"ok": ok, "skipped": ok is None, "elapsed": round(elapsed, 1)}))
    return ok


def _run_step_process(document_id: str, step_name: str, profile_key: str, timeout: int) -> dict:
    root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    python_path = str(root / "src")
    env["PYTHONPATH"] = (
        python_path
        if not env.get("PYTHONPATH")
        else python_path + os.pathsep + env["PYTHONPATH"]
    )
    started = time.monotonic()
    base = {"document_id": str(document_id), "step_name": step_name, "profile": profile_key}
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kb.pipeline.analysis",
                "run-one",
                str(document_id),
                step_name,
                "--profile",
                profile_key,
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {**base, "ok": False, "error": f"timed out after {timeout}s"}
    return {
        **base,
        "ok": result.returncode == 0,
        "skipped": result.returncode == EXIT_PROFILE_DISABLED,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "error": result.stderr[-500:] if result.returncode not in (0, EXIT_PROFILE_DISABLED) else None,
    }


def _claim_key(row: dict) -> tuple[str, str, str]:
    return (str(row["document_id"]), row["profile_key"], row["step_name"])


def switch_off_idle_profiles(db: Database) -> list[str]:
    """Switch off every profile that has nothing left to run.

    A profile stays off until someone switches it on again (the /analysis page,
    or ``set_profile_enabled``): the worker never turns one on. When no profile
    is left on, the wake file goes too, so later scheduled runs exit without
    touching the database (see analysis_wake).
    """
    import analysis_wake

    runnable = db.count_runnable_steps_by_profile()
    switched_off, still_on = [], []
    for profile in db.get_analysis_profiles():
        if not profile["enabled"]:
            continue
        if runnable.get(profile["key"], 0):
            still_on.append(profile["key"])
        else:
            db.set_profile_enabled(profile["key"], False)
            switched_off.append(profile["key"])
            logger.info(f"Profile {profile['key']} has nothing left to run; switched it off")
    if not still_on:
        analysis_wake.sleep()
    return switched_off


def run_bounded_worker(
    db: Database,
    *,
    max_steps: int,
    max_seconds: int,
    workers: int,
    step_timeout: int,
) -> dict:
    """Drain runnable analysis work while holding the cross-host lease.

    Steps are claimed one at a time as slots free up. Before every claim the
    worker re-reads the profiles, so switching a profile off stops new claims
    for it immediately; steps already running finish. Each profile is held to
    its own ``max_concurrency`` within the overall ``workers`` limit.
    """
    started = time.monotonic()
    completed = failed = skipped = 0
    with db.advisory_lock(ANALYZER_LOCK) as acquired:
        if not acquired:
            return {"status": "busy", "completed": 0, "failed": 0, "skipped": 0}

        in_flight: dict = {}  # future -> row
        in_flight_keys: set[tuple[str, str, str]] = set()
        per_profile: Counter = Counter()
        backlog: list[dict] = []
        refreshed_at = 0.0
        finished_since_refresh = False

        def can_claim() -> bool:
            claimed = completed + failed + skipped + len(in_flight)
            return claimed < max_steps and time.monotonic() - started < max_seconds

        def refresh_backlog():
            nonlocal backlog, refreshed_at, finished_since_refresh
            backlog = [r for r in db.get_runnable_steps() if _claim_key(r) not in in_flight_keys]
            refreshed_at = time.monotonic()
            finished_since_refresh = False

        def backlog_is_stale() -> bool:
            # A finished step can unblock its dependents; otherwise re-read
            # periodically so new gates and documents are noticed.
            age = time.monotonic() - refreshed_at
            return (not refreshed_at or age > RUNNABLE_REFRESH_SECONDS
                    or (not backlog and finished_since_refresh))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            while True:
                if can_claim() and backlog_is_stale():
                    refresh_backlog()

                # Claim as many as the profiles' switches and limits allow.
                if can_claim() and backlog and len(in_flight) < workers:
                    profiles = {p["key"]: p for p in db.get_analysis_profiles()}
                    remaining = []
                    for row in backlog:
                        profile = profiles.get(row["profile_key"])
                        if not profile or not profile["enabled"]:
                            continue  # switched off since the backlog was read
                        if (can_claim() and len(in_flight) < workers
                                and per_profile[row["profile_key"]] < profile["max_concurrency"]):
                            future = pool.submit(
                                _run_step_process,
                                row["document_id"], row["step_name"], row["profile_key"], step_timeout,
                            )
                            in_flight[future] = row
                            in_flight_keys.add(_claim_key(row))
                            per_profile[row["profile_key"]] += 1
                        else:
                            remaining.append(row)
                    backlog = remaining

                if not in_flight:
                    if not can_claim():
                        break
                    refresh_backlog()
                    if not backlog:
                        break
                    continue

                done, _ = wait(list(in_flight), timeout=5, return_when=FIRST_COMPLETED)
                if done:
                    finished_since_refresh = True
                for future in done:
                    row = in_flight.pop(future)
                    in_flight_keys.discard(_claim_key(row))
                    per_profile[row["profile_key"]] -= 1
                    result = future.result()
                    if result.get("skipped"):
                        skipped += 1
                    elif result["ok"]:
                        completed += 1
                    else:
                        failed += 1
                    print(json.dumps(result, sort_keys=True), flush=True)

        # The Atlas projection is built from the lite enrichment artifacts, not
        # from any analysis step, so the lite enrichment worker publishes it
        # (ingest/lite_enrich.py).

        switched_off = switch_off_idle_profiles(db)

    return {
        "status": "completed",
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "switched_off": switched_off,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "remaining_runnable": db.count_runnable_steps(),
    }


def show_status(db: Database):
    """Print analysis pipeline status summary."""
    steps = db.get_analysis_steps()
    print(f"\nAnalysis Steps ({len(steps)}):")
    print(f"  {'Name':<20s} {'Version':>7s}  {'Depends On'}")
    print(f"  {'─'*20} {'─'*7}  {'─'*30}")
    for s in steps:
        deps = ", ".join(s.get("depends_on") or []) or "—"
        print(f"  {s['name']:<20s} v{s['current_version']:>5d}  {deps}")

    runnable_by_profile = db.count_runnable_steps_by_profile()
    print("\nProfiles:")
    for p in db.get_analysis_profiles():
        switch = "on " if p["enabled"] else "off"
        canonical = " (canonical)" if p["canonical"] else ""
        print(f"  {p['key']:<16s} {switch}  {p['provider']}/{p['model']}  contract v{p['contract_version']}"
              f"  runnable {runnable_by_profile.get(p['key'], 0)}{canonical}")

    runnable = db.get_runnable_steps()
    print(f"\nRunnable steps (profiles switched on): {len(runnable)}")
    if runnable:
        for r in runnable[:20]:
            print(f"  {r['document_title'][:40]:40s} {r['profile_key']:<16s} → {r['step_name']}")
        if len(runnable) > 20:
            print(f"  ... and {len(runnable) - 20} more")


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

    parser = argparse.ArgumentParser(description="Analysis pipeline runner")
    sub = parser.add_subparsers(dest="command")

    one_parser = sub.add_parser("run-one", help="Run a single step (for subprocess use)")
    one_parser.add_argument("document_id", help="Document UUID")
    one_parser.add_argument("step_name", help="Analysis step name")
    one_parser.add_argument("--profile", required=True, help="Analysis profile key")

    sub.add_parser("status", help="Show pipeline status")

    run_parser = sub.add_parser("run", help="Run a bounded leased analysis worker")
    run_parser.add_argument("--max-steps", type=int, default=100)
    run_parser.add_argument("--max-seconds", type=int, default=3600)
    run_parser.add_argument("--workers", type=int, default=2)
    run_parser.add_argument("--step-timeout", type=int, default=600)
    run_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.command == "run-one":
        ok = run_one(args.document_id, args.step_name, args.profile)
        sys.exit(0 if ok else (EXIT_PROFILE_DISABLED if ok is None else 1))
    elif args.command == "status":
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            print("ERROR: DATABASE_URL not set")
            sys.exit(1)
        db = Database(db_url)
        show_status(db)
        db.close()
    elif args.command == "run":
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            print("ERROR: DATABASE_URL not set")
            sys.exit(1)
        if min(args.max_steps, args.max_seconds, args.workers, args.step_timeout) < 1:
            parser.error("run limits must be positive")
        db = Database(db_url)
        if args.dry_run:
            runnable = db.get_runnable_steps()
            print(
                json.dumps(
                    {
                        "status": "dry-run",
                        "runnable": len(runnable),
                        "runnable_by_profile": dict(Counter(r["profile_key"] for r in runnable)),
                        "sample": runnable[: min(20, args.max_steps)],
                    },
                    default=str,
                    sort_keys=True,
                )
            )
        else:
            result = run_bounded_worker(
                db,
                max_steps=args.max_steps,
                max_seconds=args.max_seconds,
                workers=args.workers,
                step_timeout=args.step_timeout,
            )
            print(json.dumps(result, sort_keys=True))
            if result["status"] == "busy":
                sys.exit(75)
        db.close()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
