"""Print preparation: the shared model behind the Lesson Step Table and the
Annotated Step Transcript (docs/LESSON_PRINT_FORMATS_SPEC.md).

The decomposition fixes groups, steps and each step's segments; spatial v3
supplies each step's position. The LLM writes only a short print position, a
brief summary, and verbatim quote choices. Everything it returns is checked
against the stored source before it is cached: steps must match the
decomposition exactly, and every quote must be an exact piece of a segment
assigned to its own step, stored as segment ID and character offsets. The full
transcript is never regenerated; renderers rebuild it from the segments.
"""

import hashlib
import json
import sys

from llm_config import DEFAULT_LLM, effective_llm_key

from .core import _state

SCHEMA_VERSION = 1
CACHE_SUFFIX = ":print_prep_v1"
MAX_QUOTES = 7

SYSTEM_PROMPT = (
    "You prepare Feldenkrais lessons for print. Keep the lesson's structure "
    "exactly, write plain actionable instructions, and quote the teacher only "
    "with exact, verbatim excerpts."
)


class PrintPrepError(ValueError):
    """The LLM's answer does not fit the stored lesson structure."""


def load_print_prep() -> tuple[str, dict]:
    """Load the print preparation prompt and schema."""
    prompt_path = _state["_data_dir"] / "print_prep.md"
    schema_path = _state["_data_dir"] / "print_prep.schema.json"
    return prompt_path.read_text(), json.loads(schema_path.read_text())


def _provider_suffix(provider_key: str | None) -> str:
    return f":{provider_key}" if provider_key and provider_key != DEFAULT_LLM else ""


def _complete_structure(pass_a: dict) -> dict:
    """Give every step a unique number and a group, without touching the cache.

    Decompose sometimes leaves a step out of every group, or numbers two
    different steps alike. Such a step takes the next free number if needed and
    joins the group of the step before it; opening steps join the first group.
    Idempotent, so the prompt and the merge see the same structure.
    """
    steps = [dict(s) for s in pass_a.get("steps", [])]
    groups = [{**g, "steps": list(g.get("steps", []))} for g in pass_a.get("groups", [])]
    if steps and not groups:
        groups = [{"group_number": 1, "label": "", "steps": []}]

    seen: set = set()
    next_number = max((s["step_number"] for s in steps), default=0) + 1
    for step in steps:
        if step["step_number"] in seen:
            step["step_number"] = next_number
            next_number += 1
        seen.add(step["step_number"])
    for group in groups:
        group["steps"] = [n for n in group["steps"] if n in seen]

    placed = {n for g in groups for n in g["steps"]}
    previous = None
    opening = 0
    for step in steps:
        number = step["step_number"]
        if number not in placed:
            if previous is None:
                groups[0]["steps"].insert(opening, number)
                opening += 1
            else:
                group = next(g for g in groups if previous in g["steps"])
                group["steps"].insert(group["steps"].index(previous) + 1, number)
            placed.add(number)
        previous = number
    return {**pass_a, "steps": steps, "groups": groups}


def _format_input(pass_a: dict, spatial_v3: dict) -> str:
    """Groups and steps with their positions and every assigned segment."""
    original = [s["step_number"] for s in pass_a.get("steps", [])]
    # A shared number cannot say which of its steps the spatial entry describes.
    ambiguous = {n for n in original if original.count(n) > 1}
    pass_a = _complete_structure(pass_a)
    seg_text = {s["id"]: s.get("text", "") for s in pass_a.get("pass_0", {}).get("segments", [])}
    steps = {s["step_number"]: s for s in pass_a.get("steps", [])}
    positions = {s.get("step_number"): s for s in spatial_v3.get("steps", [])
                 if s.get("step_number") not in ambiguous}

    lines = []
    for group in pass_a.get("groups", []):
        lines.append(f"## Group {group.get('group_number')}: {group.get('label', '')}")
        if group.get("description"):
            lines.append(group["description"])
        lines.append("")
        for number in group.get("steps", []):
            step = steps.get(number)
            if not step:
                continue
            pos = positions.get(number, {})
            lines.append(f"### Step {number}{' [rest/home]' if step.get('is_home') else ''}")
            lines.append(f"Instruction: {step.get('instruction', '')}")
            lines.append(f"Position: {pos.get('position', '') or '(not described)'}")
            if pos.get("action"):
                lines.append(f"Action: {pos['action']}")
            for sid in step.get("segments", []):
                lines.append(f"[{sid}] {seg_text.get(sid, '')}")
            lines.append("")
    return "\n".join(lines)


def _coverage_errors(pass_a: dict) -> list[str]:
    """Data-quality problems that make a full-transcript export untrue."""
    all_ids = [s["id"] for s in pass_a.get("pass_0", {}).get("segments", [])]
    assigned: dict[int, int] = {}
    errors = []
    for step in pass_a.get("steps", []):
        for sid in step.get("segments", []):
            if sid in assigned:
                errors.append(f"segment {sid} is assigned to steps {assigned[sid]} and {step['step_number']}")
            assigned[sid] = step["step_number"]
    missing = [sid for sid in all_ids if sid not in assigned]
    if missing:
        errors.append(f"{len(missing)} segments belong to no step (first: {missing[:5]})")
    unknown = sorted(set(assigned) - set(all_ids))
    if unknown:
        errors.append(f"steps cite segments that do not exist: {unknown[:5]}")
    return errors


def build_print_model(pass_a: dict, llm_result: dict) -> dict:
    """Merge the LLM's answer onto the decomposition and validate it.

    Raises PrintPrepError when the steps do not match the decomposition. Quotes
    that are not exact pieces of their own step's segments are dropped and
    reported under ``validation``; they never reach the model.
    """
    pass_a = _complete_structure(pass_a)
    seg_text = {s["id"]: s.get("text", "") for s in pass_a.get("pass_0", {}).get("segments", [])}
    source_steps = pass_a.get("steps", [])
    expected = [s["step_number"] for s in source_steps]
    answered = [s.get("step_number") for s in llm_result.get("steps", [])]
    if sorted(answered) != sorted(expected) or len(set(answered)) != len(answered):
        missing = sorted(set(expected) - set(answered))
        extra = sorted(set(answered) - set(expected))
        raise PrintPrepError(f"steps do not match the decomposition (missing {missing}, extra {extra}, "
                             f"{len(answered)} returned for {len(expected)})")
    by_number = {s["step_number"]: s for s in llm_result["steps"]}

    group_of = {}
    groups = []
    for group in pass_a.get("groups", []):
        numbers = list(group.get("steps", []))
        for number in numbers:
            group_of[number] = group.get("group_number")
        groups.append({"group_number": group.get("group_number"), "label": group.get("label", ""),
                       "step_numbers": numbers})

    rejected = []
    steps = []
    for source in source_steps:
        number = source["step_number"]
        answer = by_number[number]
        own_segments = list(source.get("segments", []))
        quotes, spans = [], []
        for quote in answer.get("notable_quotes", []):
            sid, text = quote.get("segment_id"), quote.get("text") or ""
            if sid not in own_segments:
                rejected.append({"step": number, "segment_id": sid, "reason": "segment not in this step"})
                continue
            start = seg_text.get(sid, "").find(text) if text.strip() else -1
            if start < 0:
                rejected.append({"step": number, "segment_id": sid, "reason": "not an exact excerpt",
                                 "text": text[:200]})
                continue
            end = start + len(text)
            if any(s == sid and start < e and b < end for s, b, e in spans):
                rejected.append({"step": number, "segment_id": sid, "reason": "repeats or overlaps another excerpt"})
                continue
            if len(quotes) == MAX_QUOTES:
                rejected.append({"step": number, "segment_id": sid, "reason": f"more than {MAX_QUOTES} excerpts"})
                continue
            spans.append((sid, start, end))
            quotes.append({"segment_id": sid, "start_offset": start, "end_offset": end})
        steps.append({
            "step_number": number,
            "group_number": group_of.get(number),
            "position": (answer.get("position") or "").strip(),
            "brief_summary": (answer.get("brief_summary") or "").strip(),
            "notable_quotes": quotes,
            "segment_ids": own_segments,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "groups": groups,
        "steps": steps,
        "editorial_notes": [n for n in llm_result.get("editorial_notes", []) if n and n.strip()],
        "validation": {
            "rejected_quotes": rejected,
            "coverage_errors": _coverage_errors(pass_a),
        },
    }


def run_print_prep(lesson_text: str, provider_key: str | None = None) -> dict:
    """Prepare the print model for one lesson under one provider (cached)."""
    _pkg = sys.modules["analysis"]
    text = lesson_text.strip()
    suffix = _provider_suffix(provider_key)
    cache_key = _pkg.get_cache_key(text + CACHE_SUFFIX + suffix)
    cached = _pkg.load_cached_analysis(cache_key)
    if cached:
        return {**cached, "cached": True}

    # Both are declared DAG dependencies, so they are cached by now.
    pass_a = _pkg.run_pass_a(lesson_text, provider_key)
    spatial_v3 = _pkg.run_pass_0b_spatial_v3(lesson_text, provider_key)

    prompt_template, schema = load_print_prep()
    prompt = prompt_template + "\n\n---\n\n## Lesson\n\n" + _format_input(pass_a, spatial_v3)
    llm = _state["_get_llm"](provider_key)
    result = llm.complete_structured_json(prompt=prompt, schema=schema, system=SYSTEM_PROMPT)

    model = build_print_model(pass_a, result)
    model["source"] = {
        "transcript_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "decompose": ":decompose_v3",
        "spatial": ":spatial_v3",
        "prompt_sha256": hashlib.sha256((prompt_template + json.dumps(schema, sort_keys=True)).encode()).hexdigest()[:16],
        "provider": provider_key or DEFAULT_LLM,
        "llm": effective_llm_key(provider_key),
    }
    model["usage"] = llm.last_usage or {}
    _pkg.save_cached_analysis(cache_key, model)
    _pkg.save_prompt_io(cache_key, prompt, SYSTEM_PROMPT, schema)
    return {**model, "cached": False}
