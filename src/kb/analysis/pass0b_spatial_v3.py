"""Pass 0b Spatial v3: Step-level position sequence extraction."""

from .core import _resolve_data_path

import json
import sys

from llm_config import DEFAULT_LLM

from .core import _state


def _format_input_for_spatial_v3(pass_a_result: dict) -> str:
    """Build LLM input from Pass A output for step-level position extraction.

    Includes group labels/descriptions and step instructions with original
    segment text for maximum position detail.
    """
    pass_0 = pass_a_result.get("pass_0", {})
    segments = pass_0.get("segments", [])
    seg_lookup = {s["id"]: s["text"] for s in segments}

    groups = pass_a_result.get("groups", [])
    steps = pass_a_result.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps}

    # Map steps to groups
    step_group = {}
    for g in groups:
        for sn in g.get("steps", []):
            step_group[sn] = g.get("group_number", 0)

    lines = []
    for g in groups:
        gnum = g.get("group_number", 0)
        label = g.get("label", "")
        desc = g.get("description", "")
        sp = g.get("starting_position", "")
        lines.append(f"## Group {gnum}: {label}")
        if desc:
            lines.append(desc)
        if sp:
            lines.append(f"Starting position: {sp}")
        lines.append("")

        for sn in g.get("steps", []):
            step = step_lookup.get(sn)
            if not step:
                continue
            instruction = step.get("instruction", "")
            tags = step.get("tags", [])
            is_home = step.get("is_home", False)
            lines.append(f"### Step {sn} [{','.join(tags)}]{' [HOME]' if is_home else ''}")
            lines.append(f"Instruction: {instruction}")

            # Include original segment text for position detail
            seg_ids = step.get("segments", [])
            for sid in seg_ids[:5]:  # limit to first 5 segments per step
                text = seg_lookup.get(sid, "")
                if text:
                    lines.append(f"[{sid}] {text}")
            lines.append("")

    return "\n".join(lines)


def load_pass_0b_spatial_v3() -> tuple[str, dict]:
    """Load spatial v3 prompt and schema."""
    prompt_path = _resolve_data_path("pass_0b_spatial_v3.md")
    schema_path = _resolve_data_path("pass_0b_spatial_v3.schema.json")
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def run_pass_0b_spatial_v3(
    lesson_text: str,
    provider_key: str | None = None,
    debug_log: list | None = None,
) -> dict:
    """
    Pass 0b Spatial v3: Step-level position sequence extraction.

    Runs Pass A (cached), then extracts a plain-language position
    for each step — designed for natural language search.
    """
    import time

    _pkg = sys.modules["analysis"]
    get_cache_key = _pkg.get_cache_key
    load_cached_analysis = _pkg.load_cached_analysis
    save_cached_analysis = _pkg.save_cached_analysis

    # Run Pass A first (has its own cache)
    pass_a_result = _pkg.run_pass_a(lesson_text, provider_key, debug_log)

    # Build input
    spatial_input = _format_input_for_spatial_v3(pass_a_result)

    prompt_template, schema = load_pass_0b_spatial_v3()
    prompt = prompt_template + "\n\n---\n\n## Lesson Analysis:\n\n" + spatial_input

    system_prompt = (
        "You are extracting body positions from a Feldenkrais lesson. "
        "Describe each position in plain language that a student would use. "
        "Be specific about which limbs, which sides, and what touches what."
    )

    # Check cache
    cache_suffix = ":spatial_v3"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix)
    cached = load_cached_analysis(cache_key)
    if cached:
        return {
            "steps": cached.get("steps", []),
            "position_summary": cached.get("position_summary", []),
            "cached": True,
        }

    llm = _state["_get_llm"](provider_key)
    t0 = time.time()

    if debug_log is not None:
        debug_log.append({
            "name": "Pass 0b Spatial v3",
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

        steps = result.get("steps", [])
        position_summary = result.get("position_summary", [])
        usage = llm.last_usage if llm.last_usage else {}

        save_cached_analysis(cache_key, {
            "steps": steps,
            "position_summary": position_summary,
            "usage": usage,
        })
        _pkg.save_prompt_io(cache_key, prompt, system_prompt, schema)

        return {
            "steps": steps,
            "position_summary": position_summary,
            "elapsed": elapsed,
            "cached": False,
            "usage": usage,
        }

    except Exception as e:
        if debug_log is not None:
            debug_log[-1]["error"] = str(e)
        raise
