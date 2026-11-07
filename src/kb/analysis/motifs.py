"""Distinctive motifs: closed-set assignment of Feldenkrais-specific motifs.

Implements docs/DISTINCTIVE_MOTIFS.md. One versioned, cached LLM extraction
per lesson; assigns only registry motif ids (two lanes: assignments +
candidate observations). Empty results are valid and expected.

Registry: motifs_registry.yaml in configured resources.
Prompt/schema: data/motifs.md + data/motifs.schema.json (house pairing).
"""

import json
import sys
import time
from datetime import datetime, timezone

from llm_config import DEFAULT_LLM

from .core import _state, _resolve_data_path

PROMPT_VERSION = "motifs-p1"
RAW_PROMPT_VERSION = "motifs-raw-p1"
LITE_PROMPT_VERSION = "motifs-lite-p1"

_ASSIGNMENT_MODES = {"taught", "referenced"}
_ROLES = {"central", "supporting", "incidental"}
_CERTAINTIES = {"clear", "probable", "uncertain"}


def load_motifs() -> tuple[str, dict]:
    """Load the motifs prompt template and schema (house .md/.schema.json pair)."""
    prompt_path = _resolve_data_path("motifs.md")
    schema_path = _resolve_data_path("motifs.schema.json")
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_motif_registry() -> dict:
    """Load the controlled motif registry (configured overrides, then built-in resources).

    PyYAML is imported here, not at module scope: the web Lambda's layer does
    not carry it (and does not package the registry either), and a top-level
    import breaks the whole app at cold start.
    """
    import yaml

    path = _resolve_data_path("motifs_registry.yaml")
    registry = yaml.safe_load(path.read_text())
    registry["motifs"] = registry.get("motifs", [])
    return registry


def _established(registry: dict) -> list[dict]:
    return [m for m in registry["motifs"] if m.get("status") == "established"]


def _registry_block(motifs: list[dict]) -> str:
    lines = []
    for m in motifs:
        lines.append(f"### {m['id']} — {m['label']}")
        lines.append(f"Definition: {m['definition'].strip()}")
        if m.get("aliases"):
            lines.append("Aliases: " + ", ".join(m["aliases"]))
        for cond in m.get("include_when", []):
            lines.append(f"Include when: {cond}")
        for cond in m.get("exclude_when", []):
            lines.append(f"Exclude when: {cond}")
        for c in m.get("counterexamples", []):
            lines.append(f"Counterexample: {c}")
        lines.append("")
    return "\n".join(lines)


def _compact_lang_steps(lang_result: dict) -> list[dict]:
    """Trim language-enriched steps to attention/inquiry-relevant fields."""
    keep = ("step", "step_id", "step_number", "inquiry", "pedagogical_mode",
            "strategy", "sensory_channel")
    out = []
    for s in lang_result.get("enriched_steps", []) or []:
        row = {k: s[k] for k in keep if k in s and s[k]}
        if row:
            out.append(row)
    return out


def _condensed_block(condensed: dict) -> list[dict]:
    out = []
    for seg in condensed.get("segments", []) or []:
        out.append({
            "theme": seg.get("theme"),
            "setting": seg.get("setting"),
        })
    return out


def load_motifs_raw() -> tuple[str, dict]:
    """Load the registry-blind raw-motifs prompt template and schema."""
    prompt_path = _resolve_data_path("motifs_raw.md")
    schema_path = _resolve_data_path("motifs_raw.schema.json")
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def _build_lesson_input(lesson_text: str, provider_key, debug_log) -> str:
    """Assemble the bounded analysis-artifact input shared by both motif passes."""
    _pkg = sys.modules[__package__]
    somatic_result = _pkg.run_pass_0b(lesson_text, provider_key, debug_log)
    lang_result = _pkg.run_pass_0b_lang(lesson_text, provider_key, debug_log)
    condensed = _pkg.run_pass_0b_condensed(somatic_result, lang_result, lesson_text)
    lesson_input = _pkg.format_input_for_concepts(
        somatic_result["pass_a"], somatic_result.get("enriched_steps", []))
    lang_block = _compact_lang_steps(lang_result)
    if lang_block:
        lesson_input += "\n\n## Language / attention analysis (per step)\n"
        lesson_input += json.dumps(lang_block, indent=1)
    cond_block = _condensed_block(condensed)
    if cond_block:
        lesson_input += "\n\n## Condensed summary\n"
        lesson_input += json.dumps(cond_block, indent=1)
    return lesson_input


def run_raw_motifs(
    lesson_text: str,
    provider_key: str | None = None,
    debug_log: list | None = None,
) -> dict:
    """Registry-blind open-vocabulary motif observation (cached, versioned).

    The registry never enters the prompt, and the registry version is NOT part
    of the cache key — raw results survive registry bumps. The diff against
    closed-set assignments is computed offline (run_motifs.py diff).
    """
    _pkg = sys.modules["analysis"]

    cache_suffix = f":raw_motifs:{RAW_PROMPT_VERSION}"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = _pkg.get_cache_key(lesson_text.strip() + cache_suffix)
    cached = _pkg.load_cached_analysis(cache_key)
    if cached:
        return {**cached, "cached": True}

    lesson_input = _build_lesson_input(lesson_text, provider_key, debug_log)
    prompt_template, schema = load_motifs_raw()
    prompt = prompt_template.format(lesson_input=lesson_input)
    system_prompt = (
        "You identify distinctive Feldenkrais motifs from supplied analysis "
        "artifacts only. Most lessons have none; empty lists are correct. "
        "Never label ordinary themes or general principles as motifs."
    )

    llm = _state["_get_llm"](provider_key)
    t0 = time.time()
    if debug_log is not None:
        debug_log.append({
            "name": "Raw Motifs",
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system": system_prompt,
            "schema": schema,
        })
    result = llm.complete_structured_json(prompt=prompt, schema=schema, system=system_prompt)
    elapsed = round(time.time() - t0, 1)
    if debug_log is not None:
        debug_log[-1]["output"] = json.dumps(result, indent=2)
        debug_log[-1]["elapsed"] = elapsed

    raw_motifs = []
    for m in result.get("raw_motifs", []) or []:
        if (m.get("label")
                and m.get("mode") in _ASSIGNMENT_MODES
                and m.get("role") in _ROLES
                and m.get("evidence")):
            raw_motifs.append(m)

    payload = {
        "raw_motifs": raw_motifs,
        "prompt_version": RAW_PROMPT_VERSION,
        "model": getattr(llm, "model", None) or (provider_key or DEFAULT_LLM),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "elapsed": elapsed,
        "usage": llm.last_usage or {},
    }
    _pkg.save_cached_analysis(cache_key, payload)
    try:
        _pkg.save_prompt_io(cache_key, prompt, system_prompt, schema)
    except Exception:
        pass  # prompt archiving is best-effort
    return {**payload, "cached": False}


def run_motifs(
    lesson_text: str,
    provider_key: str | None = None,
    debug_log: list | None = None,
) -> dict:
    """Assign distinctive motifs to one lesson (cached, versioned).

    Runs the upstream passes (somatic incl. Pass A, language, condensed) via
    their own caches, builds a bounded input from analysis artifacts only, and
    performs one closed-set structured extraction.
    """
    _pkg = sys.modules["analysis"]
    get_cache_key = _pkg.get_cache_key
    load_cached_analysis = _pkg.load_cached_analysis
    save_cached_analysis = _pkg.save_cached_analysis

    registry = load_motif_registry()
    established = _established(registry)
    registry_version = registry.get("version", "motifs-v0")

    cache_suffix = f":distinctive_motifs:{registry_version}:{PROMPT_VERSION}"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix)
    cached = load_cached_analysis(cache_key)
    if cached:
        return {**cached, "cached": True}

    lesson_input = _build_lesson_input(lesson_text, provider_key, debug_log)
    prompt_template, schema = load_motifs()
    prompt = prompt_template.format(
        registry_version=registry_version,
        registry_block=_registry_block(established),
        lesson_input=lesson_input,
    )

    # Hard-constrain assignment ids to the established registry.
    schema = json.loads(json.dumps(schema))  # deep copy
    ids = [m["id"] for m in established]
    schema["properties"]["assignments"]["items"]["properties"]["motif_id"]["enum"] = ids

    system_prompt = (
        "You identify distinctive Feldenkrais motifs from supplied analysis "
        "artifacts only. Most lessons have none; empty lists are correct. "
        "Never assign an id outside the registry."
    )

    llm = _state["_get_llm"](provider_key)
    t0 = time.time()
    if debug_log is not None:
        debug_log.append({
            "name": "Distinctive Motifs",
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system": system_prompt,
            "schema": schema,
        })

    result = llm.complete_structured_json(prompt=prompt, schema=schema, system=system_prompt)
    elapsed = round(time.time() - t0, 1)
    if debug_log is not None:
        debug_log[-1]["output"] = json.dumps(result, indent=2)
        debug_log[-1]["elapsed"] = elapsed

    # Belt-and-braces post-validation (drop anything malformed rather than fail).
    assignments = []
    for a in result.get("assignments", []) or []:
        if (a.get("motif_id") in ids
                and a.get("mode") in _ASSIGNMENT_MODES
                and a.get("role") in _ROLES
                and a.get("certainty") in _CERTAINTIES
                and a.get("evidence")):
            assignments.append(a)
    candidates = list(result.get("candidate_motifs", []) or [])[:2]

    payload = {
        "assignments": assignments,
        "candidate_motifs": candidates,
        "registry_version": registry_version,
        "prompt_version": PROMPT_VERSION,
        "model": getattr(llm, "model", None) or (provider_key or DEFAULT_LLM),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "elapsed": elapsed,
        "usage": llm.last_usage or {},
    }
    save_cached_analysis(cache_key, payload)
    try:
        _pkg.save_prompt_io(cache_key, prompt, system_prompt, schema)
    except Exception:
        pass  # prompt archiving is best-effort
    return {**payload, "cached": False}


def run_motifs_lite(
    lesson_text: str,
    provider_key: str | None = None,
    debug_log: list | None = None,
) -> dict:
    """Assign established motifs directly from a raw transcript."""
    _pkg = sys.modules[__package__]
    registry = load_motif_registry()
    established = _established(registry)
    registry_version = registry.get("version", "motifs-v0")
    suffix = f":distinctive_motifs_lite:{registry_version}:{LITE_PROMPT_VERSION}"
    if provider_key and provider_key != DEFAULT_LLM:
        suffix += f":{provider_key}"
    cache_key = _pkg.get_cache_key(lesson_text.strip() + suffix)
    cached = _pkg.load_cached_analysis(cache_key)
    if cached:
        return {**cached, "cached": True}

    prompt = (_resolve_data_path("motifs_lite.md")).read_text().format(
        registry_version=registry_version,
        registry_block=_registry_block(established),
        lesson_text=lesson_text,
    )
    schema = json.loads((_resolve_data_path("motifs_lite.schema.json")).read_text())
    ids = [motif["id"] for motif in established]
    schema["properties"]["assignments"]["items"]["properties"]["motif_id"]["enum"] = ids
    system_prompt = (
        "Assign only established distinctive Feldenkrais motifs supported by "
        "the transcript. Empty assignments are valid."
    )
    llm = _state["_get_llm"](provider_key)
    t0 = time.time()
    result = llm.complete_structured_json(prompt=prompt, schema=schema, system=system_prompt)
    elapsed = round(time.time() - t0, 1)
    assignments = []
    for assignment in result.get("assignments", []) or []:
        evidence = assignment.get("evidence") or []
        if (assignment.get("motif_id") in ids
                and assignment.get("mode") in _ASSIGNMENT_MODES
                and assignment.get("role") in _ROLES
                and assignment.get("certainty") in _CERTAINTIES
                and all(isinstance(quote, str) and quote.strip() for quote in evidence)):
            assignments.append({**assignment, "evidence": evidence[:2]})
    payload = {
        "assignments": assignments,
        "registry_version": registry_version,
        "prompt_version": LITE_PROMPT_VERSION,
        "model": getattr(llm, "model", None) or (provider_key or DEFAULT_LLM),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "elapsed": elapsed,
        "usage": llm.last_usage or {},
    }
    _pkg.save_cached_analysis(cache_key, payload)
    return {**payload, "cached": False}
