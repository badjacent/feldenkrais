"""Pass 0b: Step-level somatic feature extraction."""

import json
import sys

from llm_config import DEFAULT_LLM

from .core import _state


def run_pass_0b(lesson_text: str, provider_key: str | None = None, debug_log: list | None = None) -> dict:
    """
    Pass 0b: Step-level somatic feature extraction.

    Runs Pass A to decompose the lesson into steps/groups, then sends
    the step-level input to an LLM for enrichment.

    Returns:
        {
            "pass_a": { ... pass A result ... },
            "enriched_steps": [ { step_number, group_number, clarified, ... }, ... ],
            "elapsed": float,
            "cached": bool,
        }
    """
    import time

    # Late-bind all sibling functions through the package so test patches work
    _pkg = sys.modules["analysis"]
    run_pass_a = _pkg.run_pass_a
    format_steps_for_0b = _pkg.format_steps_for_0b
    load_pass_0b = _pkg.load_pass_0b
    get_cache_key = _pkg.get_cache_key
    load_cached_analysis = _pkg.load_cached_analysis
    save_cached_analysis = _pkg.save_cached_analysis

    # Run Pass A first (has its own cache)
    pass_a_result = run_pass_a(lesson_text, provider_key, debug_log)
    steps_text = format_steps_for_0b(pass_a_result)

    # Load prompt and schema
    prompt_template, schema = load_pass_0b()

    # Build the full prompt
    prompt = prompt_template + steps_text

    system_prompt = (
        "You are an expert Feldenkrais practitioner enriching lesson steps. "
        "Process every step. Do not skip, merge, or split steps."
    )

    # Check cache (v2 suffix breaks from old segment-level cache)
    cache_suffix = ":somatic_v2"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix)
    cached = load_cached_analysis(cache_key)
    if cached:
        return {
            "pass_a": pass_a_result,
            "enriched_steps": cached.get("enriched_steps", []),
            "summary": cached.get("summary", {}),
            "cached": True,
        }

    # Run LLM
    llm = _state["_get_llm"](provider_key)
    t0 = time.time()

    if debug_log is not None:
        debug_log.append({
            "name": "Pass 0b",
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

        enriched = result.get("steps", [])
        summary = result.get("summary", {})

        # Validate step count -- LLM may silently truncate large outputs
        expected_count = len(pass_a_result.get("steps", []))
        actual_count = len(enriched)
        truncation_warning = None
        if actual_count < expected_count:
            truncation_warning = (
                f"LLM returned {actual_count} of {expected_count} steps "
                f"(missing steps {actual_count + 1}-{expected_count}). "
                f"Output was likely truncated due to token limits."
            )
            import logging
            logging.warning(f"Pass 0b truncation: {truncation_warning}")

        # Capture token usage
        usage = llm.last_usage if llm.last_usage else {}

        # Cache the result
        save_cached_analysis(cache_key, {"enriched_steps": enriched, "summary": summary, "usage": usage})
        _pkg.save_prompt_io(cache_key, prompt, system_prompt, schema)

        return {
            "pass_a": pass_a_result,
            "enriched_steps": enriched,
            "summary": summary,
            "elapsed": elapsed,
            "cached": False,
            "truncation_warning": truncation_warning,
            "usage": usage,
        }

    except Exception as e:
        if debug_log is not None:
            debug_log[-1]["error"] = str(e)
        raise
