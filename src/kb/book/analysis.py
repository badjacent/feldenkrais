"""Framework loading, Pass B analysis, and related analysis functions."""

import json

from llm_config import DEFAULT_LLM, get_llm
from analysis import (
    run_pass_a, load_frameworks, get_active_frameworks_config,
    _derive_key, _resolve_prompts_dir, _framework_version_suffix,
)
from cache_helpers import get_cache_key, load_cached_analysis, save_cached_analysis

from book.state import (
    _db, DATA_DIR, FRAMEWORK_VERSION,
    save_job, load_job,
)


def get_available_framework_versions() -> list[str]:
    """List available framework versions by scanning the frameworks directory."""
    from book.state import DATA_DIR, FRAMEWORK_VERSION
    fw_dir = DATA_DIR / "frameworks"
    if not fw_dir.exists():
        return [FRAMEWORK_VERSION]
    return sorted(d.name for d in fw_dir.iterdir() if d.is_dir() and (d / "frameworks.json").exists())


_CORE_VERB_TO_SUBTITLE = {
    "settle": "The Lens of Settling",
    "discover": "The Lens of Discovery",
    "clear": "The Lens of Clarity",
    "transmit": "The Lens of Transmission",
    "permit": "The Lens of Permission",
}


def _derive_subtitle(essence: dict) -> str:
    """Derive subtitle from essence.core_verb."""
    verb = essence.get("core_verb", "")
    return _CORE_VERB_TO_SUBTITLE.get(verb, f"The Lens of {verb.title()}" if verb else "")


def _derive_person_description(essence: dict) -> str:
    """Build person_description from essence.sees (first 1-2 sentences) + changes_when (first sentence)."""
    sees = essence.get("sees", "")
    changes = essence.get("changes_when", "")
    # Take first two sentences from sees
    sees_sentences = [s.strip() for s in sees.split(".") if s.strip()]
    sees_part = ". ".join(sees_sentences[:2]) + "." if sees_sentences else ""
    # Take first sentence from changes_when
    changes_sentences = [s.strip() for s in changes.split(".") if s.strip()]
    changes_part = changes_sentences[0] + "." if changes_sentences else ""
    parts = [p for p in [sees_part, changes_part] if p]
    return " ".join(parts)


def _format_character_as_prompt(char: dict) -> str:
    """Format a character definition (essence + voice) as a structured markdown prompt.

    Used by load_pass_b() to replace the old vl{N}.md files.
    """
    name = char.get("name", "")
    essence = char.get("essence", {})
    voice = char.get("voice", {})

    lines = [f"# {name}\n"]

    lines.append("## What This Lens Sees\n")
    lines.append(essence.get("sees", ""))
    lines.append("")

    lines.append("## What It Regulates\n")
    lines.append(essence.get("regulates", ""))
    lines.append("")

    lines.append("## When It Changes\n")
    lines.append(essence.get("changes_when", ""))
    lines.append("")

    core_verb = essence.get("core_verb", "")
    core_dist = essence.get("core_distinction", "")
    if core_verb or core_dist:
        lines.append(f"## Core Verb: {core_verb}\n")
        lines.append(core_dist)
        lines.append("")

    lines.append("## Voice\n")
    lines.append(f"**Register:** {voice.get('private_register', '')}\n")
    lines.append(f"**Sentence quality:** {voice.get('sentence_quality', '')}\n")
    lines.append(f"**Emotional range:** {voice.get('emotional_range', '')}\n")
    lines.append(f"**Metaphor family:** {voice.get('metaphor_family', '')}\n")

    seeds = voice.get("vocabulary_seeds", [])
    if seeds:
        lines.append(f"**Vocabulary seeds:** {', '.join(seeds)}\n")

    return "\n".join(lines)


# Cache for VL booklet intros built from frameworks
_vl_booklet_intros_cache: dict[str, dict] = {}


def get_vl_booklet_intros(framework_version: str | None = None) -> dict[str, dict]:
    """Build VL_BOOKLET_INTROS dict from frameworks.json (cached).

    For the new character-definitions schema, backward-compatible fields
    are derived from essence/voice data with a _derived suffix.
    """
    from book.state import FRAMEWORK_VERSION
    v = framework_version or FRAMEWORK_VERSION
    if v not in _vl_booklet_intros_cache:
        chars = load_frameworks(v)
        intros = {}

        # Detect new schema by checking for 'role' field
        is_new_schema = chars and "role" in chars[0]

        if is_new_schema:
            blob = next((c for c in chars if c.get("role") == "shadow"), {})
            for f in chars:
                if f.get("role") == "shadow":
                    continue
                essence = f.get("essence", {})
                intros[f["id"]] = {
                    "name": f["name"],
                    "subtitle_derived": _derive_subtitle(essence),
                    "lens_derived": essence.get("sees", "").split(".")[0] + "." if essence.get("sees") else "",
                    "person_inside": f["name"],
                    "person_description_derived": _derive_person_description(essence),
                    "habit_label_derived": blob.get("name", "The Blob"),
                    "habit_description_derived": blob.get("essence", {}).get("sees", ""),
                }
        else:
            for f in chars:
                intros[f["id"]] = {
                    "name": f.get("name", ""),
                    "subtitle_derived": f.get("subtitle", ""),
                    "lens_derived": f.get("lens", ""),
                    "person_inside": f.get("person_inside", ""),
                    "person_description_derived": f.get("person_description", ""),
                    "habit_description_derived": f.get("habit_description", ""),
                    "habit_label_derived": f.get("habit_label", ""),
                }
        _vl_booklet_intros_cache[v] = intros
    return _vl_booklet_intros_cache[v]


ANALYSIS_SYSTEM_PROMPT = """You are an expert Feldenkrais practitioner analyzing lessons using the VL-1 protocol.
Follow the protocol exactly to analyze the lesson provided. Be thorough and specific in your analysis.

Important: The analysis table must cover ALL major movements identified in the lesson, from beginning to end.
This includes the final movements where the student returns to the initial position or rechecks their starting state.
Do not truncate the table early."""


def load_pass_b(framework: str, framework_version: str | None = None) -> tuple[str, dict]:
    """Load Pass B prompt and schema for a specific VL framework.

    Pass B receives Pass A output and applies the VL-specific lens.
    For the new character-definitions schema, the VL-specific module is
    generated from the character's essence + voice data in frameworks.json.
    Prompts come from versioned PROMPTS_DIR, schema from shared DATA_DIR.
    """
    from book.state import DATA_DIR, FRAMEWORK_VERSION
    fv = framework_version or FRAMEWORK_VERSION
    prompts_dir, _ = _resolve_prompts_dir(fv)
    # Load base Pass B protocol
    base_path = prompts_dir / "pass_b.md"
    base_protocol = base_path.read_text() if base_path.exists() else ""

    # Find character definition and generate VL-specific module
    chars = load_frameworks(fv)
    char = next((c for c in chars if c["id"] == framework), None)

    if char and "essence" in char:
        # New schema: format character definition as prompt
        vl_module = _format_character_as_prompt(char)
    elif char and "prompt_file" in char:
        # Legacy schema: load from prompt file
        vl_path = prompts_dir / char["prompt_file"]
        vl_module = vl_path.read_text() if vl_path.exists() else ""
    else:
        vl_module = ""

    # Combine: Pass B base + VL-specific framework
    protocol = base_protocol + "\n\n---\n\n## Framework: " + framework + "\n\n" + vl_module

    # Pass B schema (shared, unversioned)
    schema_path = DATA_DIR / "pass_b.schema.json"
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}

    return protocol, schema


# Legacy function for backwards compatibility
def load_protocol(framework: str = "VL-1") -> tuple[str, dict]:
    """Load protocol - delegates to load_pass_b for backwards compatibility."""
    return load_pass_b(framework)


VL_ANALYSIS_THRESHOLD = 0.5


def run_router(lesson_text: str) -> dict:
    """Alias for run_pass_a for backwards compatibility."""
    return run_pass_a(lesson_text)


def get_active_frameworks(pass_a_result: dict, threshold: float = VL_ANALYSIS_THRESHOLD) -> list[str]:
    """Get list of VL frameworks that scored above the threshold."""
    classifications = pass_a_result.get("classifications", {})

    # Map classification keys to framework names from registry
    key_to_framework = {_derive_key(f): f["id"] for f in get_active_frameworks_config()}

    active = []
    for key, framework in key_to_framework.items():
        score = classifications.get(key, 0)
        if score >= threshold:
            active.append((framework, score))

    # Sort by score descending, return framework names
    active.sort(key=lambda x: -x[1])
    return [f for f, s in active]


def run_pass_b(lesson_text: str, pass_a_result: dict, framework: str, split: bool = False, provider_key: str | None = None, debug_log: list | None = None, framework_version: str | None = None) -> dict:
    """Run Pass B: VL Framework Synthesis.

    Takes the original lesson text AND Pass A output, applies VL-specific lens.
    If split=True, runs as two sequential queries (narrative then table) to
    avoid truncation on long lessons.
    """
    fv = framework_version
    if split:
        return run_pass_b_split(lesson_text, pass_a_result, framework, provider_key=provider_key, debug_log=debug_log, framework_version=fv)

    protocol, schema = load_pass_b(framework, fv)

    # Build prompt with both lesson text and Pass A output
    prompt = protocol + "\n\n---\n\n## Original Lesson Text:\n\n" + lesson_text
    prompt += "\n\n---\n\n## Pass A Analysis:\n\n```json\n" + json.dumps(pass_a_result, indent=2) + "\n```"

    llm = get_llm(provider_key)
    try:
        analysis = llm.complete_structured_json(
            prompt,
            schema,
            system=ANALYSIS_SYSTEM_PROMPT,
        )
    except Exception as e:
        if debug_log is not None:
            # Extract raw output from error message if available
            error_str = str(e)
            raw_output = error_str
            debug_log.append({
                "name": f"Pass B ({framework})",
                "provider": provider_key or DEFAULT_LLM,
                "prompt": prompt,
                "system": ANALYSIS_SYSTEM_PROMPT,
                "schema": schema,
                "error": raw_output,
            })
        raise

    if debug_log is not None:
        debug_log.append({
            "name": f"Pass B ({framework})",
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system": ANALYSIS_SYSTEM_PROMPT,
            "schema": schema,
            "output": analysis,
        })
    return analysis


def run_pass_b_split(lesson_text: str, pass_a_result: dict, framework: str, provider_key: str | None = None, debug_log: list | None = None, framework_version: str | None = None) -> dict:
    """Run Pass B as two sequential queries: narrative first, then table.

    The narrative (negotiation framework + somatic narrative) is generated first,
    then fed as context to the table query. This prevents truncation on long
    lessons and gives the table an interpretive anchor.
    """
    from book.state import DATA_DIR
    protocol, _ = load_pass_b(framework, framework_version)

    base_prompt = protocol + "\n\n---\n\n## Original Lesson Text:\n\n" + lesson_text
    base_prompt += "\n\n---\n\n## Pass A Analysis:\n\n```json\n" + json.dumps(pass_a_result, indent=2) + "\n```"

    llm = get_llm(provider_key)
    prov = provider_key or DEFAULT_LLM

    # Query 1: Narrative (negotiation framework + somatic narrative)
    narrative_schema = json.loads((DATA_DIR / "pass_b_narrative.schema.json").read_text())
    narrative_prompt = base_prompt + "\n\n---\n\nFor this query, provide ONLY the negotiation framework observations and somatic narrative. Do NOT include the analytical table."
    try:
        narrative_result = llm.complete_structured_json(
            narrative_prompt,
            narrative_schema,
            system=ANALYSIS_SYSTEM_PROMPT,
        )
    except Exception as e:
        if debug_log is not None:
            debug_log.append({
                "name": f"Pass B Narrative ({framework})",
                "provider": prov,
                "prompt": narrative_prompt,
                "system": ANALYSIS_SYSTEM_PROMPT,
                "schema": narrative_schema,
                "error": str(e),
            })
        raise

    if debug_log is not None:
        debug_log.append({
            "name": f"Pass B Narrative ({framework})",
            "provider": prov,
            "prompt": narrative_prompt,
            "system": ANALYSIS_SYSTEM_PROMPT,
            "schema": narrative_schema,
            "output": narrative_result,
        })

    # Query 2: Table (receives narrative as context)
    num_groups = len(pass_a_result.get("groups", []))
    num_rows = num_groups if num_groups > 0 else min(len(pass_a_result.get("steps", [])), 12)
    table_schema = json.loads((DATA_DIR / "pass_b_table.schema.json").read_text())
    table_prompt = base_prompt
    table_prompt += "\n\n---\n\n## Narrative Analysis (from prior query):\n\n```json\n" + json.dumps(narrative_result, indent=2) + "\n```"
    table_prompt += f"\n\n---\n\nUsing the narrative analysis above as your interpretive frame, provide ONLY the analytical table. Produce exactly {num_rows} rows — one per group from Pass A. Cover all groups from beginning to end — do not truncate."
    try:
        table_result = llm.complete_structured_json(
            table_prompt,
            table_schema,
            system=ANALYSIS_SYSTEM_PROMPT,
        )
    except Exception as e:
        if debug_log is not None:
            debug_log.append({
                "name": f"Pass B Table ({framework})",
                "provider": prov,
                "prompt": table_prompt,
                "system": ANALYSIS_SYSTEM_PROMPT,
                "schema": table_schema,
                "error": str(e),
            })
        raise

    if debug_log is not None:
        debug_log.append({
            "name": f"Pass B Table ({framework})",
            "provider": prov,
            "prompt": table_prompt,
            "system": ANALYSIS_SYSTEM_PROMPT,
            "schema": table_schema,
            "output": table_result,
        })

    # Merge into single result matching the combined Pass B schema
    return {**narrative_result, **table_result}


# Global to store Pass A result for the current request (used by run_vl_analysis alias)
_current_pass_a_result = None


def run_vl_analysis(lesson_text: str, framework: str, framework_version: str | None = None) -> dict:
    """Alias that runs Pass B using stored Pass A result.

    This is for backwards compatibility with code that calls run_vl_analysis
    after run_pass_a has already been called.
    """
    global _current_pass_a_result
    fv = framework_version
    if _current_pass_a_result is None:
        # If no Pass A result stored, run it first
        _current_pass_a_result = run_pass_a(lesson_text, framework_version=fv)
    return run_pass_b(lesson_text, _current_pass_a_result, framework, framework_version=fv)


def process_job(job_id: str) -> None:
    """Process a pending job (called by async Lambda invocation)."""
    from book.state import save_job, load_job
    job = load_job(job_id)
    if not job or job.get("status") != "pending":
        return

    try:
        lesson_text = job["lesson_text"]
        pa_provider = job.get("pass_a_provider")
        pb_provider = job.get("pass_b_provider")
        fv = job.get("framework_version")
        split = job.get("split_pass_b", False)

        # Use _run_analysis for consistent caching with local path
        result = _run_analysis(lesson_text, split=split, pass_a_provider=pa_provider, pass_b_provider=pb_provider, framework_version=fv)

        job["pass_a_result"] = result["pass_a_result"]
        job["router_result"] = result["pass_a_result"]
        job["vl_analyses"] = result["vl_analyses"]

        # For backwards compatibility, also store primary result in "result"
        active_frameworks = get_active_frameworks(result["pass_a_result"])
        if not active_frameworks:
            active_frameworks = [result["pass_a_result"].get("primary_framework", "VL-1")]
        primary = active_frameworks[0]
        if primary in job["vl_analyses"] and job["vl_analyses"][primary].get("status") == "complete":
            job["result"] = job["vl_analyses"][primary]["result"]

        job["status"] = "complete"
        save_job(job_id, job)

    except Exception as e:
        # Update job with error
        job["status"] = "error"
        job["error"] = str(e)
        save_job(job_id, job)


def _run_analysis(text: str, split: bool = False, pass_a_provider: str | None = None, pass_b_provider: str | None = None, framework_version: str | None = None) -> dict:
    """Run LLM analysis (blocking). Returns dict with pass_a_result, vl_analyses, timings, and debug_log."""
    import time

    fv = framework_version
    # Include split mode, providers, and framework version in cache key
    cache_suffix = _framework_version_suffix(fv)
    if split:
        cache_suffix += ":split"
    if pass_a_provider and pass_a_provider != DEFAULT_LLM:
        cache_suffix += f":a={pass_a_provider}"
    if pass_b_provider and pass_b_provider != DEFAULT_LLM:
        cache_suffix += f":b={pass_b_provider}"
    cache_key = get_cache_key(text + cache_suffix)
    cached = load_cached_analysis(cache_key)

    if cached:
        cached["timings"] = {"cached": True}
        return cached

    timings = {}
    debug_log = []

    t0 = time.time()
    pass_a_result = run_pass_a(text, provider_key=pass_a_provider, debug_log=debug_log, framework_version=fv)
    timings["pass_a"] = round(time.time() - t0, 1)

    active_frameworks = get_active_frameworks(pass_a_result)
    if not active_frameworks:
        primary = pass_a_result.get("primary_framework", "VL-1")
        active_frameworks = [primary]

    vl_analyses = {}
    for framework in active_frameworks:
        t1 = time.time()
        try:
            analysis = run_pass_b(text, pass_a_result, framework, split=split, provider_key=pass_b_provider, debug_log=debug_log, framework_version=fv)
            vl_analyses[framework] = {"status": "complete", "result": analysis}
        except Exception as e:
            vl_analyses[framework] = {"status": "error", "error": str(e)}
        timings[f"pass_b_{framework}"] = round(time.time() - t1, 1)

    timings["total"] = round(time.time() - t0, 1)

    result = {"pass_a_result": pass_a_result, "vl_analyses": vl_analyses}
    save_cached_analysis(cache_key, result)
    result["timings"] = timings
    result["debug_log"] = debug_log
    return result
