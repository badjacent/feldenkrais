"""Formatters that build LLM input text for Pass 0b analysis steps.

Each formatter takes prior analysis results and produces a markdown string
suitable for LLM consumption.
"""


def format_steps_for_0b(pass_a_result: dict) -> str:
    """Build LLM input text for Pass 0b from Pass A output.

    Iterates groups -> steps within each group, including original segment texts.
    Returns formatted text like:

    ## Group 1: Setup and Home Position
    Establishing baseline configuration...

    ### Step 1 [S, P] -- Lie on back, feel contact with floor
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

    # -- Lesson-level summaries (brief) --
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

    # -- Group list with descriptions --
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

        # Include step instructions (lightweight -- just the text, no enrichment)
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

    # -- Lesson-level summaries --
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

    # -- Per-group step data --
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


def format_input_for_concepts(pass_a_result: dict, somatic_steps: list) -> str:
    """Build LLM input for concept extraction from Pass A + Pass 0b somatic.

    Produces a compact representation with group structure, step instructions,
    and key somatic fields (differentiation, activation, transmission_route).
    """
    pass_0 = pass_a_result.get("pass_0", {})
    segments = pass_0.get("segments", [])
    seg_lookup = {s["id"]: s["text"] for s in segments}

    steps = pass_a_result.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps}
    somatic_lookup = {s["step_number"]: s for s in somatic_steps}

    groups = pass_a_result.get("groups", [])

    somatic_fields = ["differentiation", "activation", "inhibition",
                      "transmission_route", "ground_push"]

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
            pa_step = step_lookup.get(sn)
            if not pa_step:
                continue
            tags_str = ", ".join(pa_step.get("tags", []))
            instruction = pa_step.get("instruction", "")
            lines.append(f"### Step {sn} [{tags_str}] — {instruction}")

            # Include original segment texts
            for seg_id in pa_step.get("segments", []):
                text = seg_lookup.get(seg_id, "")
                if text:
                    lines.append(f"[{seg_id}] {text}")

            # Include key somatic fields
            som = somatic_lookup.get(sn, {})
            for field in somatic_fields:
                val = som.get(field, "")
                if val and str(val).lower() != "none":
                    lines.append(f"- **{field}**: {val}")

            lines.append("")

    return "\n".join(lines)


def format_input_for_spatial(pass_a_result: dict, somatic_steps: list) -> str:
    """Build LLM input for spatial extraction from Pass A groups + Pass 0b somatic.

    Includes only the fields relevant to spatial geometry: clarified,
    ground_push, and mechanical_directive per step, organized by group.
    """
    steps = pass_a_result.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps}
    somatic_lookup = {s["step_number"]: s for s in somatic_steps}

    groups = pass_a_result.get("groups", [])

    spatial_fields = ["clarified", "ground_push", "mechanical_directive"]

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
            pa_step = step_lookup.get(sn)
            if not pa_step:
                continue
            instruction = pa_step.get("instruction", "")
            lines.append(f"### Step {sn} — {instruction}")

            som = somatic_lookup.get(sn, {})
            for field in spatial_fields:
                val = som.get(field, "")
                if val and str(val).lower() != "none":
                    lines.append(f"- **{field}**: {val}")

            lines.append("")

    return "\n".join(lines)
