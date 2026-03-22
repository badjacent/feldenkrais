"""Booklet section renderers and full booklet HTML assembly."""

import json
from datetime import datetime, timezone

from book.analysis import get_vl_booklet_intros
from book.booklet_data import (
    VL_BOOKLET_INTROS, SPIFFER_TRANSLATIONS,
    booklet_query_block, map_groups_to_table_rows,
    _get_negotiation_state, _get_column_by_keyword,
)
from book.state import DATA_DIR


def render_booklet_cover(title: str, framework_id: str, source_url: str = "") -> str:
    """Render the booklet cover page."""
    intro = VL_BOOKLET_INTROS.get(framework_id, {})
    framework_name = intro.get("name", framework_id)
    subtitle = intro.get("subtitle_derived", "")

    source_link = ""
    if source_url:
        source_link = f'<p class="cover-source"><a href="{source_url}" target="_blank">Original Lesson &rarr;</a></p>'

    return f"""
    <div class="booklet-cover">
        <div class="cover-content">
            <h1 class="cover-title">{title}</h1>
            <p class="cover-subtitle">A Companion to This Lesson</p>
            {source_link}
            <div class="cover-lens">
                Explored through the lens of<br>
                <strong><em>{framework_name}</em></strong>
                <span class="cover-lens-subtitle">{subtitle}</span>
            </div>
        </div>
    </div>
    """


def render_booklet_intro(lesson_title: str = "", framework_id: str = "",
                         somatic_narrative: dict = None, groups: list = None) -> str:
    """Render the 'What This Booklet Is' section with an LLM query block."""
    somatic_narrative = somatic_narrative or {}
    groups = groups or []

    intro = VL_BOOKLET_INTROS.get(framework_id, {})
    framework_name = intro.get("name", framework_id)
    primary_inquiry = somatic_narrative.get("primary_inquiry", "")
    group_summary = ", ".join(
        f"Part {g.get('group_number', '?')}: {g.get('label', '')}" for g in groups
    )

    context = {
        "lesson_title": lesson_title,
        "primary_inquiry": primary_inquiry,
        "framework_id": framework_id,
        "framework_name": framework_name,
        "groups_summary": [{"group_number": g.get("group_number"), "label": g.get("label")} for g in groups],
        "somatic_narrative": somatic_narrative,
    }

    return f"""
    <section class="booklet-section">
        <h2>What This Booklet Is</h2>
        {booklet_query_block("booklet-intro", context)}
    </section>
    """


def render_booklet_home(home_position: str) -> str:
    """Render the 'Where You Started' section."""
    return f"""
    <section class="booklet-section">
        <h2>Where You Started</h2>
        <div class="home-position">
            <p>{home_position}</p>
        </div>
        <p>
            This starting position isn't arbitrary. It's chosen to give your nervous system
            a baseline — a clear sense of how things are <em>before</em> the lesson begins.
            You'll return to it at the end, and the contrast is often striking.
        </p>
    </section>
    """


def render_booklet_inquiry(somatic_narrative: dict) -> str:
    """Render 'The Question This Lesson Asks' section with LLM-translated inquiry."""
    primary = somatic_narrative.get("primary_inquiry", "")

    html = ['<section class="booklet-section">']
    html.append("<h2>The Question This Lesson Asks</h2>")

    if primary:
        query_block = booklet_query_block("booklet-inquiry", {
            "primary_inquiry": primary,
        })
        html.append(f'<blockquote class="primary-inquiry">{query_block}</blockquote>')

    html.append("</section>")
    return "\n".join(html)


def render_booklet_person_inside_narrative(framework_id: str, vl_analysis: dict, somatic_narrative: dict = None) -> str:
    """Render the opening Person Inside narrative — full lesson story in one paragraph."""
    intro = VL_BOOKLET_INTROS.get(framework_id, {})
    if not intro:
        return ""

    result = vl_analysis.get("result", {})
    table = result.get("analytical_table", {})
    rows = table.get("rows", [])

    vl_voice_parts = []
    shadow_voice_parts = []
    for i, row in enumerate(rows, 1):
        vl_v = row.get("vl_voice", "")
        sh_v = row.get("shadow_voice", "") or row.get("striver_shadow", "")
        if vl_v:
            vl_voice_parts.append(f"Stage {i}: {vl_v}")
        if sh_v:
            shadow_voice_parts.append(f"Stage {i}: {sh_v}")

    if not vl_voice_parts:
        return ""

    primary_inquiry = (somatic_narrative or {}).get("primary_inquiry", "")
    context = {
        "framework_name": intro.get("name", ""),
        "person_inside": intro.get("person_inside", ""),
        "person_description": intro.get("person_description_derived", ""),
        "primary_inquiry": primary_inquiry,
        "vl_voices": "\n".join(vl_voice_parts),
        "shadow_voices": "\n".join(shadow_voice_parts) if shadow_voice_parts else "(no shadow observations)",
    }

    html = ['<section class="booklet-section">']
    html.append(f'<h2>{intro["person_inside"]}</h2>')
    html.append(booklet_query_block("lens-synthesis", context))
    html.append("</section>")
    return "\n".join(html)


def render_booklet_concepts(lesson_title: str = "", framework_id: str = "",
                            somatic_narrative: dict = None, groups: list = None) -> str:
    """Render 'How Feldenkrais Lessons Work' section — LLM-customized for this lesson."""
    somatic_narrative = somatic_narrative or {}
    groups = groups or []
    intro = VL_BOOKLET_INTROS.get(framework_id, {})

    group_summary = ", ".join(
        f"Part {g.get('group_number', '?')}: {g.get('label', '')}" for g in groups
    )

    context = {
        "lesson_title": lesson_title,
        "primary_inquiry": somatic_narrative.get("primary_inquiry", ""),
        "framework_name": intro.get("name", framework_id),
        "group_summary": group_summary,
    }

    html = ['<section class="booklet-section booklet-concepts">']
    html.append("<h2>How Feldenkrais Lessons Work</h2>")
    html.append(booklet_query_block("lesson-concepts", context))
    html.append("</section>")
    return "\n".join(html)


def render_council_section() -> str:
    """Render the 'Council of Five' section from data/council_intro.html."""
    path = DATA_DIR / "council_intro.html"
    if path.exists():
        return path.read_text()
    return ""


def render_booklet_journey(groups: list, steps: list, vl_analysis: dict, pass_a_steps: dict, framework_id: str = "") -> str:
    """Render the main 'Journey' section — group by group walkthrough."""
    html = ['<section class="booklet-section booklet-journey">']
    html.append("<h2>The Journey</h2>")

    # Get Person Inside name and framework name for play-format dialogue
    intro = VL_BOOKLET_INTROS.get(framework_id, {})
    person_inside_name = intro.get("person_inside", "The Person Inside")
    framework_name = intro.get("name", "")

    # Get analytical table data from the primary VL analysis
    result = vl_analysis.get("result", {})
    table = result.get("analytical_table", {})
    column_headers = table.get("column_headers", {})

    group_to_rows = map_groups_to_table_rows(groups, table, pass_a_steps)

    step_lookup = {s["step_number"]: s for s in steps if "step_number" in s}

    for g in groups:
        group_num = g.get("group_number", "?")
        group_label = g.get("label", f"Group {group_num}")
        group_desc = g.get("description", "")
        group_step_nums = g.get("steps", [])

        html.append(f'<div class="journey-group">')
        html.append(f'<h3>Part {group_num}: {group_label}</h3>')

        # Step list (no header)
        html.append('<ol class="step-list">')
        for sn in group_step_nums:
            step = step_lookup.get(sn)
            if step:
                instruction = step.get("instruction", "")
                html.append(f"<li>{instruction}</li>")
        html.append("</ol>")

        # Pedagogical gloss — joined as opening paragraph
        group_instructions = []
        all_tags = []
        for sn in group_step_nums:
            step = step_lookup.get(sn)
            if step:
                group_instructions.append(step.get("instruction", ""))
                all_tags.extend(step.get("tags", []))
        if all_tags:
            from collections import Counter
            tag_counts = Counter(all_tags)
            prominent = [tag for tag, count in tag_counts.most_common(3) if count > 1 or len(group_step_nums) <= 3]
            if prominent:
                tag_labels = [SPIFFER_TRANSLATIONS.get(t, t) for t in prominent]

                matched_rows = group_to_rows.get(group_num, [])
                matched_rows_summary = []
                for row in matched_rows:
                    row_summary = {}
                    for k, v in row.items():
                        if v and k not in ("row_number",):
                            row_summary[k] = v
                    if row_summary:
                        matched_rows_summary.append(row_summary)

                query_context = {
                    "all_groups": [{"group_number": gg.get("group_number"), "label": gg.get("label"), "description": gg.get("description")} for gg in groups],
                    "all_steps": [{"step_number": s.get("step_number"), "instruction": s.get("instruction"), "tags": s.get("tags", [])} for s in steps],
                    "target_group": {
                        "group_number": group_num,
                        "label": group_label,
                        "description": group_desc,
                        "steps": group_instructions,
                        "prominent_tags": tag_labels,
                        "analytical_rows": matched_rows_summary,
                    },
                    "framework_name": framework_name,
                }
                html.append(booklet_query_block("pedagogical-gloss", query_context))

        # The Question + Person Inside / Shadow dialogue
        matched_rows = group_to_rows.get(group_num, [])
        if matched_rows:
            for row in matched_rows:
                question = _get_column_by_keyword(row, column_headers, "question")
                vl_voice = row.get("vl_voice", "")
                shadow_voice = row.get("shadow_voice", "") or row.get("striver_shadow", "")

                if question:
                    html.append(f'<p class="the-question"><strong>The question:</strong> {question}</p>')

                if vl_voice:
                    html.append(f'<p><strong>{person_inside_name}:</strong> <em>{vl_voice}</em></p>')

                if shadow_voice:
                    habit_label = intro.get("habit_label_derived", "The Blob")
                    html.append(f'<p><strong>{habit_label}:</strong> <em>{shadow_voice}</em></p>')

        html.append("</div>")  # end journey-group

    html.append("</section>")
    return "\n".join(html)


def render_booklet_arc(somatic_narrative: dict, negotiation: dict, groups: list, table: dict, column_headers: dict, pass_a_steps: dict) -> str:
    """Render 'The Arc' section — overall trajectory."""
    html = ['<section class="booklet-section">']
    html.append("<h2>The Arc</h2>")
    html.append("<p>Every Feldenkrais lesson has a trajectory — from effort to ease, from noise to quiet. Here's how this lesson made that journey.</p>")

    # Washing gradient
    washing = somatic_narrative.get("washing_gradient", "")
    if washing:
        html.append('<h3>What Changed</h3>')
        html.append(f"<p>{washing}</p>")

    # Threshold of quiet
    threshold = somatic_narrative.get("threshold_of_quiet", "")
    if threshold:
        html.append('<h3>The Threshold of Quiet</h3>')
        html.append(f"<p>{threshold}</p>")

    # Shadow and Foundation observations
    if negotiation:
        shadow_obs = negotiation.get("shadow_observations", []) or negotiation.get("striver_observations", [])
        foundation_obs = negotiation.get("foundation_observations", [])

        if shadow_obs:
            html.append('<h3>Habitual Effort Patterns</h3>')
            html.append("<ul>")
            for obs in shadow_obs:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

        if foundation_obs:
            html.append('<h3>What the Foundation Discovered</h3>')
            html.append("<ul>")
            for obs in foundation_obs:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

    html.append("</section>")
    return "\n".join(html)


def render_booklet_lens(framework_id: str, vl_analysis: dict, somatic_narrative: dict = None, groups: list = None) -> str:
    """Render 'The Lens' section — framework intro + LLM-generated Person Inside narrative."""
    intro = VL_BOOKLET_INTROS.get(framework_id, {})
    if not intro:
        return ""

    html = ['<section class="booklet-section">']
    html.append(f'<h2>The Lens: {intro["name"]}</h2>')

    # The Person Inside — static description
    html.append(f'<h3>{intro["person_inside"]}</h3>')
    html.append(f'<p>{intro.get("person_description_derived", "")}</p>')

    # The habit — static description
    habit_label = intro.get("habit_label_derived", "The Blob")
    html.append(f'<h3>{habit_label.title()}</h3>')
    html.append(f'<p>{intro.get("habit_description_derived", "")}</p>')

    # Collect vl_voice and shadow_voice entries from analytical table rows
    result = vl_analysis.get("result", {})
    table = result.get("analytical_table", {})
    rows = table.get("rows", [])

    vl_voice_parts = []
    shadow_voice_parts = []
    for i, row in enumerate(rows, 1):
        vl_v = row.get("vl_voice", "")
        sh_v = row.get("shadow_voice", "") or row.get("striver_shadow", "")
        if vl_v:
            vl_voice_parts.append(f"Stage {i}: {vl_v}")
        if sh_v:
            shadow_voice_parts.append(f"Stage {i}: {sh_v}")

    if vl_voice_parts:
        primary_inquiry = (somatic_narrative or {}).get("primary_inquiry", "")
        context = {
            "framework_name": intro.get("name", ""),
            "person_inside": intro.get("person_inside", ""),
            "person_description": intro.get("person_description_derived", ""),
            "primary_inquiry": primary_inquiry,
            "vl_voices": "\n".join(vl_voice_parts),
            "shadow_voices": "\n".join(shadow_voice_parts) if shadow_voice_parts else "(no shadow observations)",
        }
        html.append('<h3>The Lesson Through This Lens</h3>')
        html.append(booklet_query_block("lens-synthesis", context))

    html.append("</section>")
    return "\n".join(html)


def render_booklet_washing(somatic_narrative: dict) -> str:
    """Render 'What Changed' — washing gradient + threshold of quiet."""
    washing = somatic_narrative.get("washing_gradient", "")
    threshold = somatic_narrative.get("threshold_of_quiet", "")

    if not washing and not threshold:
        return ""

    html = ['<section class="booklet-section">']
    html.append("<h2>What Changed</h2>")
    if washing:
        html.append(f"<p>{washing}</p>")
    if threshold:
        html.append(f"<p>{threshold}</p>")
    html.append("</section>")
    return "\n".join(html)


def render_booklet_reflection(
    steps: list,
    groups: list,
    framework_id: str,
    lesson_title: str,
    somatic_narrative: dict,
) -> str:
    """Render 'Next Time' section as an LLM query block."""
    intro = VL_BOOKLET_INTROS.get(framework_id, {})

    # Gather unique tags across all steps
    all_tags = set()
    for step in steps:
        all_tags.update(step.get("tags", []))

    # Build human-readable tag summary
    tag_labels = [SPIFFER_TRANSLATIONS.get(t, t) for t in sorted(all_tags) if t in SPIFFER_TRANSLATIONS]
    tag_summary = ", ".join(tag_labels) if tag_labels else "varied pedagogical approaches"

    # Build journey summary
    journey_summary = "\n".join([
        f"- Part {g.get('group_number', '?')}: {g.get('label', '')} — {g.get('description', '')}"
        for g in groups
    ])

    # Extract key movement instructions (sample from different parts of lesson)
    key_movements = []
    if steps:
        indices = [0, len(steps)//4, len(steps)//2, 3*len(steps)//4, len(steps)-1]
        seen = set()
        for i in indices:
            if 0 <= i < len(steps):
                instr = steps[i].get("instruction", "")
                if instr and instr not in seen:
                    key_movements.append(instr)
                    seen.add(instr)
    movement_examples = "\n".join(f"- {m}" for m in key_movements[:6])

    context = {
        "lesson_title": lesson_title,
        "framework_name": intro.get("name", framework_id),
        "person_inside": intro.get("person_inside", "Council member"),
        "lens_description": intro.get("lens_derived", ""),
        "primary_inquiry": somatic_narrative.get("primary_inquiry", ""),
        "journey_summary": journey_summary,
        "tag_summary": tag_summary,
        "movement_examples": movement_examples,
    }

    html = ['<section class="booklet-section">']
    html.append("<h2>Next Time</h2>")
    html.append(booklet_query_block("booklet-reflection", context))
    html.append("</section>")
    return "\n".join(html)


def build_booklet_html(
    analysis_result: dict,
    lesson_title: str = "",
    framework: str = "",
    source_url: str = "",
    original_title: str = "",
) -> str:
    """Assemble the complete booklet HTML from cached analysis data."""
    pass_a = analysis_result.get("pass_a_result", {})
    vl_analyses = analysis_result.get("vl_analyses", {})

    # Use the lesson title from analysis or provided title
    title = pass_a.get("lesson_title", "") or original_title or lesson_title or "Untitled Lesson"

    # Get primary framework — explicit override wins, then Pass A classification, then fallback
    framework_id = framework or pass_a.get("primary_framework", "VL-1")

    # Get the VL analysis for the chosen framework
    primary_vl = vl_analyses.get(framework_id, {})
    if primary_vl.get("status") != "complete":
        if framework:
            # Explicit framework requested but not available — don't silently fall back
            pass
        else:
            # Fall back to first complete analysis
            for fid, vl in vl_analyses.items():
                if vl.get("status") == "complete":
                    primary_vl = vl
                    framework_id = fid
                    break

    result = primary_vl.get("result", {})
    somatic_narrative = result.get("somatic_narrative", {})
    negotiation = result.get("negotiation_framework", {})
    table = result.get("analytical_table", {})
    column_headers = table.get("column_headers", {})

    groups = pass_a.get("groups", [])
    steps = pass_a.get("steps", [])
    home_position = pass_a.get("home_position", "")

    # Build step lookup
    pass_a_steps = {s["step_number"]: s for s in steps if "step_number" in s}

    # Assemble sections
    sections = []
    sections.append(render_booklet_cover(title, framework_id, source_url))
    sections.append(render_booklet_person_inside_narrative(framework_id, primary_vl, somatic_narrative))
    if somatic_narrative:
        sections.append(render_booklet_inquiry(somatic_narrative))
    sections.append(render_booklet_concepts(title, framework_id, somatic_narrative, groups))
    if groups and steps:
        sections.append(render_booklet_journey(groups, steps, primary_vl, pass_a_steps, framework_id))
    if somatic_narrative:
        sections.append(render_booklet_washing(somatic_narrative))

    body = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>{title} — Lesson Companion</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400;1,600&family=Inter:wght@400;500;600&display=swap');

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Lora', Georgia, 'Times New Roman', serif;
            max-width: 720px;
            margin: 0 auto;
            padding: 40px 24px;
            line-height: 1.75;
            color: #2c2c2c;
            background: #faf9f7;
        }}

        /* Cover */
        .booklet-cover {{
            text-align: center;
            padding: 80px 20px 60px;
            margin-bottom: 40px;
            border-bottom: 2px solid #d4c5a9;
        }}
        .cover-title {{
            font-size: 2.2em;
            font-weight: 700;
            color: #1a1a1a;
            line-height: 1.2;
            margin-bottom: 16px;
        }}
        .cover-subtitle {{
            font-size: 1.1em;
            color: #777;
            font-style: italic;
            margin-bottom: 10px;
        }}
        .cover-source {{
            margin-bottom: 40px;
        }}
        .cover-source a {{
            color: #4a6741;
            text-decoration: none;
            font-size: 0.9em;
        }}
        .cover-source a:hover {{
            text-decoration: underline;
        }}
        .cover-lens {{
            font-size: 0.95em;
            color: #666;
            line-height: 1.6;
        }}
        .cover-lens strong {{
            font-size: 1.3em;
            color: #4a6741;
            display: block;
            margin-top: 6px;
        }}
        .cover-lens-subtitle {{
            display: block;
            font-size: 0.85em;
            color: #999;
            margin-top: 2px;
        }}

        /* Sections */
        .booklet-section {{
            margin-bottom: 48px;
            padding-bottom: 32px;
            border-bottom: 1px solid #e8e2d9;
        }}
        .booklet-section:last-child {{
            border-bottom: none;
        }}

        h2 {{
            font-family: 'Inter', -apple-system, sans-serif;
            font-size: 1.4em;
            font-weight: 600;
            color: #333;
            margin-bottom: 16px;
            letter-spacing: -0.01em;
        }}
        h3 {{
            font-family: 'Inter', -apple-system, sans-serif;
            font-size: 1.1em;
            font-weight: 600;
            color: #444;
            margin-top: 28px;
            margin-bottom: 10px;
        }}
        h4 {{
            font-family: 'Inter', -apple-system, sans-serif;
            font-size: 0.95em;
            font-weight: 600;
            color: #555;
            margin-top: 20px;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}

        p {{
            margin-bottom: 14px;
        }}

        /* Home position */
        .home-position {{
            padding: 20px 24px;
            background: #f0ede6;
            border-radius: 6px;
            margin-bottom: 16px;
            font-style: italic;
            border-left: 4px solid #b8a88a;
        }}

        /* Primary inquiry */
        .primary-inquiry {{
            font-size: 1.15em;
            font-style: italic;
            padding: 24px 28px;
            margin: 20px 0;
            background: #f5f2ec;
            border-left: 4px solid #4a6741;
            border-radius: 4px;
            color: #333;
        }}

        /* Three states */
        .three-states {{
            margin: 20px 0;
        }}
        .state {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 14px 18px;
            margin: 8px 0;
            border-radius: 6px;
            font-size: 0.95em;
        }}
        .state strong {{
            font-family: 'Inter', sans-serif;
            white-space: nowrap;
            min-width: 110px;
        }}
        .state span {{
            color: #555;
        }}
        .state-domination, .state-bracing {{
            background: #fdf2f2;
            border-left: 4px solid #c0392b;
        }}
        .state-negotiation, .state-softening {{
            background: #fef9f0;
            border-left: 4px solid #e67e22;
        }}
        .state-integration, .state-transmission {{
            background: #f0f9f0;
            border-left: 4px solid #27ae60;
        }}

        /* Journey */
        .journey-group {{
            margin-bottom: 40px;
            padding: 24px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .journey-group h3 {{
            margin-top: 0;
            font-size: 1.15em;
            color: #333;
        }}
        .group-description {{
            color: #666;
            font-style: italic;
            margin-bottom: 16px;
        }}
        .step-list {{
            margin: 12px 0 16px 20px;
        }}
        .step-list li {{
            margin-bottom: 8px;
            line-height: 1.6;
        }}
        .home-badge {{
            display: inline-block;
            font-family: 'Inter', sans-serif;
            font-size: 0.75em;
            padding: 2px 8px;
            background: #e8f4fd;
            color: #2980b9;
            border-radius: 3px;
            font-style: normal;
            vertical-align: middle;
            margin-left: 6px;
        }}
        .spiffer-hints {{
            margin: 12px 0;
            padding: 12px 16px;
            background: #f9f7f3;
            border-radius: 6px;
        }}
        .spiffer-hint {{
            font-size: 0.9em;
            color: #777;
            font-style: italic;
            margin-bottom: 6px;
        }}
        .spiffer-hint:last-child {{
            margin-bottom: 0;
        }}
        .llm-query {{
            margin: 12px 0;
            padding: 12px 16px;
            background: #fef9e7;
            border: 1px dashed #d4ac0d;
            border-radius: 6px;
            font-size: 0.85em;
            color: #856404;
            font-family: monospace;
        }}
        .llm-query .query-prompt {{
            margin: 0;
        }}
        .llm-query-unfilled {{
            margin: 12px 0;
            padding: 10px 16px;
            background: #f5f5f5;
            border: 1px dashed #ccc;
            border-radius: 6px;
            font-size: 0.85em;
            color: #999;
            font-style: italic;
            text-align: center;
        }}
        .llm-filled {{
            margin: 12px 0;
        }}
        .llm-filled p {{
            margin: 0 0 12px;
        }}
        .llm-filled-group {{
            margin: 12px 0;
        }}
        .prompt-link {{
            font-size: 0.75em;
            color: #aaa;
            cursor: pointer;
            font-family: 'Inter', sans-serif;
        }}
        .prompt-link:hover {{
            color: #888;
            text-decoration: underline;
        }}
        .prompt-dialog {{
            max-width: 640px;
            width: 90vw;
            max-height: 80vh;
            padding: 24px;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-family: monospace;
            font-size: 0.82em;
            line-height: 1.6;
            color: #856404;
            background: #fef9e7;
            overflow-y: auto;
        }}
        .prompt-dialog::backdrop {{
            background: rgba(0,0,0,0.3);
        }}
        .prompt-dialog button {{
            margin-top: 16px;
            padding: 6px 20px;
            border: 1px solid #ccc;
            border-radius: 4px;
            background: white;
            cursor: pointer;
            font-size: 0.95em;
        }}
        .prompt-dialog button:hover {{
            background: #f5f5f5;
        }}

        /* Negotiation badge */
        .neg-badge {{
            display: inline-block;
            font-family: 'Inter', sans-serif;
            font-size: 0.8em;
            font-weight: 600;
            padding: 4px 14px;
            border-radius: 14px;
            color: white;
            margin-bottom: 12px;
        }}

        /* Table insights */
        .table-question, .table-goal {{
            font-size: 0.95em;
            margin-bottom: 10px;
        }}

        /* Voice boxes */
        .voice-box {{
            padding: 16px 20px;
            border-radius: 6px;
            margin: 12px 0;
        }}
        .voice-box p {{
            font-style: italic;
            margin-bottom: 0;
            line-height: 1.6;
        }}
        .voice-label {{
            font-family: 'Inter', sans-serif;
            font-size: 0.75em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }}
        .person-inside {{
            background: #f0f7ff;
            border-left: 3px solid #3498db;
        }}
        .person-inside .voice-label {{
            color: #3498db;
        }}
        .shadow-voice {{
            background: #fff5f5;
            border-left: 3px solid #c0392b;
        }}
        .shadow-voice .voice-label {{
            color: #c0392b;
        }}

        /* Arc timeline */
        .arc-timeline {{
            margin: 20px 0;
            padding: 0 20px;
        }}
        .arc-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 0;
            border-left: 2px solid #ddd;
            margin-left: 8px;
            padding-left: 20px;
            position: relative;
        }}
        .arc-dot {{
            position: absolute;
            left: -7px;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            flex-shrink: 0;
        }}
        .arc-label {{
            flex: 1;
            font-size: 0.9em;
        }}
        .arc-state {{
            font-family: 'Inter', sans-serif;
            font-size: 0.8em;
            font-weight: 600;
            text-transform: uppercase;
        }}

        /* Lens */
        .lens-description {{
            font-size: 1.05em;
            color: #444;
            margin-bottom: 20px;
        }}

        /* Reflection */
        .reflection-list {{
            margin: 16px 0 16px 20px;
        }}
        .reflection-list li {{
            margin-bottom: 12px;
        }}
        .closing {{
            margin-top: 24px;
            font-style: italic;
            color: #666;
            text-align: center;
            padding: 20px;
        }}

        ul {{
            margin: 12px 0 12px 20px;
        }}
        li {{
            margin-bottom: 6px;
        }}

        /* Print styles */
        @media print {{
            body {{
                background: white;
                padding: 0;
                max-width: none;
            }}
            .booklet-cover {{
                page-break-after: always;
            }}
            .booklet-section {{
                page-break-inside: avoid;
            }}
            .journey-group {{
                page-break-inside: avoid;
                box-shadow: none;
                border: 1px solid #ddd;
            }}
        }}

        /* Back link */
        .back-link {{
            font-family: 'Inter', sans-serif;
            font-size: 0.85em;
            color: #999;
            text-decoration: none;
            display: inline-block;
            margin-bottom: 20px;
        }}
        .back-link:hover {{
            color: #333;
        }}
    </style>
</head>
<body>
    <a href="/" class="back-link">&larr; All Booklets</a>
    {body}
    <footer style="margin-top:48px;padding-top:12px;border-top:1px solid #e8e2d9;font-family:'Inter',sans-serif;font-size:0.75em;color:#bbb;">
        Generated {datetime.now(timezone.utc).strftime("%b %d, %Y at %I:%M %p")} UTC
    </footer>

</body>
</html>"""

