"""Analysis engine for Feldenkrais lesson analysis.

Contains pass 0/A/0b analysis functions and framework helpers.
No dependency on FastAPI — can be imported independently by batch scripts.

Initialize with init(db, data_dir, get_llm_fn) before calling any functions.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

from cache_helpers import get_cache_key, load_cached_analysis, save_cached_analysis
from llm_config import DEFAULT_LLM

# ── Module state (initialized via init()) ──────────────────────────────────

_db = None
_data_dir = None
_get_llm = None
PROMPTS_DIR = None
FRAMEWORK_VERSION = None
_frameworks_cache: dict[str, list[dict]] = {}


def init(db, data_dir: Path, get_llm_fn):
    """Initialize the analysis module.

    Args:
        db: Database instance (or None).
        data_dir: Path to the data directory.
        get_llm_fn: Callable that returns an LLM instance (e.g., llm_config.get_llm).
    """
    global _db, _data_dir, _get_llm, PROMPTS_DIR, FRAMEWORK_VERSION
    _db = db
    _data_dir = data_dir
    _get_llm = get_llm_fn
    PROMPTS_DIR, FRAMEWORK_VERSION = _resolve_prompts_dir()


# ── Framework helpers ──────────────────────────────────────────────────────


def _resolve_prompts_dir(version: str | None = None) -> tuple[Path, str]:
    """Resolve the versioned prompts directory. If version is None, read from pointer."""
    if version is None:
        pointer = _data_dir / "frameworks.json"
        if pointer.exists():
            version = json.loads(pointer.read_text())["current_version"]
        else:
            version = "v1"
    return _data_dir / "frameworks" / version, version


def _framework_version_suffix(framework_version: str | None = None) -> str:
    """Return cache key suffix for framework version. Empty for v1 (backwards compat)."""
    v = framework_version or FRAMEWORK_VERSION
    if v == "v1":
        return ""
    return f":f{v}"


def load_frameworks(framework_version: str | None = None) -> list[dict]:
    """Load framework registry for a given version (cached per version)."""
    v = framework_version or FRAMEWORK_VERSION
    if v not in _frameworks_cache:
        prompts_dir, _ = _resolve_prompts_dir(v)
        path = prompts_dir / "frameworks.json"
        registry = json.loads(path.read_text())
        # New flat character-definitions schema
        if "characters" in registry:
            _frameworks_cache[v] = registry["characters"]
        else:
            # Legacy nested schema
            ver_key = registry.get("current_version", list(registry.get("versions", {}).keys())[0])
            _frameworks_cache[v] = registry["versions"][ver_key]["frameworks"]
    return _frameworks_cache[v]


def get_active_frameworks_config(framework_version: str | None = None) -> list[dict]:
    """Return only active (primary) frameworks from a given version."""
    frameworks = load_frameworks(framework_version)
    # New schema uses role="primary"; legacy uses active=True
    if frameworks and "role" in frameworks[0]:
        return [f for f in frameworks if f.get("role") == "primary"]
    return [f for f in frameworks if f.get("active", True)]


def _derive_key(char: dict) -> str:
    """Derive Pass A classification key from character id + name.

    VL-1 + The Riser → vl1_riser
    """
    if "key" in char:
        return char["key"]
    base = char["id"].lower().replace("-", "")
    name_part = char["name"].lower().replace("the ", "").replace(" ", "_")
    return f"{base}_{name_part}"


def build_classification_table(framework_version: str | None = None) -> str:
    """Build the VL classification markdown table from the framework registry."""
    rows = []
    for f in get_active_frameworks_config(framework_version):
        focus = f.get("focus") or _derive_focus(f)
        indicators = f.get("indicators") or _derive_indicators(f)
        rows.append(f"| **{f['id']}** {f['name']} | {focus} | {indicators} |")
    header = "| Framework | Focus | Key Indicators |\n|-----------|-------|----------------|\n"
    return header + "\n".join(rows)


def build_pass_a_schema(framework_version: str | None = None) -> dict:
    """Build Pass A schema with dynamic classification properties from registry."""
    schema = json.loads((_data_dir / "pass_a.schema.json").read_text())
    active = get_active_frameworks_config(framework_version)
    cls_props = {}
    cls_required = []
    for f in active:
        key = _derive_key(f)
        cls_props[key] = {"type": "number", "minimum": 0, "maximum": 1}
        cls_required.append(key)
    schema["properties"]["classifications"]["properties"] = cls_props
    schema["properties"]["classifications"]["required"] = cls_required
    schema["properties"]["primary_framework"]["enum"] = [f["id"] for f in active]
    schema["properties"]["secondary_frameworks"]["items"] = {
        "type": "string",
        "enum": [f["id"] for f in active],
    }
    return schema


def _derive_focus(char: dict) -> str:
    """Derive focus from core_verb + key phrases from essence.regulates."""
    if "focus" in char:
        return char["focus"]
    essence = char.get("essence", {})
    verb = essence.get("core_verb", "")
    regulates = essence.get("regulates", "")
    reg_sentences = [s.strip() for s in regulates.split(".") if s.strip()]
    reg_part = reg_sentences[0] if reg_sentences else ""
    return f"{verb.title()}: {reg_part}" if verb else reg_part


def _derive_indicators(char: dict) -> str:
    """Derive indicators from first 4-5 vocabulary_seeds."""
    if "indicators" in char:
        return char["indicators"]
    voice = char.get("voice", {})
    seeds = voice.get("vocabulary_seeds", [])
    return ", ".join(seeds[:5])


# ── System prompts ─────────────────────────────────────────────────────────

ROUTER_SYSTEM_PROMPT = """You are an expert Feldenkrais practitioner classifying lessons.
Analyze the lesson text and classify it according to which VL framework(s) best describe its pedagogical focus.
Be precise with your scores and provide specific indicators from the lesson text."""


# ── Pass 0: Text Segmentation ─────────────────────────────────────────────


def run_pass_0(text: str) -> dict:
    """
    Pass 0: Text Segmentation.

    Segments lesson text into sentence-level chunks with character offsets.
    Uses rule-based segmentation optimized for Feldenkrais lesson text patterns.

    Returns:
        {
            "segments": [{"id": 1, "text": "...", "start": 0, "end": 50}, ...],
            "original_length": 1234
        }
    """
    # Clean the text - normalize whitespace but preserve structure
    cleaned = text.strip()

    # Feldenkrais lessons often use "..." as instruction separators
    # Split on: sentence endings (. ! ?) followed by space/newline, or "..." patterns

    segments = []
    segment_id = 1

    # Use regex to find sentence boundaries
    sentence_pattern = re.compile(
        r'([.!?](?:\s|$))|'  # Standard sentence endings
        r'(\.{2,}\s*)|'       # Ellipsis (2+ dots)
        r'(\n\s*\n)|'         # Paragraph breaks
        r'(\n(?=\d+\.\s))'    # Newline before numbered step
    )

    last_end = 0

    for match in sentence_pattern.finditer(cleaned):
        # Get the text from last_end to end of this match
        end_pos = match.end()
        segment_text = cleaned[last_end:end_pos].strip()

        if segment_text and len(segment_text) > 2:  # Skip tiny fragments
            segments.append({
                "id": segment_id,
                "text": segment_text,
                "start": last_end,
                "end": end_pos
            })
            segment_id += 1

        last_end = end_pos

    # Don't forget the last segment if text doesn't end with punctuation
    if last_end < len(cleaned):
        remaining = cleaned[last_end:].strip()
        if remaining and len(remaining) > 2:
            segments.append({
                "id": segment_id,
                "text": remaining,
                "start": last_end,
                "end": len(cleaned)
            })

    # If segmentation produced too few segments, fall back to simpler approach
    if len(segments) < 3 and len(cleaned) > 200:
        # Fall back: split on any sentence-ending punctuation
        segments = []
        segment_id = 1
        last_end = 0

        for match in re.finditer(r'[.!?]+\s*', cleaned):
            end_pos = match.end()
            segment_text = cleaned[last_end:end_pos].strip()
            if segment_text:
                segments.append({
                    "id": segment_id,
                    "text": segment_text,
                    "start": last_end,
                    "end": end_pos
                })
                segment_id += 1
            last_end = end_pos

        if last_end < len(cleaned):
            remaining = cleaned[last_end:].strip()
            if remaining:
                segments.append({
                    "id": segment_id,
                    "text": remaining,
                    "start": last_end,
                    "end": len(cleaned)
                })

    return {
        "segments": segments,
        "original_length": len(cleaned)
    }


def format_text_with_segments(pass_0_result: dict) -> str:
    """
    Format text with segment markers for LLM consumption.

    Returns text like:
    [1] Lie on your back on the floor.
    [2] Bend your knees so your feet are standing.
    ...
    """
    lines = []
    for seg in pass_0_result.get("segments", []):
        lines.append(f"[{seg['id']}] {seg['text']}")
    return "\n".join(lines)


# ── Pass A: Decomposition + Routing ───────────────────────────────────────


def load_pass_a(framework_version: str | None = None) -> tuple[str, dict]:
    """Load Pass A prompt and schema (Decomposition + Routing).

    The prompt's {CLASSIFICATION_TABLE} placeholder is populated from
    the framework registry, and the schema's classification properties
    are built dynamically. Prompt comes from versioned PROMPTS_DIR,
    schema from shared DATA_DIR.
    """
    prompts_dir, _ = _resolve_prompts_dir(framework_version or FRAMEWORK_VERSION)
    prompt_path = prompts_dir / "pass_a.md"

    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    fv = framework_version or FRAMEWORK_VERSION
    prompt = prompt.replace("{CLASSIFICATION_TABLE}", build_classification_table(fv))

    schema = build_pass_a_schema(fv)

    return prompt, schema


def _pass_a_cache_key(lesson_text: str, provider_key: str | None = None, framework_version: str | None = None) -> str:
    """Cache key for Pass A results, independent of Pass B provider."""
    suffix = ":pass_a" + _framework_version_suffix(framework_version)
    if provider_key and provider_key != DEFAULT_LLM:
        suffix += f":{provider_key}"
    return get_cache_key(lesson_text + suffix)


def run_pass_a(lesson_text: str, provider_key: str | None = None, debug_log: list | None = None, framework_version: str | None = None) -> dict:
    """Run Pass A: Decomposition + Routing.

    Internally runs Pass 0 to segment text, then passes segmented text to LLM.
    Returns VL classification, SPIFFER-Q decomposed steps, AND Pass 0 segments.
    Results are cached independently of Pass B provider.
    """
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
    pass_0_result = run_pass_0(lesson_text)
    segmented_text = format_text_with_segments(pass_0_result)

    pass_a_prompt, pass_a_schema = load_pass_a(fv)

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

    llm = _get_llm(provider_key)
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

    # Normalize segment_start/segment_end → segments array for downstream compatibility
    all_input_ids = set(s["id"] for s in pass_0_result.get("segments", []))
    for step in result.get("steps", []):
        if "segment_start" in step and "segment_end" in step and "segments" not in step:
            seg_start = step["segment_start"]
            seg_end = step["segment_end"]
            step["segments"] = sorted(
                sid for sid in all_input_ids if seg_start <= sid <= seg_end
            )

    # Validate segment coverage — check for gaps
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

    return result


# ── Pass 0b: Somatic Feature Extraction ───────────────────────────────────


def load_pass_0b() -> tuple[str, dict]:
    """Load Pass 0b prompt and schema (Step-Level Enrichment)."""
    prompt_path = _data_dir / "pass_0b.md"
    schema_path = _data_dir / "pass_0b.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_pass_0b_lang() -> tuple[str, dict]:
    """Load Pass 0b-lang prompt and schema (Step-Level Language Analysis)."""
    prompt_path = _data_dir / "pass_0b_lang.md"
    schema_path = _data_dir / "pass_0b_lang.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_pass_0b_word_lang() -> tuple[str, dict]:
    """Load Pass 0b word-level language annotation prompt and schema."""
    prompt_path = _data_dir / "pass_0b_word_lang.md"
    schema_path = _data_dir / "pass_0b_word_lang.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_pass_0b_condensed() -> tuple[str, dict]:
    """Load Pass 0b condensed prompt and schema."""
    prompt_path = _data_dir / "pass_0b_condensed.md"
    schema_path = _data_dir / "pass_0b_condensed.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_pass_0b_segmentation() -> tuple[str, dict]:
    """Load Pass 0b segmentation prompt and schema."""
    prompt_path = _data_dir / "pass_0b_segmentation.md"
    schema_path = _data_dir / "pass_0b_segmentation.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def format_steps_for_0b(pass_a_result: dict) -> str:
    """Build LLM input text for Pass 0b from Pass A output.

    Iterates groups → steps within each group, including original segment texts.
    Returns formatted text like:

    ## Group 1: Setup and Home Position
    Establishing baseline configuration...

    ### Step 1 [S, P] — Lie on back, feel contact with floor
    [1] Lie on your back on the floor.
    [2] Feel the contact of your body with the surface.
    """
    pass_0 = pass_a_result.get("pass_0", {})
    segments = pass_0.get("segments", [])
    seg_lookup = {s["id"]: s["text"] for s in segments}

    steps = pass_a_result.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps}

    groups = pass_a_result.get("groups", [])

    # Track which steps are assigned to groups
    grouped_step_nums = set()
    for g in groups:
        for sn in g.get("steps", []):
            grouped_step_nums.add(sn)

    lines = []

    for g in groups:
        gnum = g.get("group_number", 0)
        label = g.get("label", "")
        desc = g.get("description", "")
        lines.append(f"## Group {gnum}: {label}")
        if desc:
            lines.append(desc)
        lines.append("")

        for sn in g.get("steps", []):
            step = step_lookup.get(sn)
            if not step:
                continue
            tags_str = ", ".join(step.get("tags", []))
            instruction = step.get("instruction", "")
            lines.append(f"### Step {sn} [{tags_str}] — {instruction}")

            # Include original segment texts
            for seg_id in step.get("segments", []):
                text = seg_lookup.get(seg_id, "")
                if text:
                    lines.append(f"[{seg_id}] {text}")

            lines.append("")

    # Handle ungrouped steps
    ungrouped = [s for s in steps if s["step_number"] not in grouped_step_nums]
    if ungrouped:
        lines.append("## Ungrouped Steps")
        lines.append("")
        for step in ungrouped:
            sn = step["step_number"]
            tags_str = ", ".join(step.get("tags", []))
            instruction = step.get("instruction", "")
            lines.append(f"### Step {sn} [{tags_str}] — {instruction}")
            for seg_id in step.get("segments", []):
                text = seg_lookup.get(seg_id, "")
                if text:
                    lines.append(f"[{seg_id}] {text}")
            lines.append("")

    return "\n".join(lines)


def format_steps_for_word_lang(pass_a_result: dict, lang_enriched: list) -> str:
    """Build LLM input for word-level annotation from Pass A output + lang analysis.

    Each step shows the original transcript segments followed by the prior
    language analysis results as context.
    """
    pass_0 = pass_a_result.get("pass_0", {})
    segments = pass_0.get("segments", [])
    seg_lookup = {s["id"]: s["text"] for s in segments}

    groups = pass_a_result.get("groups", [])
    steps = pass_a_result.get("steps", [])

    # Build lang lookup
    lang_lookup = {e["step_number"]: e for e in lang_enriched}

    # Map steps to groups
    group_lookup = {}
    for g in groups:
        for sn in g.get("steps", []):
            group_lookup[sn] = g

    lines = []
    for step in steps:
        sn = step["step_number"]
        g = group_lookup.get(sn, {})
        gn = g.get("group_number", "?")
        g_title = g.get("title", "")

        tags = step.get("tags", [])
        tag_str = ", ".join(tags) if tags else ""
        instruction = step.get("instruction", "")

        lines.append(f"## Group {gn}: {g_title}")
        lines.append(f"### Step {sn} [{tag_str}] — {instruction}")

        # Original segment texts
        for sid in step.get("segments", []):
            text = seg_lookup.get(sid, "")
            if text:
                lines.append(f"[{sid}] {text}")

        # Prior language analysis as context
        lang = lang_lookup.get(sn, {})
        lang_fields = []
        for field in ["attention_cues", "effort_language", "image_language",
                      "explanatory_language", "distinctive_phrasing"]:
            val = lang.get(field, "None")
            if val and val != "None":
                label = field.replace("_", " ").title()
                lang_fields.append(f"  - {label}: {val}")

        if lang_fields:
            lines.append("\n**Prior language analysis:**")
            lines.extend(lang_fields)

        lines.append("")

    return "\n".join(lines)


def format_input_for_segmentation(
    pass_a_result: dict,
    somatic_summary: dict,
    lang_summary: dict,
) -> str:
    """Build lightweight LLM input for segmentation query.

    Includes only group labels/descriptions and lesson-level summaries
    (not full step data) to keep the query fast and cheap.
    """
    lines = []

    # ── Lesson-level summaries (brief) ──
    lines.append("## Lesson-Level Summaries")
    lines.append("")

    if somatic_summary:
        if somatic_summary.get("somatic_architecture_summary"):
            lines.append("### Somatic Architecture Summary")
            lines.append(somatic_summary["somatic_architecture_summary"])
            lines.append("")
        if somatic_summary.get("differentiation_strategy"):
            lines.append(f"**Differentiation Strategy:** {somatic_summary['differentiation_strategy']}")
            lines.append("")

    if lang_summary:
        if lang_summary.get("teaching_voice_summary"):
            lines.append("### Language Summary")
            lines.append(lang_summary["teaching_voice_summary"])
            lines.append("")

    # ── Group list with descriptions ──
    lines.append("## Groups")
    lines.append("")

    groups = pass_a_result.get("groups", [])
    steps = pass_a_result.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps}

    for g in groups:
        gnum = g.get("group_number", 0)
        label = g.get("label", "")
        desc = g.get("description", "")
        lines.append(f"### Group {gnum}: {label}")
        if desc:
            lines.append(desc)

        # Include step instructions (lightweight — just the text, no enrichment)
        step_nums = g.get("steps", [])
        if step_nums:
            step_texts = []
            for sn in step_nums:
                pa_step = step_lookup.get(sn)
                if pa_step:
                    step_texts.append(f"- Step {sn}: {pa_step.get('instruction', '')}")
            if step_texts:
                lines.append("\n".join(step_texts))
        lines.append("")

    return "\n".join(lines)


def format_input_for_0b_condensed(
    pass_a_result: dict,
    somatic_enriched: list,
    somatic_summary: dict,
    lang_enriched: list,
    lang_summary: dict,
    group_filter: set | None = None,
) -> str:
    """Build LLM input text for Pass 0b Condensed from all prior analysis outputs.

    Assembles a markdown document with:
    - Lesson-level summaries (somatic + language) for big-picture context
    - Per group: all steps with somatic + language fields

    If group_filter is provided, only include groups whose group_number is in the set.
    """
    lines = []

    # ── Lesson-level summaries ──
    lines.append("## Lesson-Level Summaries")
    lines.append("")

    if somatic_summary:
        lines.append("### Somatic Architecture Summary")
        if somatic_summary.get("somatic_architecture_summary"):
            lines.append(somatic_summary["somatic_architecture_summary"])
            lines.append("")
        if somatic_summary.get("primary_transmission_routes"):
            lines.append("**Primary Transmission Routes:**")
            for route in somatic_summary["primary_transmission_routes"]:
                lines.append(f"- {route}")
            lines.append("")
        if somatic_summary.get("differentiation_strategy"):
            lines.append(f"**Differentiation Strategy:** {somatic_summary['differentiation_strategy']}")
            lines.append("")
        if somatic_summary.get("key_inhibitions"):
            lines.append("**Key Inhibitions:**")
            for inhib in somatic_summary["key_inhibitions"]:
                lines.append(f"- {inhib}")
            lines.append("")
        if somatic_summary.get("ground_force_strategy"):
            lines.append(f"**Ground Force Strategy:** {somatic_summary['ground_force_strategy']}")
            lines.append("")

    if lang_summary:
        lines.append("### Language Summary")
        if lang_summary.get("teaching_voice_summary"):
            lines.append(lang_summary["teaching_voice_summary"])
            lines.append("")
        if lang_summary.get("dominant_attention_mode"):
            lines.append(f"**Dominant Attention Mode:** {lang_summary['dominant_attention_mode']}")
            lines.append("")
        if lang_summary.get("effort_philosophy"):
            lines.append(f"**Effort Philosophy:** {lang_summary['effort_philosophy']}")
            lines.append("")
        if lang_summary.get("speaker_principles"):
            lines.append("**Speaker Principles:**")
            for princ in lang_summary["speaker_principles"]:
                lines.append(f"- {princ}")
            lines.append("")
        if lang_summary.get("signature_vocabulary"):
            lines.append("**Signature Vocabulary:**")
            for vocab in lang_summary["signature_vocabulary"]:
                lines.append(f"- {vocab}")
            lines.append("")

    # ── Per-group step data ──
    lines.append("## Groups and Steps")
    lines.append("")

    # Build lookups
    somatic_lookup = {e["step_number"]: e for e in somatic_enriched}
    lang_lookup = {e["step_number"]: e for e in lang_enriched}

    groups = pass_a_result.get("groups", [])
    steps = pass_a_result.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps}

    somatic_fields = [
        "clarified", "mechanical_directive", "attentional_cue", "sensory_inquiry",
        "differentiation", "activation", "inhibition", "transmission_route",
        "ground_push", "intent",
    ]
    lang_fields = [
        "attention_cues", "effort_language", "image_language",
        "explanatory_language", "distinctive_phrasing",
    ]

    for g in groups:
        gnum = g.get("group_number", 0)
        if group_filter is not None and gnum not in group_filter:
            continue
        label = g.get("label", "")
        desc = g.get("description", "")
        lines.append(f"### Group {gnum}: {label}")
        if desc:
            lines.append(desc)
        lines.append("")

        for sn in g.get("steps", []):
            pa_step = step_lookup.get(sn)
            if not pa_step:
                continue
            instruction = pa_step.get("instruction", "")
            lines.append(f"#### Step {sn} — {instruction}")

            som = somatic_lookup.get(sn, {})
            for field in somatic_fields:
                val = som.get(field, "")
                if val and str(val).lower() != "none":
                    lines.append(f"- **{field}**: {val}")

            lang = lang_lookup.get(sn, {})
            for field in lang_fields:
                val = lang.get(field, "")
                if val and str(val).lower() != "none":
                    lines.append(f"- **{field}**: {val}")

            lines.append("")

    return "\n".join(lines)


# ── Pass 0b LLM calls ─────────────────────────────────────────────────────


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
    cache_suffix = ":pass_0b_v2"
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
    llm = _get_llm(provider_key)
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

        # Validate step count — LLM may silently truncate large outputs
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


def run_pass_0b_lang(lesson_text: str, provider_key: str | None = None, debug_log: list | None = None) -> dict:
    """
    Pass 0b-lang: Step-level language analysis.

    Runs Pass A to decompose the lesson into steps/groups, then sends
    the step-level input to an LLM for language analysis.

    Returns:
        {
            "pass_a": { ... pass A result ... },
            "enriched_steps": [ { step_number, group_number, ... }, ... ],
            "summary": { speaker_principles, signature_vocabulary, ... },
            "elapsed": float,
            "cached": bool,
        }
    """
    import time

    # Run Pass A first (has its own cache)
    pass_a_result = run_pass_a(lesson_text, provider_key, debug_log)
    steps_text = format_steps_for_0b(pass_a_result)

    # Load prompt and schema
    prompt_template, schema = load_pass_0b_lang()

    # Build the full prompt
    prompt = prompt_template + steps_text

    system_prompt = (
        "You are an expert in Feldenkrais pedagogy and language analysis. "
        "Analyze the teacher's language choices at each step. "
        "Process every step. Do not skip, merge, or split steps."
    )

    # Check cache
    cache_suffix = ":pass_0b_lang_v1"
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
    llm = _get_llm(provider_key)
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

        # Validate step count
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

        # Capture token usage
        usage = llm.last_usage if llm.last_usage else {}

        # Cache the result
        save_cached_analysis(cache_key, {"enriched_steps": enriched, "summary": summary, "usage": usage})

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

    Runs Pass A (cached) → runs pass_0b_lang (cached) → formats combined input →
    calls LLM with word-level annotation prompt.

    Returns:
        {
            "pass_a": { ... },
            "word_annotations": [{ step_number, phrases: [{ text, category, note }] }],
            "elapsed": float,
            "cached": bool,
        }
    """
    import time

    # Run Pass A first (has its own cache)
    pass_a_result = run_pass_a(lesson_text, provider_key, debug_log)

    # Run pass_0b_lang (has its own cache)
    lang_result = run_pass_0b_lang(lesson_text, provider_key, debug_log)
    lang_enriched = lang_result.get("enriched_steps", [])

    # Format combined input
    steps_text = format_steps_for_word_lang(pass_a_result, lang_enriched)

    # Load prompt and schema
    prompt_template, schema = load_pass_0b_word_lang()
    prompt = prompt_template + steps_text

    system_prompt = (
        "You are an expert in Feldenkrais pedagogy and language analysis. "
        "Annotate the teacher's language at the word/phrase level. "
        "Process every step. Do not skip, merge, or split steps."
    )

    # Check cache
    cache_suffix = ":pass_0b_word_lang_v3"
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

    # Run LLM
    llm = _get_llm(provider_key)
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
        result = llm.complete_structured_json(
            prompt=prompt,
            schema=schema,
            system=system_prompt,
        )
        elapsed = round(time.time() - t0, 1)

        if debug_log is not None:
            debug_log[-1]["output"] = json.dumps(result, indent=2)
            debug_log[-1]["elapsed"] = elapsed

        # Regroup flat annotations into per-step format for the client
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

        # Capture token usage
        usage = llm.last_usage if llm.last_usage else {}

        # Cache the result
        save_cached_analysis(cache_key, {"word_annotations": word_annotations, "usage": usage})

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


# ── Segmentation + Condensed Synthesis ─────────────────────────────────────


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

    input_text = format_input_for_segmentation(pass_a_result, somatic_summary, lang_summary)

    prompt_template, schema = load_pass_0b_segmentation()

    # Inject user hint if provided
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

    # Check cache (hint is included so different hints produce different results)
    cache_suffix = ":segmentation_v2"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    hint_suffix = f":hint:{hint.strip()}" if hint and hint.strip() else ""
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix + hint_suffix)
    if not skip_cache:
        cached = load_cached_analysis(cache_key)
        if cached:
            return {"segments": cached.get("segments", []), "cached": True}

    # Run LLM
    llm = _get_llm(provider_key)
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
        result = llm.complete_structured_json(
            prompt=prompt,
            schema=schema,
            system=system_prompt,
        )
        elapsed = round(time.time() - t0, 1)

        if debug_log is not None:
            debug_log[-1]["output"] = json.dumps(result, indent=2)
            debug_log[-1]["elapsed"] = elapsed

        segments = result.get("segments", [])
        usage = llm.last_usage if llm.last_usage else {}

        save_cached_analysis(cache_key, {"segments": segments, "usage": usage})

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
    input_text = format_input_for_0b_condensed(
        pass_a_result, somatic_enriched, somatic_summary,
        lang_enriched, lang_summary, group_filter=group_filter,
    )

    prompt_template, schema = load_pass_0b_condensed()

    # For multi-segment runs, load foreground/background framing from template
    segment_preamble = ""
    if segment_context:
        seg_num = segment_context["segment_number"]
        seg_theme = segment_context["theme"]
        all_segs = segment_context["all_segments"]

        seg_overview = "\n".join(
            f"  - Segment {s['segment_number']}: Groups {s['groups']} — {s['theme']}"
            for s in all_segs
        )

        segment_template_path = _data_dir / "pass_0b_condensed_segment.md"
        segment_preamble = segment_template_path.read_text() if segment_template_path.exists() else ""
        segment_preamble += (
            f"You are synthesizing **Segment {seg_num}: {seg_theme}**.\n\n"
            f"{seg_overview}\n\n"
        )

        prompt = prompt_template + segment_preamble + input_text
    else:
        prompt = prompt_template + input_text

    # Inject user hint if provided (between prompt template and input data)
    if hint and hint.strip():
        hint_block = f"\n\n## User Guidance\n{hint.strip()}\n"
        # Insert hint before the input data section
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

    llm = _get_llm(provider_key)

    try:
        result = llm.complete_structured_json(
            prompt=prompt,
            schema=schema,
            system=system_prompt,
        )

        if debug_log is not None:
            debug_log[-1]["output"] = json.dumps(result, indent=2)

        usage = llm.last_usage if llm.last_usage else {}
        return {
            "group_cards": result.get("group_cards", []),
            "setting": result.get("setting", {}),
            "usage": usage,
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

    Returns:
        {
            "segments": [
                {
                    "segment_number": 1,
                    "theme": "...",
                    "transition_note": "...",
                    "group_cards": [...],
                    "setting": {...},
                }
            ],
            "elapsed": float,
            "cached": bool,
        }
    """
    import time

    pass_a_result = somatic_result.get("pass_a", {})
    somatic_enriched = somatic_result.get("enriched_steps", [])
    somatic_summary = somatic_result.get("summary", {})
    lang_enriched = lang_result.get("enriched_steps", [])
    lang_summary = lang_result.get("summary", {})

    # Check cache (v4 — segmented output with foreground/background framing)
    cache_suffix = ":pass_0b_condensed_v4"
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

    # Phase 1: Segmentation
    seg_result = run_segmentation_query(
        pass_a_result, somatic_summary, lang_summary,
        lesson_text, provider_key, debug_log, skip_cache=skip_cache,
        hint=hint,
    )
    segments_meta = seg_result.get("segments", [])

    # Phase 2: Per-segment condensed synthesis
    output_segments = []

    if len(segments_meta) <= 1:
        # Single segment — run without group filter (identical to old behavior)
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
        # Multiple segments — run condensed per segment with foreground/background framing
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

    # Cache the segmented result
    save_cached_analysis(cache_key, {"segments": output_segments})

    return {
        "segments": output_segments,
        "elapsed": elapsed,
        "cached": False,
    }
