"""Pass 0b synthesis: segmentation query and condensed card generation."""

import json
import sys

from llm_config import DEFAULT_LLM

from .core import _state


def run_segmentation_query(
    pass_a_result: dict,
    somatic_summary: dict,
    lang_summary: dict,
    lesson_text: str,
    provider_key: str | None = None,
    debug_log: list | None = None,
    skip_cache: bool = False,
    hint: str = "",
) -> dict:
    """Run the segmentation query to identify pedagogical arcs.

    Returns the segmentation result dict with a 'segments' key.
    """
    import time

    _pkg = sys.modules["analysis"]
    get_cache_key = _pkg.get_cache_key
    load_cached_analysis = _pkg.load_cached_analysis
    save_cached_analysis = _pkg.save_cached_analysis

    input_text = _pkg.format_input_for_segmentation(pass_a_result, somatic_summary, lang_summary)

    prompt_template, schema = _pkg.load_pass_0b_segmentation()

    hint_block = ""
    if hint and hint.strip():
        hint_block = f"\n\n## User Guidance\n{hint.strip()}\n"

    prompt = prompt_template + hint_block + input_text

    system_prompt = (
        "You are an expert Feldenkrais practitioner identifying pedagogical arcs in a lesson. "
        "Most lessons are a single arc. Only segment when there is a genuine shift in organizing principle. "
        "HARD CONSTRAINTS: Maximum 2 segments. The first segment must include at least the first four groups. "
        "Never split the early exploratory phase of a lesson into its own segment."
    )

    cache_suffix = ":segmentation_v2"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    hint_suffix = f":hint:{hint.strip()}" if hint and hint.strip() else ""
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix + hint_suffix)
    if not skip_cache:
        cached = load_cached_analysis(cache_key)
        if cached:
            return {"segments": cached.get("segments", []), "cached": True}

    llm = _state["_get_llm"](provider_key)
    t0 = time.time()

    if debug_log is not None:
        debug_log.append({
            "name": "Segmentation",
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

        segments = result.get("segments", [])
        usage = llm.last_usage if llm.last_usage else {}

        save_cached_analysis(cache_key, {"segments": segments, "usage": usage})
        _pkg.save_prompt_io(cache_key, prompt, system_prompt, schema)

        return {"segments": segments, "elapsed": elapsed, "cached": False, "usage": usage}

    except Exception as e:
        if debug_log is not None:
            debug_log[-1]["error"] = str(e)
        raise


def _run_condensed_for_groups(
    pass_a_result: dict,
    somatic_enriched: list,
    somatic_summary: dict,
    lang_enriched: list,
    lang_summary: dict,
    group_filter: set | None,
    provider_key: str | None,
    debug_log: list | None,
    segment_context: dict | None = None,
    hint: str = "",
) -> dict:
    """Run a single condensed synthesis call, optionally filtered to specific groups.

    segment_context, if provided, contains:
        - segment_number: int
        - theme: str
        - total_segments: int
        - all_segments: list of {segment_number, theme, groups}

    Returns raw LLM result dict with group_cards and setting.
    """
    _pkg = sys.modules["analysis"]

    input_text = _pkg.format_input_for_0b_condensed(
        pass_a_result, somatic_enriched, somatic_summary,
        lang_enriched, lang_summary, group_filter=group_filter,
    )

    prompt_template, schema = _pkg.load_pass_0b_condensed()

    segment_preamble = ""
    if segment_context:
        seg_num = segment_context["segment_number"]
        seg_theme = segment_context["theme"]
        all_segs = segment_context["all_segments"]

        seg_overview = "\n".join(
            f"  - Segment {s['segment_number']}: Groups {s['groups']} — {s['theme']}"
            for s in all_segs
        )

        segment_template_path = _state["_data_dir"] / "pass_0b_condensed_segment.md"
        segment_preamble = segment_template_path.read_text() if segment_template_path.exists() else ""
        segment_preamble += (
            f"You are synthesizing **Segment {seg_num}: {seg_theme}**.\n\n"
            f"{seg_overview}\n\n"
        )

        prompt = prompt_template + segment_preamble + input_text
    else:
        prompt = prompt_template + input_text

    if hint and hint.strip():
        hint_block = f"\n\n## User Guidance\n{hint.strip()}\n"
        prompt = prompt_template + segment_preamble + hint_block + input_text

    system_prompt = (
        "You are an expert Feldenkrais practitioner creating condensed lesson cards. "
        "Preserve all five points of view. Generate group cards first, then the setting card."
    )

    if debug_log is not None:
        label = "Pass 0b Condensed"
        if group_filter:
            label += f" (groups {sorted(group_filter)})"
        debug_log.append({
            "name": label,
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system": system_prompt,
            "schema": schema,
        })

    llm = _state["_get_llm"](provider_key)

    try:
        result = llm.complete_structured_json(prompt=prompt, schema=schema, system=system_prompt)

        if debug_log is not None:
            debug_log[-1]["output"] = json.dumps(result, indent=2)

        usage = llm.last_usage if llm.last_usage else {}
        return {
            "group_cards": result.get("group_cards", []),
            "setting": result.get("setting", {}),
            "usage": usage,
            "_prompt": prompt,
            "_system": system_prompt,
            "_schema": schema,
        }

    except Exception as e:
        if debug_log is not None:
            debug_log[-1]["error"] = str(e)
        raise


def run_pass_0b_condensed(
    somatic_result: dict,
    lang_result: dict,
    lesson_text: str,
    provider_key: str | None = None,
    debug_log: list | None = None,
    skip_cache: bool = False,
    hint: str = "",
) -> dict:
    """
    Pass 0b Condensed: Generate lesson cards from prior analyses.

    Two-phase approach:
    1. Segmentation query identifies pedagogical arcs
    2. Per-segment condensed synthesis (one call per segment)
    """
    import time

    _pkg = sys.modules["analysis"]
    get_cache_key = _pkg.get_cache_key
    load_cached_analysis = _pkg.load_cached_analysis
    save_cached_analysis = _pkg.save_cached_analysis

    pass_a_result = somatic_result.get("pass_a", {})
    somatic_enriched = somatic_result.get("enriched_steps", [])
    somatic_summary = somatic_result.get("summary", {})
    lang_enriched = lang_result.get("enriched_steps", [])
    lang_summary = lang_result.get("summary", {})

    cache_suffix = ":condense_v4"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    hint_suffix = f":hint:{hint.strip()}" if hint and hint.strip() else ""
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix + hint_suffix)
    if not skip_cache:
        cached = load_cached_analysis(cache_key)
        if cached:
            return {
                "segments": cached.get("segments", []),
                "cached": True,
            }

    t0 = time.time()

    seg_result = run_segmentation_query(
        pass_a_result, somatic_summary, lang_summary,
        lesson_text, provider_key, debug_log, skip_cache=skip_cache,
        hint=hint,
    )
    segments_meta = seg_result.get("segments", [])

    output_segments = []

    if len(segments_meta) <= 1:
        condensed = _run_condensed_for_groups(
            pass_a_result, somatic_enriched, somatic_summary,
            lang_enriched, lang_summary,
            group_filter=None,
            provider_key=provider_key,
            debug_log=debug_log,
            hint=hint,
        )
        seg_meta = segments_meta[0] if segments_meta else {}
        output_segments.append({
            "segment_number": seg_meta.get("segment_number", 1),
            "theme": seg_meta.get("theme", ""),
            "transition_note": seg_meta.get("transition_note"),
            "group_cards": condensed["group_cards"],
            "setting": condensed["setting"],
        })
    else:
        all_segs_summary = [
            {"segment_number": s.get("segment_number", i+1), "theme": s.get("theme", ""), "groups": s.get("groups", [])}
            for i, s in enumerate(segments_meta)
        ]
        for seg_meta in segments_meta:
            group_nums = set(seg_meta.get("groups", []))
            seg_ctx = {
                "segment_number": seg_meta.get("segment_number", 1),
                "theme": seg_meta.get("theme", ""),
                "total_segments": len(segments_meta),
                "all_segments": all_segs_summary,
            }
            condensed = _run_condensed_for_groups(
                pass_a_result, somatic_enriched, somatic_summary,
                lang_enriched, lang_summary,
                group_filter=group_nums,
                provider_key=provider_key,
                debug_log=debug_log,
                segment_context=seg_ctx,
                hint=hint,
            )
            output_segments.append({
                "segment_number": seg_meta.get("segment_number", 1),
                "theme": seg_meta.get("theme", ""),
                "transition_note": seg_meta.get("transition_note"),
                "group_cards": condensed["group_cards"],
                "setting": condensed["setting"],
            })

    elapsed = round(time.time() - t0, 1)
    save_cached_analysis(cache_key, {"segments": output_segments})

    # Save prompt IO from the condensed calls (use first call's prompt as representative)
    if output_segments:
        # Collect prompts from _run_condensed_for_groups via the _prompt/_system/_schema keys
        # These were added to the condensed return dict above
        condensed_prompts = []
        # Re-retrieve from the last condensed call — we need to capture during the loop
        # For now, save the last prompt available from the condensed variable
        if condensed and condensed.get("_prompt"):
            _pkg.save_prompt_io(cache_key, condensed["_prompt"], condensed.get("_system", ""), condensed.get("_schema"))

    return {
        "segments": output_segments,
        "elapsed": elapsed,
        "cached": False,
    }
