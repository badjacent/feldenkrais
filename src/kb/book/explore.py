"""Explore feature: session management, exploration rounds, HTML rendering, and routes."""

import asyncio
import json

from fastapi import Form
from fastapi.responses import HTMLResponse, JSONResponse

from llm_config import get_llm
from cache_helpers import get_cache_key, load_cached_analysis

from book.state import _db, DATA_DIR, ANALYSIS_TIMEOUT, get_available_lessons, load_lesson
from book.analysis import _run_analysis


# --- Pass 0b Experiment ---

def load_explore_prompt(round_type: str) -> str:
    """Load exploration prompt (init or round)."""
    filename = "explore_init.md" if round_type == "init" else "explore_round.md"
    path = DATA_DIR / filename
    return path.read_text() if path.exists() else ""


def load_explore_schema() -> dict:
    """Load the exploration output schema."""
    path = DATA_DIR / "explore.schema.json"
    return json.loads(path.read_text()) if path.exists() else {}


def explore_session_key(lesson_cache_key: str) -> str:
    """Get the cache key for an exploration session."""
    return f"{lesson_cache_key}:explore"


def load_explore_session(session_key: str) -> dict:
    """Load exploration session from database cache."""
    if _db:
        cached = _db.get_cache(session_key)
        if cached:
            return cached
    return {"rounds": []}


def save_explore_session(session_key: str, session: dict) -> None:
    """Save exploration session to database cache."""
    if _db:
        _db.set_cache(session_key, session)


def build_explore_context(analysis_result: dict) -> str:
    """Format analysis result as text context for the exploration LLM prompt."""
    parts = []
    pass_a = analysis_result.get("pass_a_result", {})

    # Lesson title
    title = pass_a.get("lesson_title", "Untitled")
    parts.append(f"# Lesson: {title}\n")

    # Classifications
    classifications = pass_a.get("classifications", {})
    primary = pass_a.get("primary_framework", "Unknown")
    secondary = pass_a.get("secondary_frameworks", [])
    parts.append(f"Primary framework: {primary}")
    if secondary:
        parts.append(f"Secondary: {', '.join(secondary)}")
    if classifications:
        scores = ", ".join(f"{k}={v:.2f}" for k, v in classifications.items() if v > 0)
        parts.append(f"Scores: {scores}")
    parts.append("")

    # Home position
    home_pos = pass_a.get("home_position")
    if home_pos:
        parts.append(f"Home position: {home_pos}\n")

    # Groups and steps
    groups = pass_a.get("groups", [])
    steps = pass_a.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps if "step_number" in s}

    if groups:
        for g in groups:
            gnum = g.get("group_number", "?")
            label = g.get("label", "")
            desc = g.get("description", "")
            parts.append(f"## Group {gnum}: {label}")
            if desc:
                parts.append(f"  {desc}")
            for sn in g.get("steps", []):
                step = step_lookup.get(sn, {})
                tags = ", ".join(step.get("tags", []))
                instruction = step.get("instruction", "")
                note = f" ({step['note']})" if step.get("note") else ""
                home = " [HOME]" if step.get("is_home") else ""
                parts.append(f"  Step {sn}: [{tags}] {instruction}{home}{note}")
            parts.append("")
    elif steps:
        for step in steps:
            sn = step.get("step_number", "?")
            tags = ", ".join(step.get("tags", []))
            instruction = step.get("instruction", "")
            parts.append(f"  Step {sn}: [{tags}] {instruction}")
        parts.append("")

    # Rationale
    if pass_a.get("rationale"):
        parts.append(f"Rationale: {pass_a['rationale']}\n")

    # Pass B analyses
    vl_analyses = analysis_result.get("vl_analyses", {})
    for framework, vl_data in vl_analyses.items():
        if vl_data.get("status") != "complete":
            continue
        result = vl_data.get("result", {})
        parts.append(f"## Pass B: {framework}")

        # Somatic narrative
        narrative = result.get("somatic_narrative")
        if narrative:
            parts.append(f"Somatic narrative: {narrative}")

        # Negotiation framework
        neg = result.get("negotiation_framework")
        if neg:
            parts.append(f"Negotiation framework: {json.dumps(neg)}")

        # Table rows (if present)
        table = result.get("table")
        if table:
            parts.append(f"Analysis table: {len(table)} rows")
            for row in table[:5]:  # Just first few to keep context manageable
                parts.append(f"  - {json.dumps(row)}")
            if len(table) > 5:
                parts.append(f"  ... and {len(table) - 5} more rows")
        parts.append("")

    return "\n".join(parts)


def run_explore_round(
    analysis_result: dict,
    session: dict,
    focus: dict | None = None,
    hint: str | None = None,
    provider_key: str | None = None,
) -> dict:
    """Run one exploration round.

    Args:
        analysis_result: The cached analysis (pass_a_result + vl_analyses)
        session: Current exploration session (with previous rounds)
        focus: For round N, the selected focus area {group, step, label}
        hint: Optional user hint/question
        provider_key: LLM provider override

    Returns:
        Round result dict with annotations, suggestions, meta_observation
    """
    import time

    rounds = session.get("rounds", [])
    round_num = len(rounds)

    # Build context from analysis
    context = build_explore_context(analysis_result)

    if round_num == 0:
        # Round 0: survey and suggest
        prompt_template = load_explore_prompt("init")
        prompt = prompt_template + "\n\n---\n\n## Analysis to explore:\n\n" + context
    else:
        # Round N: focused exploration
        prompt_template = load_explore_prompt("round")

        # Build focus description
        focus_desc = ""
        if focus:
            focus_desc = f"Focus on Group {focus.get('group', '?')}"
            if focus.get("step"):
                focus_desc += f", Step {focus['step']}"
            if focus.get("label"):
                focus_desc += f" — {focus['label']}"
        if hint:
            focus_desc += f"\nUser hint: {hint}"

        # Build previous annotations summary
        prev_annotations = []
        for r in rounds:
            for ann in r.get("annotations", []):
                prev_annotations.append(
                    f"[Round {r['round']}] Group {ann.get('anchor_group', '?')}"
                    f"{' Step ' + str(ann['anchor_step']) if ann.get('anchor_step') else ''}"
                    f" ({ann.get('category', '?')}): {ann.get('text', '')}"
                )

        prev_section = ""
        if prev_annotations:
            prev_section = "\n\n## Previous annotations (do not repeat these):\n\n" + "\n".join(prev_annotations)

        prompt = (
            prompt_template
            + "\n\n---\n\n## Analysis:\n\n" + context
            + "\n\n---\n\n## Focus area:\n\n" + focus_desc
            + prev_section
        )

    system = "You are an expert Feldenkrais practitioner exploring a lesson analysis. Be specific, insightful, and non-obvious."
    schema = load_explore_schema()
    llm = get_llm(provider_key)

    t0 = time.time()
    result = llm.complete_structured_json(
        prompt=prompt,
        schema=schema,
        system=system,
    )
    elapsed = round(time.time() - t0, 1)

    # Build round entry
    round_entry = {
        "round": round_num,
        "focus": focus,
        "hint": hint,
        "annotations": result.get("annotations", []),
        "suggestions": result.get("suggestions", []),
        "meta_observation": result.get("meta_observation", ""),
        "elapsed": elapsed,
    }

    return round_entry


ANNOTATION_COLORS = {
    "pedagogical_gap": ("#f0ad4e", "#fff3cd"),
    "hidden_connection": ("#6f42c1", "#f3e8ff"),
    "question": ("#007bff", "#e8f0fe"),
    "reinterpretation": ("#28a745", "#e8f5e9"),
    "missing_voice": ("#fd7e14", "#fff3e0"),
    "contradiction": ("#dc3545", "#fde8e8"),
}


def render_explore_analysis_with_annotations(analysis_result: dict, session: dict) -> str:
    """Render Pass A groups/steps with annotation badges overlaid."""
    pass_a = analysis_result.get("pass_a_result", {})
    groups = pass_a.get("groups", [])
    steps = pass_a.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps if "step_number" in s}

    # Collect all annotations by group and step
    annotations_by_group = {}
    annotations_by_step = {}
    for r in session.get("rounds", []):
        for ann in r.get("annotations", []):
            g = ann.get("anchor_group")
            s = ann.get("anchor_step")
            if g is not None:
                annotations_by_group.setdefault(g, []).append(ann)
            if s is not None:
                annotations_by_step.setdefault(s, []).append(ann)

    def _render_annotation_badges(annotations):
        badges = []
        for ann in annotations:
            cat = ann.get("category", "unknown")
            color, bg = ANNOTATION_COLORS.get(cat, ("#999", "#f0f0f0"))
            conf = ann.get("confidence", 0)
            text = ann.get("text", "")
            # Escape HTML in tooltip
            tooltip = text.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            badges.append(
                f'<span style="display: inline-block; font-size: 0.75em; padding: 2px 8px; '
                f'margin: 2px 3px; border-radius: 12px; background: {bg}; color: {color}; '
                f'border: 1px solid {color}; cursor: help;" '
                f'title="{tooltip}">{cat} ({conf:.1f})</span>'
            )
        return "".join(badges)

    def _render_annotation_detail(annotations):
        if not annotations:
            return ""
        parts = ['<div style="margin: 6px 0 6px 20px;">']
        for ann in annotations:
            cat = ann.get("category", "unknown")
            color, bg = ANNOTATION_COLORS.get(cat, ("#999", "#f0f0f0"))
            conf = ann.get("confidence", 0)
            text = ann.get("text", "")
            parts.append(
                f'<div style="margin: 4px 0; padding: 8px 12px; background: {bg}; '
                f'border-left: 3px solid {color}; border-radius: 4px; font-size: 0.9em;">'
                f'<strong style="color: {color};">{cat}</strong> '
                f'<span style="color: #999; font-size: 0.85em;">({conf:.1f})</span><br>'
                f'{text}</div>'
            )
        parts.append('</div>')
        return "\n".join(parts)

    html = []

    # Lesson title
    title = pass_a.get("lesson_title")
    if title:
        html.append(f"<h3>{title}</h3>")

    # Home position
    home_pos = pass_a.get("home_position")
    if home_pos:
        html.append(f'<p style="padding: 6px 10px; background: #e8f4fd; border-left: 3px solid #17a2b8; border-radius: 4px; font-size: 0.9em;"><strong>Home:</strong> {home_pos}</p>')

    if groups:
        grouped_step_nums = set()
        for g in groups:
            grouped_step_nums.update(g.get("steps", []))

        for g in groups:
            gnum = g.get("group_number", 0)
            label = g.get("label", f"Group {gnum}")
            desc = g.get("description", "")
            group_anns = annotations_by_group.get(gnum, [])
            # Only group-level annotations (no specific step)
            group_only_anns = [a for a in group_anns if not a.get("anchor_step")]

            html.append(f'<details open style="margin-bottom: 10px;" id="group-{gnum}">')
            html.append(
                f'<summary style="cursor: pointer; font-weight: bold; padding: 6px 0;">'
                f'{gnum}. {label}'
                f'{_render_annotation_badges(group_only_anns)}'
                f'</summary>'
            )
            if desc:
                html.append(f'<p style="margin: 2px 0 6px 20px; color: #666; font-size: 0.9em;">{desc}</p>')

            # Group-level annotation details
            if group_only_anns:
                html.append(_render_annotation_detail(group_only_anns))

            html.append('<ol style="margin-left: 20px;">')
            for sn in g.get("steps", []):
                step = step_lookup.get(sn)
                if not step:
                    continue
                tags = ", ".join(step.get("tags", []))
                instruction = step.get("instruction", "")
                home = ' <span style="font-size: 0.8em; padding: 1px 6px; background: #e8f4fd; color: #17a2b8; border-radius: 3px;">home</span>' if step.get("is_home") else ""
                step_anns = annotations_by_step.get(sn, [])

                html.append(f'<li id="step-{sn}"><strong>[{tags}]</strong> {instruction}{home}')
                if step_anns:
                    html.append(_render_annotation_badges(step_anns))
                    html.append(_render_annotation_detail(step_anns))
                html.append('</li>')
            html.append('</ol>')
            html.append('</details>')
    elif steps:
        html.append('<ol>')
        for step in steps:
            sn = step.get("step_number", 0)
            tags = ", ".join(step.get("tags", []))
            instruction = step.get("instruction", "")
            step_anns = annotations_by_step.get(sn, [])
            html.append(f'<li><strong>[{tags}]</strong> {instruction}')
            if step_anns:
                html.append(_render_annotation_badges(step_anns))
                html.append(_render_annotation_detail(step_anns))
            html.append('</li>')
        html.append('</ol>')

    return "\n".join(html)


def render_explore_panel(session: dict) -> str:
    """Render the exploration panel (suggestions, round history)."""
    rounds = session.get("rounds", [])
    html = []

    if not rounds:
        html.append('<p style="color: #666;">Load a lesson to begin exploration.</p>')
        return "\n".join(html)

    latest = rounds[-1]

    # Meta observation from latest round
    meta = latest.get("meta_observation", "")
    if meta:
        html.append(f'<div style="padding: 10px; background: #f8f9fa; border-radius: 6px; margin-bottom: 12px; font-style: italic; color: #555;">{meta}</div>')

    # Current suggestions
    suggestions = latest.get("suggestions", [])
    if suggestions:
        html.append('<h4 style="margin-bottom: 8px;">Suggested explorations:</h4>')
        for i, s in enumerate(suggestions):
            group = s.get("group", "?")
            step = s.get("step")
            label = s.get("label", "")
            why = s.get("why", "")
            step_str = f", Step {step}" if step else ""
            html.append(
                f'<div class="suggestion-card" data-group="{group}" data-step="{step or ""}" '
                f'data-label="{label}" '
                f'onclick="selectSuggestion(this)" '
                f'style="padding: 10px; margin-bottom: 8px; border: 1px solid #ddd; border-radius: 6px; '
                f'cursor: pointer; transition: border-color 0.2s;"'
                f'onmouseover="this.style.borderColor=\'#007bff\'" '
                f'onmouseout="if(!this.classList.contains(\'selected\'))this.style.borderColor=\'#ddd\'">'
                f'<strong>Group {group}{step_str}:</strong> {label}'
                f'<div style="font-size: 0.85em; color: #666; margin-top: 4px;">{why}</div>'
                f'</div>'
            )

    # Round history
    if len(rounds) > 1 or (len(rounds) == 1 and rounds[0].get("annotations")):
        html.append('<h4 style="margin-top: 16px; margin-bottom: 8px;">Round history:</h4>')
        for r in reversed(rounds):
            rnum = r.get("round", 0)
            focus = r.get("focus")
            annotations = r.get("annotations", [])
            elapsed = r.get("elapsed", "?")

            focus_str = "Survey" if focus is None else f"Group {focus.get('group', '?')}"
            if focus and focus.get("label"):
                focus_str += f" — {focus['label']}"

            html.append(f'<details style="margin-bottom: 6px;">')
            html.append(
                f'<summary style="cursor: pointer; font-size: 0.9em; color: #555;">'
                f'Round {rnum}: {focus_str} '
                f'<span style="color: #999;">({len(annotations)} annotations, {elapsed}s)</span>'
                f'</summary>'
            )
            if r.get("meta_observation"):
                html.append(f'<p style="font-style: italic; color: #666; font-size: 0.85em; margin: 4px 0 4px 16px;">{r["meta_observation"]}</p>')
            for ann in annotations:
                cat = ann.get("category", "?")
                color, bg = ANNOTATION_COLORS.get(cat, ("#999", "#f0f0f0"))
                g = ann.get("anchor_group", "?")
                s = ann.get("anchor_step")
                anchor = f"G{g}" + (f"/S{s}" if s else "")
                html.append(
                    f'<div style="margin: 3px 0 3px 16px; padding: 6px 10px; background: {bg}; '
                    f'border-left: 3px solid {color}; border-radius: 4px; font-size: 0.85em;">'
                    f'<strong style="color: {color};">[{anchor}] {cat}</strong> '
                    f'({ann.get("confidence", 0):.1f})<br>{ann.get("text", "")}</div>'
                )
            html.append('</details>')

    return "\n".join(html)


def build_explore_html() -> str:
    """Build the /explore page HTML."""
    categories, display_names = get_available_lessons()

    category_options = ['<option value="">-- Select category --</option>']
    for cat_name in categories.keys():
        cat_display = display_names.get(cat_name, cat_name.replace("_", " ").title())
        category_options.append(f'<option value="{cat_name}">{cat_display}</option>')
    category_select = "\n".join(category_options)

    lessons_json = json.dumps({
        cat: [(t[0], t[1]) for t in lessons]
        for cat, lessons in categories.items()
    })

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Explore - Feldenkrais Lesson Analysis</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; margin-bottom: 5px; }}
        .top-bar {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
            margin-bottom: 15px;
            padding-bottom: 15px;
            border-bottom: 1px solid #eee;
        }}
        .top-bar select, .top-bar button {{
            padding: 8px 12px;
            font-size: 14px;
            border: 1px solid #ccc;
            border-radius: 4px;
        }}
        .top-bar button {{
            background: #007bff;
            color: white;
            cursor: pointer;
            border: none;
        }}
        .top-bar button:hover {{ background: #0056b3; }}
        .top-bar button:disabled {{ background: #ccc; cursor: not-allowed; }}
        .columns {{
            display: flex;
            gap: 20px;
        }}
        .col-left {{
            flex: 6;
            min-width: 0;
        }}
        .col-right {{
            flex: 4;
            min-width: 300px;
        }}
        .panel {{
            background: #f8f9fa;
            border-radius: 8px;
            padding: 15px;
            position: sticky;
            top: 20px;
            max-height: calc(100vh - 60px);
            overflow-y: auto;
        }}
        .hint-area {{
            margin-top: 12px;
        }}
        .hint-area textarea {{
            width: 100%;
            height: 60px;
            font-size: 13px;
            padding: 8px;
            border: 1px solid #ccc;
            border-radius: 4px;
            resize: vertical;
        }}
        .explore-btn {{
            display: block;
            width: 100%;
            padding: 10px;
            margin-top: 8px;
            background: #28a745;
            color: white;
            border: none;
            border-radius: 4px;
            font-size: 15px;
            cursor: pointer;
        }}
        .explore-btn:hover {{ background: #218838; }}
        .explore-btn:disabled {{ background: #ccc; cursor: not-allowed; }}
        .suggestion-card.selected {{
            border-color: #007bff !important;
            background: #e8f0fe;
        }}
        .loading {{
            text-align: center;
            padding: 20px;
            color: #666;
        }}
        .loading .spinner {{
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid #ddd;
            border-top-color: #007bff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            vertical-align: middle;
            margin-right: 8px;
        }}
        @keyframes spin {{
            to {{ transform: rotate(360deg); }}
        }}
        .error {{ color: red; padding: 10px; background: #fee; border-radius: 4px; }}
    </style>
    <script>
        const lessonsData = {lessons_json};
        let sessionKey = null;
        let selectedFocus = null;

        function onCategoryChange(select) {{
            const category = select.value;
            const lessonSelect = document.getElementById('selected_lesson');
            lessonSelect.innerHTML = '<option value="">-- Select lesson --</option>';
            if (category && lessonsData[category]) {{
                lessonsData[category].forEach(([filename, display]) => {{
                    const option = document.createElement('option');
                    option.value = filename;
                    option.textContent = display;
                    lessonSelect.appendChild(option);
                }});
            }}
        }}

        function selectSuggestion(card) {{
            document.querySelectorAll('.suggestion-card').forEach(c => {{
                c.classList.remove('selected');
                c.style.borderColor = '#ddd';
            }});
            card.classList.add('selected');
            card.style.borderColor = '#007bff';
            selectedFocus = {{
                group: parseInt(card.dataset.group),
                step: card.dataset.step ? parseInt(card.dataset.step) : null,
                label: card.dataset.label
            }};
        }}

        async function loadLesson() {{
            const category = document.getElementById('selected_category').value;
            const lesson = document.getElementById('selected_lesson').value;
            if (!category || !lesson) return;

            const loadBtn = document.getElementById('load-btn');
            const analysisDiv = document.getElementById('analysis');
            const panelDiv = document.getElementById('panel');

            loadBtn.disabled = true;
            loadBtn.textContent = 'Loading...';
            analysisDiv.innerHTML = '<div class="loading"><span class="spinner"></span> Loading analysis and running initial survey...</div>';
            panelDiv.innerHTML = '';

            try {{
                const formData = new FormData();
                formData.append('selected_category', category);
                formData.append('selected_lesson', lesson);

                const response = await fetch('/api/explore/load', {{
                    method: 'POST',
                    body: formData
                }});
                const data = await response.json();

                if (data.error) {{
                    analysisDiv.innerHTML = '<div class="error">' + data.error + '</div>';
                    loadBtn.disabled = false;
                    loadBtn.textContent = 'Load & Explore';
                    return;
                }}

                sessionKey = data.session_key;
                analysisDiv.innerHTML = data.analysis_html || '';
                panelDiv.innerHTML = data.panel_html || '';
            }} catch (error) {{
                analysisDiv.innerHTML = '<div class="error">Error: ' + error.message + '</div>';
            }}
            loadBtn.disabled = false;
            loadBtn.textContent = 'Load & Explore';
        }}

        async function runRound() {{
            if (!sessionKey) return;

            const hint = document.getElementById('hint-text').value.trim();
            const exploreBtn = document.getElementById('explore-btn');
            const panelDiv = document.getElementById('panel');
            const analysisDiv = document.getElementById('analysis');

            if (!selectedFocus && !hint) {{
                alert('Select a suggestion or enter a hint to explore.');
                return;
            }}

            exploreBtn.disabled = true;
            exploreBtn.textContent = 'Exploring...';

            // Add loading indicator
            const loadingDiv = document.createElement('div');
            loadingDiv.className = 'loading';
            loadingDiv.id = 'round-loading';
            loadingDiv.innerHTML = '<span class="spinner"></span> Running exploration round...';
            panelDiv.prepend(loadingDiv);

            try {{
                const formData = new FormData();
                formData.append('session_key', sessionKey);
                if (selectedFocus) {{
                    formData.append('focus_group', selectedFocus.group);
                    if (selectedFocus.step) formData.append('focus_step', selectedFocus.step);
                    if (selectedFocus.label) formData.append('focus_label', selectedFocus.label);
                }}
                if (hint) formData.append('hint', hint);

                const response = await fetch('/api/explore/round', {{
                    method: 'POST',
                    body: formData
                }});
                const data = await response.json();

                const loading = document.getElementById('round-loading');
                if (loading) loading.remove();

                if (data.error) {{
                    panelDiv.innerHTML = '<div class="error">' + data.error + '</div>' + panelDiv.innerHTML;
                    exploreBtn.disabled = false;
                    exploreBtn.textContent = 'Explore Selected Area';
                    return;
                }}

                // Update both panels
                analysisDiv.innerHTML = data.analysis_html || analysisDiv.innerHTML;
                panelDiv.innerHTML = data.panel_html || panelDiv.innerHTML;
                selectedFocus = null;
                document.getElementById('hint-text').value = '';
            }} catch (error) {{
                const loading = document.getElementById('round-loading');
                if (loading) loading.remove();
                panelDiv.innerHTML = '<div class="error">Error: ' + error.message + '</div>' + panelDiv.innerHTML;
            }}
            exploreBtn.disabled = false;
            exploreBtn.textContent = 'Explore Selected Area';
        }}
    </script>
</head>
<body>
    <h1>Exploration Agent</h1>
    <p style="color: #666; margin-top: 0;">
        Iteratively explore a lesson analysis. The agent suggests areas of interest; you choose where to dig deeper.
        <a href="/analyzer" style="float: right; color: #007bff; text-decoration: none; font-size: 0.9em;">&larr; Main Analyzer</a>
    </p>

    <div class="top-bar">
        <select id="selected_category" onchange="onCategoryChange(this)">
            {category_select}
        </select>
        <select id="selected_lesson">
            <option value="">-- Select lesson --</option>
        </select>
        <button id="load-btn" onclick="loadLesson()">Load &amp; Explore</button>
    </div>

    <div class="columns">
        <div class="col-left" id="analysis">
            <p style="color: #999;">Select a lesson and click "Load & Explore" to begin.</p>
        </div>
        <div class="col-right">
            <div class="panel" id="panel">
                <p style="color: #999;">Exploration panel will appear here.</p>
            </div>
            <div class="hint-area">
                <textarea id="hint-text" placeholder="Optional: add your own question or direction..."></textarea>
                <button class="explore-btn" id="explore-btn" onclick="runRound()">Explore Selected Area</button>
            </div>
        </div>
    </div>
</body>
</html>
"""


async def explore_page():
    """Exploration agent page."""
    return build_explore_html()


async def api_explore_load(
    selected_category: str = Form(""),
    selected_lesson: str = Form(""),
):
    """Load lesson analysis and run Round 0 (survey + suggest)."""
    text = load_lesson(selected_category, selected_lesson).strip()
    if not text:
        return JSONResponse({"error": "No lesson text found."}, status_code=400)

    try:
        # Load or run the analysis
        analysis_result = await asyncio.wait_for(
            asyncio.to_thread(_run_analysis, text),
            timeout=ANALYSIS_TIMEOUT,
        )

        # Get cache key for the analysis (same as _run_analysis uses)
        lesson_cache_key = get_cache_key(text)
        session_key = explore_session_key(lesson_cache_key)

        # Load existing session or start fresh
        session = load_explore_session(session_key)

        if not session.get("rounds"):
            # Run Round 0
            round_result = await asyncio.wait_for(
                asyncio.to_thread(run_explore_round, analysis_result, session),
                timeout=ANALYSIS_TIMEOUT,
            )
            session["rounds"].append(round_result)
            session["lesson_cache_key"] = lesson_cache_key
            save_explore_session(session_key, session)

        analysis_html = render_explore_analysis_with_annotations(analysis_result, session)
        panel_html = render_explore_panel(session)

        return JSONResponse({
            "session_key": session_key,
            "analysis_html": analysis_html,
            "panel_html": panel_html,
            "round_count": len(session["rounds"]),
        })

    except asyncio.TimeoutError:
        return JSONResponse({"error": "Analysis timed out."}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_explore_round(
    session_key: str = Form(""),
    focus_group: str = Form(""),
    focus_step: str = Form(""),
    focus_label: str = Form(""),
    hint: str = Form(""),
):
    """Run one exploration round with selected focus."""
    if not session_key:
        return JSONResponse({"error": "No session key."}, status_code=400)

    session = load_explore_session(session_key)
    if not session.get("rounds"):
        return JSONResponse({"error": "Session not initialized. Load a lesson first."}, status_code=400)

    # Reconstruct analysis from cache
    lesson_cache_key = session.get("lesson_cache_key", session_key.replace(":explore", ""))
    cached_analysis = load_cached_analysis(lesson_cache_key)
    if not cached_analysis:
        return JSONResponse({"error": "Cached analysis not found. Re-load the lesson."}, status_code=400)

    # Build focus dict
    focus = None
    if focus_group:
        focus = {"group": int(focus_group)}
        if focus_step:
            focus["step"] = int(focus_step)
        if focus_label:
            focus["label"] = focus_label

    hint_text = hint.strip() if hint else None

    try:
        round_result = await asyncio.wait_for(
            asyncio.to_thread(run_explore_round, cached_analysis, session, focus, hint_text),
            timeout=ANALYSIS_TIMEOUT,
        )
        session["rounds"].append(round_result)
        save_explore_session(session_key, session)

        analysis_html = render_explore_analysis_with_annotations(cached_analysis, session)
        panel_html = render_explore_panel(session)

        return JSONResponse({
            "analysis_html": analysis_html,
            "panel_html": panel_html,
            "round_count": len(session["rounds"]),
            "new_annotations": len(round_result.get("annotations", [])),
            "elapsed": round_result.get("elapsed"),
        })

    except asyncio.TimeoutError:
        return JSONResponse({"error": "Exploration round timed out."}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

