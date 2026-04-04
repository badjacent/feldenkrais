"""Pass A: Decomposition + Routing.

Segments lesson text and classifies it according to VL frameworks.
"""

import sys

from llm_config import DEFAULT_LLM

from .core import (
    _state,
    _framework_version_suffix,
    get_active_frameworks_config,
)
from .framework_builders import (
    _derive_key,
    ROUTER_SYSTEM_PROMPT,
)


def load_pass_a(framework_version: str | None = None) -> tuple[str, dict]:
    """Load Pass A prompt and schema (Decomposition + Routing).

    The prompt's {CLASSIFICATION_TABLE} placeholder is populated from
    the framework registry, and the schema's classification properties
    are built dynamically. Prompt comes from versioned PROMPTS_DIR,
    schema from shared DATA_DIR.
    """
    _pkg = sys.modules["analysis"]

    prompts_dir, _ = _pkg._resolve_prompts_dir(framework_version or _state["FRAMEWORK_VERSION"])
    prompt_path = prompts_dir / "pass_a.md"

    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    fv = framework_version or _state["FRAMEWORK_VERSION"]
    prompt = prompt.replace("{CLASSIFICATION_TABLE}", _pkg.build_classification_table(fv))

    schema = _pkg.build_pass_a_schema(fv)

    return prompt, schema


def _pass_a_cache_key(lesson_text: str, provider_key: str | None = None, framework_version: str | None = None) -> str:
    """Cache key for Pass A results, independent of Pass B provider."""
    _pkg = sys.modules["analysis"]
    suffix = ":decompose_v3" + _framework_version_suffix(framework_version)
    if provider_key and provider_key != DEFAULT_LLM:
        suffix += f":{provider_key}"
    return _pkg.get_cache_key(lesson_text + suffix)


def run_pass_a(lesson_text: str, provider_key: str | None = None, debug_log: list | None = None, framework_version: str | None = None) -> dict:
    """Run Pass A: Decomposition + Routing.

    Internally runs Pass 0 to segment text, then passes segmented text to LLM.
    Returns VL classification, SPIFFER-Q decomposed steps, AND Pass 0 segments.
    Results are cached independently of Pass B provider.
    """
    # Late-bind all sibling functions through the package so test patches work
    _pkg = sys.modules["analysis"]
    load_cached_analysis = _pkg.load_cached_analysis
    save_cached_analysis = _pkg.save_cached_analysis

    fv = framework_version
    # Check Pass A cache first
    pa_cache_key = _pass_a_cache_key(lesson_text, provider_key, fv)
    cached = load_cached_analysis(pa_cache_key)
    if cached:
        if debug_log is not None:
            debug_log.append({
                "name": "Pass A",
                "provider": provider_key or DEFAULT_LLM,
                "cached": True,
            })
        return cached

    # Run Pass 0 to segment the text
    pass_0_result = _pkg.run_pass_0(lesson_text)
    segmented_text = _pkg.format_text_with_segments(pass_0_result)

    pass_a_prompt, pass_a_schema = _pkg.load_pass_a(fv)

    if not pass_a_prompt or not pass_a_schema:
        # Return default if Pass A not configured
        active = get_active_frameworks_config(fv)
        default_cls = {_derive_key(f): (1.0 if i == 0 else 0.0) for i, f in enumerate(active)}
        return {
            "classifications": default_cls,
            "primary_framework": active[0]["id"] if active else "VL-1",
            "rationale": "Pass A not configured, defaulting to first framework",
            "steps": [],
            "pass_0": pass_0_result,
        }

    prompt = pass_a_prompt + "\n\n---\n\n## Lesson to Analyze:\n\n" + segmented_text

    llm = _state["_get_llm"](provider_key)
    result = llm.complete_structured_json(
        prompt,
        pass_a_schema,
        system=ROUTER_SYSTEM_PROMPT,
    )

    if debug_log is not None:
        debug_log.append({
            "name": "Pass A",
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system": ROUTER_SYSTEM_PROMPT,
            "schema": pass_a_schema,
            "output": result,
        })

    # Normalize segment_start/segment_end -> segments array for downstream compatibility
    all_input_ids = set(s["id"] for s in pass_0_result.get("segments", []))
    for step in result.get("steps", []):
        if "segment_start" in step and "segment_end" in step and "segments" not in step:
            seg_start = step["segment_start"]
            seg_end = step["segment_end"]
            step["segments"] = sorted(
                sid for sid in all_input_ids if seg_start <= sid <= seg_end
            )

    # Validate segment coverage -- check for gaps
    all_covered_ids = set()
    for step in result.get("steps", []):
        all_covered_ids.update(step.get("segments", []))
    missing_ids = sorted(all_input_ids - all_covered_ids)
    if missing_ids:
        import logging
        logging.warning(
            f"Pass A segment gap: {len(missing_ids)} segments not assigned to any step: "
            f"{missing_ids[:20]}{'...' if len(missing_ids) > 20 else ''}"
        )
        result["segment_gaps"] = missing_ids

    # Attach Pass 0 segments to result for UI rendering
    result["pass_0"] = pass_0_result

    # Cache Pass A result independently
    save_cached_analysis(pa_cache_key, result)
    _pkg.save_prompt_io(pa_cache_key, prompt, ROUTER_SYSTEM_PROMPT, pass_a_schema)

    return result
