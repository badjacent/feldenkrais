"""Movement axes: ordinal practitioner-facing classification per lesson.

Four axes (flexion_extension, rotation, side_bending, cross_lateral), each an
ordinal value with step-cited evidence. One versioned, cached LLM extraction
per lesson over the same analysis-artifact input as the motif passes. No
registry involved; the cache key depends only on the prompt version.

Prompt/schema: data/axes.md + data/axes.schema.json (house pairing).
"""

import json
import sys
import time
from datetime import datetime, timezone

from llm_config import DEFAULT_LLM

from .core import _state
from .motifs import _build_lesson_input

AXES_PROMPT_VERSION = "axes-p1"

AXIS_VALUES = {
    "flexion_extension": ["strongly_flexion", "flexion_leaning", "mixed_or_balanced",
                          "extension_leaning", "strongly_extension"],
    "rotation": ["none", "minor", "moderate", "central"],
    "side_bending": ["none", "minor", "moderate", "central"],
    "cross_lateral": ["none", "minor", "moderate", "central"],
}


def load_axes() -> tuple[str, dict]:
    """Load the movement-axes prompt template and schema."""
    prompt_path = _state["_data_dir"] / "axes.md"
    schema_path = _state["_data_dir"] / "axes.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def run_axes(
    lesson_text: str,
    provider_key: str | None = None,
    debug_log: list | None = None,
) -> dict:
    """Classify one lesson along the four movement axes (cached, versioned)."""
    _pkg = sys.modules["analysis"]

    cache_suffix = f":movement_axes:{AXES_PROMPT_VERSION}"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = _pkg.get_cache_key(lesson_text.strip() + cache_suffix)
    cached = _pkg.load_cached_analysis(cache_key)
    if cached:
        return {**cached, "cached": True}

    lesson_input = _build_lesson_input(lesson_text, provider_key, debug_log)
    prompt_template, schema = load_axes()
    prompt = prompt_template.format(lesson_input=lesson_input)
    system_prompt = (
        "You classify Feldenkrais lessons along movement axes from supplied "
        "analysis artifacts only. Judge overall emphasis, cite step evidence, "
        "and never infer beyond the supplied material."
    )

    llm = _state["_get_llm"](provider_key)
    t0 = time.time()
    if debug_log is not None:
        debug_log.append({
            "name": "Movement Axes",
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

    axes = {}
    for axis, values in AXIS_VALUES.items():
        a = result.get(axis) or {}
        if a.get("value") in values:
            axes[axis] = {"value": a["value"], "evidence": a.get("evidence", [])}

    payload = {
        "axes": axes,
        "prompt_version": AXES_PROMPT_VERSION,
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
