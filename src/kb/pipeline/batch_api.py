"""Batch analysis pipeline runner using the Gemini Batch API.

Collects documents awaiting a given analysis step, assembles prompts for all
of them, submits a single batch job to Gemini at 50% cost, polls for
completion, and saves results.

Usage:
    # Submit a batch for all documents needing the 'somatic' step
    .venv/bin/python -m kb.pipeline.batch_api submit somatic

    # Check status of a batch job
    .venv/bin/python -m kb.pipeline.batch_api status <job_name>

    # Poll until complete and save results
    .venv/bin/python -m kb.pipeline.batch_api wait <job_name>

    # Submit, wait, and save in one go
    .venv/bin/python -m kb.pipeline.batch_api run somatic

    # List recent batch jobs
    .venv/bin/python -m kb.pipeline.batch_api list
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google import genai
from google.genai import types as gtypes

logger = logging.getLogger(__name__)

BATCH_MODEL = "gemini-3-flash-preview"


def _init():
    """Initialize DB, cache, analysis modules. Returns db."""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

    from database import Database
    import cache_helpers
    import llm_config
    import analysis as analysis_mod

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")

    db = Database(db_url)
    cache_helpers.init(db)
    llm_config.init(db)
    data_dir = Path(__file__).parent.parent.parent.parent / "data"
    analysis_mod.init(db, data_dir, llm_config.get_llm)
    return db


def _get_transcript(db, document_id: str) -> str | None:
    with db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT data->>'text' as text FROM analysis WHERE document_id = %s AND type_id = 1",
            (document_id,),
        )
        row = cur.fetchone()
        return row["text"].strip() if row and row["text"] else None


# ── Prompt builders per step ──────────────────────────────────────────────
# Each returns (prompt, system_prompt, schema, cache_key, parse_fn)
# parse_fn transforms raw LLM result dict into the dict to cache

def _build_decompose(transcript, db):
    import analysis as _a
    pass_0_result = _a.run_pass_0(transcript)
    segmented_text = _a.format_text_with_segments(pass_0_result)
    tmpl, schema = _a.load_pass_a()
    if not tmpl:
        return None
    prompt = tmpl + "\n\n---\n\n## Lesson to Analyze:\n\n" + segmented_text
    system = _a.ROUTER_SYSTEM_PROMPT
    cache_key = _a.get_cache_key(transcript + ":decompose_v3")

    def parse(result):
        all_input_ids = set(s["id"] for s in pass_0_result.get("segments", []))
        for step in result.get("steps", []):
            if not isinstance(step, dict):
                continue
            if "segment_start" in step and "segment_end" in step and "segments" not in step:
                step["segments"] = sorted(
                    sid for sid in all_input_ids if step["segment_start"] <= sid <= step["segment_end"]
                )
        result["pass_0"] = pass_0_result
        return result

    return prompt, system, schema, cache_key, parse


def _build_somatic(transcript, db):
    import analysis as _a
    pa_data = _a.load_cached_analysis(_a.get_cache_key(transcript + ":decompose_v3"))
    if not pa_data:
        return None
    tmpl, schema = _a.load_pass_0b()
    prompt = tmpl + _a.format_steps_for_0b(pa_data)
    system = ("You are an expert Feldenkrais practitioner enriching lesson steps. "
              "Process every step. Do not skip, merge, or split steps.")
    cache_key = _a.get_cache_key(transcript.strip() + ":somatic_v2")

    def parse(result):
        return {"enriched_steps": result.get("steps", []), "summary": result.get("summary", {})}

    return prompt, system, schema, cache_key, parse


def _build_language(transcript, db):
    import analysis as _a
    pa_data = _a.load_cached_analysis(_a.get_cache_key(transcript + ":decompose_v3"))
    if not pa_data:
        return None
    tmpl, schema = _a.load_pass_0b_lang()
    prompt = tmpl + _a.format_steps_for_0b(pa_data)
    system = ("You are an expert in Feldenkrais pedagogy and language analysis. "
              "Analyze the teacher's language choices at each step. "
              "Process every step. Do not skip, merge, or split steps.")
    cache_key = _a.get_cache_key(transcript.strip() + ":language_v1")

    def parse(result):
        return {"enriched_steps": result.get("steps", []), "summary": result.get("summary", {})}

    return prompt, system, schema, cache_key, parse


def _build_language_patterns(transcript, db):
    import analysis as _a
    pa_data = _a.load_cached_analysis(_a.get_cache_key(transcript + ":decompose_v3"))
    lang_data = _a.load_cached_analysis(_a.get_cache_key(transcript.strip() + ":language_v1"))
    if not pa_data or not lang_data:
        return None
    steps_text = _a.format_steps_for_word_lang(pa_data, lang_data.get("enriched_steps", []))
    tmpl, schema = _a.load_pass_0b_word_lang()
    prompt = tmpl + steps_text
    system = ("You are an expert in Feldenkrais pedagogy and language analysis. "
              "Annotate the teacher's language at the word/phrase level. "
              "Process every step. Do not skip, merge, or split steps.")
    cache_key = _a.get_cache_key(transcript.strip() + ":language_patterns_v3")

    def parse(result):
        from collections import defaultdict
        by_step = defaultdict(list)
        for ann in result.get("annotations", []):
            sn = ann.get("step_number")
            if sn is not None:
                by_step[sn].append({"text": ann.get("text", ""), "category": ann.get("category", ""), "note": ann.get("note", "")})
        return {"word_annotations": [{"step_number": sn, "phrases": p} for sn, p in sorted(by_step.items())]}

    return prompt, system, schema, cache_key, parse


def _build_concepts(transcript, db):
    import analysis as _a
    pa_data = _a.load_cached_analysis(_a.get_cache_key(transcript + ":decompose_v3"))
    som_data = _a.load_cached_analysis(_a.get_cache_key(transcript.strip() + ":somatic_v2"))
    if not pa_data or not som_data:
        return None
    concept_input = _a.format_input_for_concepts(pa_data, som_data.get("enriched_steps", []))
    tmpl, schema = _a.load_pass_0b_concepts()
    prompt = tmpl + concept_input
    system = ("You are an expert Feldenkrais practitioner tagging a lesson with "
              "searchable concepts. Be specific and distinctive — avoid generic terms "
              "that apply to every lesson.")
    cache_key = _a.get_cache_key(transcript.strip() + ":concept_tags_v2")

    def parse(result):
        return {"concepts": result.get("concepts", []),
                "distinctive_summary": result.get("distinctive_summary", ""),
                "search_terms": result.get("search_terms", [])}

    return prompt, system, schema, cache_key, parse


def _build_spatial(transcript, db):
    import analysis as _a
    pa_data = _a.load_cached_analysis(_a.get_cache_key(transcript + ":decompose_v3"))
    som_data = _a.load_cached_analysis(_a.get_cache_key(transcript.strip() + ":somatic_v2"))
    if not pa_data or not som_data:
        return None
    spatial_input = _a.format_input_for_spatial(pa_data, som_data.get("enriched_steps", []))
    tmpl, schema = _a.load_pass_0b_spatial()
    prompt = tmpl + spatial_input
    system = ("You are a factual observer describing the physical geometry of body "
              "positions and configurations. Report only what is stated — no "
              "interpretation, no biomechanical analysis, no pedagogical language.")
    cache_key = _a.get_cache_key(transcript.strip() + ":spatial_v2")

    def parse(result):
        return {"groups": result.get("groups", [])}

    return prompt, system, schema, cache_key, parse


def _build_condense(transcript, db):
    import analysis as _a
    pa_data = _a.load_cached_analysis(_a.get_cache_key(transcript + ":decompose_v3"))
    som_data = _a.load_cached_analysis(_a.get_cache_key(transcript.strip() + ":somatic_v2"))
    lang_data = _a.load_cached_analysis(_a.get_cache_key(transcript.strip() + ":language_v1"))
    if not pa_data or not som_data or not lang_data:
        return None
    cond_input = _a.format_input_for_0b_condensed(
        pa_data, som_data.get("enriched_steps", []), som_data.get("summary", {}),
        lang_data.get("enriched_steps", []), lang_data.get("summary", {}),
    )
    tmpl, schema = _a.load_pass_0b_condensed()
    prompt = tmpl + cond_input
    system = ("You are an expert Feldenkrais practitioner creating condensed lesson cards. "
              "Preserve all five points of view. Generate group cards first, then the setting card.")
    cache_key = _a.get_cache_key(transcript.strip() + ":condense_v4")

    def parse(result):
        return {"segments": [{"segment_number": 1, "theme": "",
                              "group_cards": result.get("group_cards", []),
                              "setting": result.get("setting", {})}]}

    return prompt, system, schema, cache_key, parse


STEP_BUILDERS = {
    "decompose": _build_decompose,
    "somatic": _build_somatic,
    "language": _build_language,
    "language_patterns": _build_language_patterns,
    "concept_tags": _build_concepts,
    "spatial": _build_spatial,
    "condense": _build_condense,
}


# ── Batch submission and polling ──────────────────────────────────────────

def submit_batch(db, step_name: str, document_ids: list[str] | None = None, dry_run: bool = False) -> dict:
    """Assemble prompts and submit a Gemini batch job.

    Returns {"job_name": str, "count": int, "doc_map": {index: {...}}}
    """
    import cache_helpers
    builder = STEP_BUILDERS.get(step_name)
    if not builder:
        raise ValueError(f"No batch builder for step: {step_name}. Available: {list(STEP_BUILDERS.keys())}")

    if document_ids:
        docs = [{"document_id": did, "document_title": did[:8]} for did in document_ids]
    else:
        runnable = db.get_runnable_steps()
        docs = [r for r in runnable if r["step_name"] == step_name]

    if not docs:
        logger.info(f"No documents to process for step '{step_name}'")
        return {"job_name": None, "count": 0, "doc_map": {}}

    logger.info(f"Building prompts for {len(docs)} documents, step '{step_name}'")

    requests = []
    doc_map = {}

    for doc in docs:
        doc_id = str(doc["document_id"])
        transcript = _get_transcript(db, doc_id)
        if not transcript:
            logger.warning(f"  SKIP {doc_id[:8]} — no transcript")
            continue

        built = builder(transcript, db)
        if built is None:
            logger.warning(f"  SKIP {doc_id[:8]} — missing upstream data")
            continue

        prompt, system, schema, cache_key, parse_fn = built

        if cache_helpers.load_cached_analysis(cache_key):
            logger.info(f"  SKIP {doc_id[:8]} — already cached")
            continue

        clean_schema = {k: v for k, v in schema.items() if k not in ("$schema", "$id")}

        is_gemini3 = "gemini-3" in BATCH_MODEL
        config = {
            "max_output_tokens": 65536,
            "temperature": 1.0 if is_gemini3 else 0.0,
            "response_mime_type": "application/json",
            "response_json_schema": clean_schema,
        }
        if is_gemini3:
            config["thinking_config"] = gtypes.ThinkingConfig(thinking_level="MINIMAL")

        if system:
            config["system_instruction"] = system
        req = {"contents": [{"parts": [{"text": prompt}], "role": "user"}], "config": config}

        idx = len(requests)
        requests.append(req)
        doc_map[str(idx)] = {
            "document_id": doc_id,
            "cache_key": cache_key,
            "step_name": step_name,
            "title": doc.get("document_title", doc_id[:8]),
        }

    if not requests:
        logger.info("No requests to submit (all cached or skipped)")
        return {"job_name": None, "count": 0, "doc_map": {}}

    logger.info(f"Prepared {len(requests)} requests")

    if dry_run:
        for idx, meta in sorted(doc_map.items(), key=lambda x: int(x[0])):
            print(f"  [{idx}] {meta['title'][:50]} — {meta['cache_key']}")
        return {"job_name": "DRY_RUN", "count": len(requests), "doc_map": doc_map}

    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    batch_job = client.batches.create(
        model=f"models/{BATCH_MODEL}",
        src=requests,
        config={"display_name": f"feldenkrais-{step_name}-{len(requests)}"},
    )

    job_name = batch_job.name
    logger.info(f"Batch submitted: {job_name} ({len(requests)} requests)")

    cache_helpers.save_cached_analysis(f"batch_job:{job_name}", {
        "step_name": step_name,
        "count": len(requests),
        "doc_map": doc_map,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

    return {"job_name": job_name, "count": len(requests), "doc_map": doc_map}


def check_status(job_name: str) -> dict:
    """Check the status of a batch job."""
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    job = client.batches.get(name=job_name)
    return {
        "name": job.name,
        "state": job.state.name if hasattr(job.state, 'name') else str(job.state),
        "display_name": getattr(job, 'display_name', ''),
    }


def wait_and_save(db, job_name: str, poll_interval: int = 30) -> dict:
    """Poll a batch job until complete, then save results.

    Returns {"saved": int, "errors": int, "state": str}
    """
    import cache_helpers
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

    job_meta = cache_helpers.load_cached_analysis(f"batch_job:{job_name}")
    if not job_meta:
        raise ValueError(f"No metadata found for job {job_name}")

    doc_map = job_meta["doc_map"]
    step_name = job_meta["step_name"]
    builder = STEP_BUILDERS.get(step_name)

    while True:
        job = client.batches.get(name=job_name)
        state = job.state.name if hasattr(job.state, 'name') else str(job.state)
        logger.info(f"Job {job_name}: {state}")
        if state in ('JOB_STATE_SUCCEEDED', 'JOB_STATE_FAILED',
                      'JOB_STATE_CANCELLED', 'JOB_STATE_EXPIRED'):
            break
        time.sleep(poll_interval)

    if state != 'JOB_STATE_SUCCEEDED':
        logger.error(f"Job ended with state: {state}")
        return {"saved": 0, "errors": 0, "state": state}

    # Get responses — the SDK puts them in dest.inlined_responses
    responses = []
    dest = getattr(job, 'dest', None)
    if dest:
        responses = getattr(dest, 'inlined_responses', None) or []
    if not responses:
        resp = getattr(job, 'response', None)
        if resp:
            responses = getattr(resp, 'inlined_responses', None) or getattr(resp, 'inline_responses', None) or []
    logger.info(f"Got {len(responses)} responses")

    saved = 0
    errors = 0

    for idx, response in enumerate(responses):
        meta = doc_map.get(str(idx))
        if not meta:
            logger.warning(f"No metadata for response index {idx}")
            continue

        doc_id = meta["document_id"]
        cache_key = meta["cache_key"]

        try:
            # InlinedResponse wraps a GenerateContentResponse in .response
            inner = getattr(response, 'response', response)
            raw_text = inner.text if hasattr(inner, 'text') else inner.candidates[0].content.parts[0].text
            result = json.loads(raw_text)

            transcript = _get_transcript(db, doc_id)
            built = builder(transcript, db)
            if built:
                _, _, _, _, parse_fn = built
                cache_data = parse_fn(result)
            else:
                cache_data = result

            cache_helpers.save_cached_analysis(cache_key, cache_data)

            step_info = db.get_analysis_step(step_name)
            if step_info:
                depends_on = step_info.get("depends_on") or []
                from pipeline.analysis import _compute_upstream_hash
                upstream_hash = _compute_upstream_hash(db, doc_id, depends_on)
                db.mark_step_completed(doc_id, step_name, step_info["current_version"], upstream_hash)

            saved += 1
            logger.info(f"  Saved {meta.get('title', doc_id[:8])}")

        except Exception as e:
            errors += 1
            logger.error(f"  Error processing {doc_id[:8]}: {e}")
            db.mark_step_error(doc_id, step_name, str(e)[:500])

    logger.info(f"Done: {saved} saved, {errors} errors")
    return {"saved": saved, "errors": errors, "state": state}


def list_jobs():
    """List recent batch jobs."""
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    for job in client.batches.list(config=gtypes.ListBatchJobsConfig(page_size=20)):
        state = job.state.name if hasattr(job.state, 'name') else str(job.state)
        name = getattr(job, 'display_name', '')
        print(f"  {job.name}  {state:25s}  {name}")


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Batch analysis pipeline (Gemini Batch API, 50% cost)")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("submit", help="Submit a batch job for an analysis step")
    p.add_argument("step", choices=list(STEP_BUILDERS.keys()))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--document", action="append", help="Specific document ID(s)")

    p = sub.add_parser("status", help="Check batch job status")
    p.add_argument("job_name")

    p = sub.add_parser("wait", help="Poll and save results")
    p.add_argument("job_name")
    p.add_argument("--interval", type=int, default=30)

    p = sub.add_parser("run", help="Submit, wait, and save in one go")
    p.add_argument("step", choices=list(STEP_BUILDERS.keys()))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--document", action="append", help="Specific document ID(s)")

    sub.add_parser("list", help="List recent batch jobs")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "list":
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
        list_jobs()
        return

    if args.command == "status":
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
        print(json.dumps(check_status(args.job_name), indent=2))
        return

    db = _init()

    if args.command == "submit":
        result = submit_batch(db, args.step, args.document, args.dry_run)
        print(json.dumps({k: v for k, v in result.items() if k != "doc_map"}, indent=2))
        if result["job_name"] and result["job_name"] != "DRY_RUN":
            print(f"\nTo check: .venv/bin/python -m kb.pipeline.batch_api status {result['job_name']}")
            print(f"To wait:  .venv/bin/python -m kb.pipeline.batch_api wait {result['job_name']}")

    elif args.command == "wait":
        result = wait_and_save(db, args.job_name, args.interval)
        print(json.dumps(result, indent=2))

    elif args.command == "run":
        result = submit_batch(db, args.step, args.document, args.dry_run)
        if args.dry_run or not result["job_name"]:
            print(json.dumps({k: v for k, v in result.items() if k != "doc_map"}, indent=2))
        else:
            print(f"Submitted: {result['job_name']} ({result['count']} requests)")
            print("Waiting for completion...")
            save_result = wait_and_save(db, result["job_name"])
            print(json.dumps(save_result, indent=2))

    db.close()


if __name__ == "__main__":
    main()
