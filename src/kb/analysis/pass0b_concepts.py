"""Pass 0b Concepts: Lesson-level concept tagging for search."""

import json
import sys

from llm_config import DEFAULT_LLM

from .core import _state


def run_pass_0b_concepts(
    lesson_text: str,
    provider_key: str | None = None,
    debug_log: list | None = None,
) -> dict:
    """
    Pass 0b Concepts: Lesson-level concept tagging for search.

    Runs Pass A + Pass 0b somatic first, then extracts concepts, distinctive
    summary, and search terms.
    """
    import time

    _pkg = sys.modules["analysis"]
    get_cache_key = _pkg.get_cache_key
    load_cached_analysis = _pkg.load_cached_analysis
    save_cached_analysis = _pkg.save_cached_analysis

    somatic_result = _pkg.run_pass_0b(lesson_text, provider_key, debug_log)
    pass_a_result = somatic_result["pass_a"]
    somatic_steps = somatic_result.get("enriched_steps", [])

    concept_input = _pkg.format_input_for_concepts(pass_a_result, somatic_steps)

    prompt_template, schema = _pkg.load_pass_0b_concepts()
    prompt = prompt_template + concept_input

    system_prompt = (
        "You are an expert Feldenkrais practitioner tagging a lesson with "
        "searchable concepts. Be specific and distinctive — avoid generic terms "
        "that apply to every lesson."
    )

    cache_suffix = ":concept_tags_v2"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix)
    cached = load_cached_analysis(cache_key)
    if cached:
        return {
            "concepts": cached.get("concepts", []),
            "distinctive_summary": cached.get("distinctive_summary", ""),
            "search_terms": cached.get("search_terms", []),
            "cached": True,
        }

    llm = _state["_get_llm"](provider_key)
    t0 = time.time()

    if debug_log is not None:
        debug_log.append({
            "name": "Pass 0b Concepts",
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

        concepts = result.get("concepts", [])
        distinctive_summary = result.get("distinctive_summary", "")
        search_terms = result.get("search_terms", [])
        usage = llm.last_usage if llm.last_usage else {}

        save_cached_analysis(cache_key, {
            "concepts": concepts,
            "distinctive_summary": distinctive_summary,
            "search_terms": search_terms,
            "usage": usage,
        })
        _pkg.save_prompt_io(cache_key, prompt, system_prompt, schema)

        return {
            "concepts": concepts,
            "distinctive_summary": distinctive_summary,
            "search_terms": search_terms,
            "elapsed": elapsed,
            "cached": False,
            "usage": usage,
        }

    except Exception as e:
        if debug_log is not None:
            debug_log[-1]["error"] = str(e)
        raise
