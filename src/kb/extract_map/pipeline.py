"""Extraction + Mapping pipeline stages.

Each stage takes lesson text, calls an LLM with structured output,
and caches the result. Stage 2 (mapping) depends on Stage 1 (extraction).

Initialize with init(db, get_llm_fn) before calling any functions.
"""

import json
import time
from pathlib import Path

from cache_helpers import get_cache_key, load_cached_analysis, save_cached_analysis
from llm_config import DEFAULT_LLM

_DATA_DIR = Path(__file__).parent / "data"

_db = None
_get_llm = None


def init(db, get_llm_fn):
    """Initialize the extract_map module."""
    global _db, _get_llm
    _db = db
    _get_llm = get_llm_fn


def _load_prompt_and_schema(name: str) -> tuple[str, dict]:
    """Load a prompt template and JSON schema by name."""
    prompt_path = _DATA_DIR / f"{name}.md"
    schema_path = _DATA_DIR / f"{name}.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def run_extraction(lesson_text: str, provider_key: str | None = None,
                   debug_log: list | None = None) -> dict:
    """
    Stage 1: Extract the sequence of instructions from a lesson transcript.

    Returns dict with steps, elapsed, cached.
    """
    prompt_template, schema = _load_prompt_and_schema("extraction")
    prompt = prompt_template + lesson_text

    system_prompt = (
        "You are an expert Feldenkrais practitioner extracting every instruction "
        "from a lesson transcript. Preserve the teacher's exact language. "
        "Never infer instructions not explicitly in the text. "
        "When ambiguous between move and attend, default to attend."
    )

    cache_suffix = ":extraction_v2"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix)
    cached = load_cached_analysis(cache_key)
    if cached:
        return {
            "steps": cached.get("steps", []),
            "cached": True,
        }

    llm = _get_llm(provider_key)
    t0 = time.time()

    if debug_log is not None:
        debug_log.append({
            "name": "Extraction",
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system": system_prompt,
            "schema": schema,
        })

    try:
        result = llm.complete_structured_json(
            prompt=prompt,
            schema=schema,
            system=system_prompt,
        )
        elapsed = round(time.time() - t0, 1)

        if debug_log is not None:
            debug_log[-1]["output"] = json.dumps(result, indent=2)
            debug_log[-1]["elapsed"] = elapsed

        usage = llm.last_usage if llm.last_usage else {}

        steps = result.get("steps", [])
        save_cached_analysis(cache_key, {"steps": steps, "usage": usage})

        return {
            "steps": steps,
            "elapsed": elapsed,
            "cached": False,
            "usage": usage,
        }

    except Exception as e:
        if debug_log is not None:
            debug_log[-1]["error"] = str(e)
        raise


def run_mapping(lesson_text: str, extraction_result: dict | None = None,
                provider_key: str | None = None,
                debug_log: list | None = None) -> dict:
    """
    Stage 2: Map extracted instructions to canonical ontology terms.

    Runs extraction first if extraction_result is not provided.

    Returns dict with extraction, body_map, vocabulary_map, ontology_gaps, elapsed, cached.
    """
    if extraction_result is None:
        extraction_result = run_extraction(lesson_text, provider_key, debug_log)

    extraction_json = json.dumps({
        "steps": extraction_result.get("steps", []),
    }, indent=2)

    prompt_template, schema = _load_prompt_and_schema("mapping")
    prompt = prompt_template + extraction_json

    system_prompt = (
        "You are an expert Feldenkrais practitioner mapping lesson features to a canonical ontology. "
        "Only map to canonical terms provided in the ontology. If no term fits, output to ontology_gaps. "
        "Do not invent canonical terms. Include low-confidence mappings — flag them, do not omit them."
    )

    cache_suffix = ":mapping_v3"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix)
    cached = load_cached_analysis(cache_key)
    if cached:
        return {
            "extraction": extraction_result,
            "body_map": cached.get("body_map", {}),
            "vocabulary_map": cached.get("vocabulary_map", []),
            "ontology_gaps": cached.get("ontology_gaps", []),
            "cached": True,
        }

    llm = _get_llm(provider_key)
    t0 = time.time()

    if debug_log is not None:
        debug_log.append({
            "name": "Mapping",
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system": system_prompt,
            "schema": schema,
        })

    try:
        result = llm.complete_structured_json(
            prompt=prompt,
            schema=schema,
            system=system_prompt,
        )
        elapsed = round(time.time() - t0, 1)

        if debug_log is not None:
            debug_log[-1]["output"] = json.dumps(result, indent=2)
            debug_log[-1]["elapsed"] = elapsed

        usage = llm.last_usage if llm.last_usage else {}

        payload = {
            "body_map": result.get("body_map", {}),
            "vocabulary_map": result.get("vocabulary_map", []),
            "ontology_gaps": result.get("ontology_gaps", []),
        }
        save_cached_analysis(cache_key, {**payload, "usage": usage})

        return {
            "extraction": extraction_result,
            **payload,
            "elapsed": elapsed,
            "cached": False,
            "usage": usage,
        }

    except Exception as e:
        if debug_log is not None:
            debug_log[-1]["error"] = str(e)
        raise


def run_full_pipeline(lesson_text: str, provider_key: str | None = None,
                      debug_log: list | None = None) -> dict:
    """Run both stages: extraction then mapping. Returns combined result."""
    extraction = run_extraction(lesson_text, provider_key, debug_log)
    mapping = run_mapping(lesson_text, extraction, provider_key, debug_log)
    return mapping
