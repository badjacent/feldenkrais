"""Pass 0b Spatial: Per-group spatial configuration extraction."""

import json
import sys

from llm_config import DEFAULT_LLM

from .core import _state


def run_pass_0b_spatial(
    lesson_text: str,
    provider_key: str | None = None,
    debug_log: list | None = None,
) -> dict:
    """
    Pass 0b Spatial: Per-group spatial configuration extraction.

    Runs Pass 0b (which runs Pass A internally), then extracts spatial
    configurations for each group.
    """
    import time

    _pkg = sys.modules["analysis"]
    get_cache_key = _pkg.get_cache_key
    load_cached_analysis = _pkg.load_cached_analysis
    save_cached_analysis = _pkg.save_cached_analysis

    somatic_result = _pkg.run_pass_0b(lesson_text, provider_key, debug_log)
    pass_a_result = somatic_result["pass_a"]
    somatic_steps = somatic_result.get("enriched_steps", [])

    spatial_input = _pkg.format_input_for_spatial(pass_a_result, somatic_steps)

    prompt_template, schema = _pkg.load_pass_0b_spatial()
    prompt = prompt_template + spatial_input

    system_prompt = (
        "You are a factual observer describing the physical geometry of body "
        "positions and configurations. Report only what is stated — no "
        "interpretation, no biomechanical analysis, no pedagogical language."
    )

    cache_suffix = ":spatial_v2"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix)
    cached = load_cached_analysis(cache_key)
    if cached:
        return {
            "groups": cached.get("groups", []),
            "cached": True,
        }

    llm = _state["_get_llm"](provider_key)
    t0 = time.time()

    if debug_log is not None:
        debug_log.append({
            "name": "Pass 0b Spatial",
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system": system_prompt,
            "schema": schema,
        })

    try:
        result = llm.complete_structured_json(prompt=prompt, schema=schema, system=system_prompt)
        elapsed = round(time.time() - t0, 1)

        if debug_log is not None:
            debug_log[-1]["output"] = json.dumps(result, indent=2)
            debug_log[-1]["elapsed"] = elapsed

        groups = result.get("groups", [])
        usage = llm.last_usage if llm.last_usage else {}

        save_cached_analysis(cache_key, {"groups": groups, "usage": usage})
        _pkg.save_prompt_io(cache_key, prompt, system_prompt, schema)

        return {
            "groups": groups,
            "elapsed": elapsed,
            "cached": False,
            "usage": usage,
        }

    except Exception as e:
        if debug_log is not None:
            debug_log[-1]["error"] = str(e)
        raise
