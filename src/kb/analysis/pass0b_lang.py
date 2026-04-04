"""Pass 0b language analysis: step-level and word-level."""

import json
import sys
from collections import defaultdict

from llm_config import DEFAULT_LLM

from .core import _state


def run_pass_0b_lang(lesson_text: str, provider_key: str | None = None, debug_log: list | None = None) -> dict:
    """
    Pass 0b-lang: Step-level language analysis.

    Runs Pass A to decompose the lesson into steps/groups, then sends
    the step-level input to an LLM for language analysis.
    """
    import time

    _pkg = sys.modules["analysis"]
    get_cache_key = _pkg.get_cache_key
    load_cached_analysis = _pkg.load_cached_analysis
    save_cached_analysis = _pkg.save_cached_analysis

    # Run Pass A first (has its own cache)
    pass_a_result = _pkg.run_pass_a(lesson_text, provider_key, debug_log)
    steps_text = _pkg.format_steps_for_0b(pass_a_result)

    # Load prompt and schema
    prompt_template, schema = _pkg.load_pass_0b_lang()

    prompt = prompt_template + steps_text

    system_prompt = (
        "You are an expert in Feldenkrais pedagogy and language analysis. "
        "Analyze the teacher's language choices at each step. "
        "Process every step. Do not skip, merge, or split steps."
    )

    # Check cache
    cache_suffix = ":language_v1"
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

    llm = _state["_get_llm"](provider_key)
    t0 = time.time()

    if debug_log is not None:
        debug_log.append({
            "name": "Pass 0b-lang",
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

        enriched = result.get("steps", [])
        summary = result.get("summary", {})

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
            logging.warning(f"Pass 0b-lang truncation: {truncation_warning}")

        usage = llm.last_usage if llm.last_usage else {}
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


def run_pass_0b_word_lang(lesson_text: str, provider_key: str | None = None, debug_log: list | None = None) -> dict:
    """
    Pass 0b word-level language annotation.

    Runs Pass A (cached) -> runs pass_0b_lang (cached) -> formats combined input ->
    calls LLM with word-level annotation prompt.
    """
    import time

    _pkg = sys.modules["analysis"]
    get_cache_key = _pkg.get_cache_key
    load_cached_analysis = _pkg.load_cached_analysis
    save_cached_analysis = _pkg.save_cached_analysis

    pass_a_result = _pkg.run_pass_a(lesson_text, provider_key, debug_log)

    lang_result = _pkg.run_pass_0b_lang(lesson_text, provider_key, debug_log)
    lang_enriched = lang_result.get("enriched_steps", [])

    steps_text = _pkg.format_steps_for_word_lang(pass_a_result, lang_enriched)

    prompt_template, schema = _pkg.load_pass_0b_word_lang()
    prompt = prompt_template + steps_text

    system_prompt = (
        "You are an expert in Feldenkrais pedagogy and language analysis. "
        "Annotate the teacher's language at the word/phrase level. "
        "Process every step. Do not skip, merge, or split steps."
    )

    cache_suffix = ":language_patterns_v3"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix)
    cached = load_cached_analysis(cache_key)
    if cached:
        return {
            "pass_a": pass_a_result,
            "word_annotations": cached.get("word_annotations", []),
            "cached": True,
        }

    llm = _state["_get_llm"](provider_key)
    t0 = time.time()

    if debug_log is not None:
        debug_log.append({
            "name": "Pass 0b Word-Lang",
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

        flat_annotations = result.get("annotations", [])
        by_step = defaultdict(list)
        for ann in flat_annotations:
            sn = ann.get("step_number")
            if sn is not None:
                by_step[sn].append({
                    "text": ann.get("text", ""),
                    "category": ann.get("category", ""),
                    "note": ann.get("note", ""),
                })
        word_annotations = [
            {"step_number": sn, "phrases": phrases}
            for sn, phrases in sorted(by_step.items())
        ]

        truncation_warning = None
        usage = llm.last_usage if llm.last_usage else {}
        save_cached_analysis(cache_key, {"word_annotations": word_annotations, "usage": usage})
        _pkg.save_prompt_io(cache_key, prompt, system_prompt, schema)

        return {
            "pass_a": pass_a_result,
            "word_annotations": word_annotations,
            "elapsed": elapsed,
            "cached": False,
            "truncation_warning": truncation_warning,
            "usage": usage,
        }

    except Exception as e:
        if debug_log is not None:
            debug_log[-1]["error"] = str(e)
        raise
