"""HTML builder functions for the /analyzer interface.

Includes render_router_result, render_multi_vl_analyses, render_pass_b_analysis,
render_analysis, build_html, and build_result_html.
"""

import json
import re

from analysis import get_active_frameworks_config, _derive_key

from book.state import format_timestamp, get_available_lessons, FRAMEWORK_VERSION
from book.analysis import (
    get_available_framework_versions,
    get_vl_booklet_intros,
    load_pass_b,
)


def render_router_result(router_result: dict) -> str:
    """Render router classification as HTML."""
    if not router_result:
        return ""

    html = ['<div class="router-result" style="background: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">']
    html.append("<h2>Framework Classification</h2>")

    # Primary framework
    primary = router_result.get("primary_framework", "Unknown")
    html.append(f'<p><strong>Primary Framework:</strong> <span style="font-size: 1.2em; color: #007bff;">{primary}</span></p>')

    # Secondary frameworks
    secondary = router_result.get("secondary_frameworks", [])
    if secondary:
        html.append(f'<p><strong>Secondary:</strong> {", ".join(secondary)}</p>')

    # Classification scores as a simple bar chart
    classifications = router_result.get("classifications", {})
    if classifications:
        html.append('<div style="margin: 15px 0;">')
        framework_names = {_derive_key(f): f.get("display_name", f"{f['id']} {f['name']}") for f in get_active_frameworks_config()}
        for key, name in framework_names.items():
            score = classifications.get(key, 0)
            pct = int(score * 100)
            color = "#007bff" if score >= 0.6 else "#6c757d" if score >= 0.3 else "#dee2e6"
            html.append(f'''
                <div style="margin: 5px 0;">
                    <span style="display: inline-block; width: 150px;">{name}</span>
                    <span style="display: inline-block; width: 200px; background: #eee; border-radius: 4px;">
                        <span style="display: inline-block; width: {pct}%; background: {color}; height: 20px; border-radius: 4px;"></span>
                    </span>
                    <span style="margin-left: 10px;">{score:.2f}</span>
                </div>
            ''')
        html.append('</div>')

    # Rationale
    if router_result.get("rationale"):
        html.append(f'<p><strong>Rationale:</strong> {router_result["rationale"]}</p>')

    # Key indicators (from router)
    indicators = router_result.get("key_indicators", [])
    if indicators:
        html.append("<details><summary><strong>Key Indicators</strong></summary><ul>")
        for ind in indicators:
            framework = ind.get("framework", "")
            indicator = ind.get("indicator", "")
            html.append(f"<li><strong>{framework}:</strong> {indicator}</li>")
        html.append("</ul></details>")

    html.append("</div>")

    # SPIFFER-Q Decomposition (Pass A output)
    steps = router_result.get("steps", [])
    groups = router_result.get("groups", [])
    if steps:
        html.append('<div class="spiffer-q-steps" style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-bottom: 20px;">')

        # Lesson title if present
        if router_result.get("lesson_title"):
            html.append(f"<h2>{router_result['lesson_title']}</h2>")

        html.append("<h3>SPIFFER-Q Decomposition (Pass A)</h3>")

        # Home position
        home_pos = router_result.get("home_position")
        if home_pos:
            html.append(f'<p style="margin-bottom: 12px; padding: 8px 12px; background: #e8f4fd; border-left: 3px solid #17a2b8; border-radius: 4px;"><strong>Home position:</strong> {home_pos}</p>')

        # Build segment lookup from Pass 0
        pass_0 = router_result.get("pass_0", {})
        segments_list = pass_0.get("segments", [])
        segment_lookup = {seg["id"]: seg["text"] for seg in segments_list}

        # Build step lookup by step_number
        step_lookup = {s["step_number"]: s for s in steps if "step_number" in s}

        def _render_step(step):
            """Render a single step as an <li> with tags, instruction, and individual source segments."""
            tags = ", ".join(step.get("tags", []))
            instruction = step.get("instruction", "")
            note = f' <small style="color: #666;">({step["note"]})</small>' if step.get("note") else ""
            home_badge = ' <span style="font-size: 0.8em; padding: 1px 6px; background: #e8f4fd; color: #17a2b8; border-radius: 3px;">home</span>' if step.get("is_home") else ""

            segment_ids = step.get("segments", [])
            parts = []
            parts.append(f"<li><strong>[{tags}]</strong> {instruction}{home_badge}{note}")

            # Show individual source segments
            if segment_ids:
                parts.append('<details style="margin-top: 6px; margin-left: 20px;">')
                parts.append(f'<summary style="cursor: pointer; color: #666; font-size: 0.9em;">Source text ({len(segment_ids)} segment{"s" if len(segment_ids) != 1 else ""})</summary>')
                parts.append('<div style="margin: 6px 0;">')
                for sid in segment_ids:
                    seg_text = segment_lookup.get(sid, f"[segment {sid} not found]")
                    parts.append(f'<div style="margin: 4px 0; padding: 6px 10px; background: #fff; border-left: 3px solid #007bff; font-style: italic; color: #555; font-size: 0.9em;">'
                                 f'<span style="color: #999; font-size: 0.8em; font-style: normal;">#{sid}</span> {seg_text}</div>')
                parts.append('</div></details>')

            parts.append("</li>")
            return "\n".join(parts)

        if groups:
            # Three-level tree: Group > Steps > Segments
            # Track which steps are assigned to groups
            grouped_step_nums = set()
            for g in groups:
                grouped_step_nums.update(g.get("steps", []))

            for g in groups:
                group_label = g.get("label", f"Group {g.get('group_number', '?')}")
                group_desc = g.get("description", "")
                group_step_nums = g.get("steps", [])

                html.append('<details open style="margin-bottom: 12px;">')
                html.append(f'<summary style="cursor: pointer; font-weight: bold; font-size: 1.05em; padding: 6px 0;">'
                            f'{g.get("group_number", "?")}. {group_label}</summary>')
                if group_desc:
                    html.append(f'<p style="margin: 4px 0 8px 20px; color: #555; font-size: 0.9em;">{group_desc}</p>')

                html.append('<ol style="margin-left: 20px;">')
                for sn in group_step_nums:
                    step = step_lookup.get(sn)
                    if step:
                        html.append(_render_step(step))
                html.append('</ol>')
                html.append('</details>')

            # Render any ungrouped steps
            ungrouped = [s for s in steps if s.get("step_number") not in grouped_step_nums]
            if ungrouped:
                html.append('<details style="margin-bottom: 12px;">')
                html.append('<summary style="cursor: pointer; font-weight: bold; font-size: 1.05em; padding: 6px 0;">Ungrouped Steps</summary>')
                html.append('<ol style="margin-left: 20px;">')
                for step in ungrouped:
                    html.append(_render_step(step))
                html.append('</ol>')
                html.append('</details>')
        else:
            # Fallback: flat list (no groups available)
            html.append("<ol>")
            for step in steps:
                html.append(_render_step(step))
            html.append("</ol>")

        html.append("</div>")

    return "\n".join(html)


def render_multi_vl_analyses(vl_analyses: dict, schema: dict, pass_a_result: dict = None) -> str:
    """Render multiple VL analyses with tabs for switching between them."""
    if not vl_analyses:
        return ""

    frameworks = list(vl_analyses.keys())

    if len(frameworks) == 1:
        # Single framework, no tabs needed
        framework = frameworks[0]
        entry = vl_analyses[framework]
        if entry.get("status") == "complete" and entry.get("result"):
            return f'<h2>{framework} Analysis</h2>' + render_analysis(entry["result"], schema, pass_a_result)
        elif entry.get("status") == "error":
            return f'<div class="error"><strong>{framework}:</strong> {entry.get("error", "Unknown error")}</div>'
        return ""

    # Multiple frameworks - use tabs
    html = []

    # Tab buttons
    html.append('<div class="vl-tabs" style="margin-bottom: 20px;">')
    for i, framework in enumerate(frameworks):
        active = "background: #007bff; color: white;" if i == 0 else "background: #e9ecef; color: #333;"
        html.append(f'''
            <button onclick="showVLTab('{framework}')" id="tab-{framework}"
                    style="padding: 10px 20px; border: none; cursor: pointer; {active} border-radius: 4px 4px 0 0; margin-right: 2px;">
                {framework}
            </button>
        ''')
    html.append('</div>')

    # Tab content
    for i, framework in enumerate(frameworks):
        display = "block" if i == 0 else "none"
        entry = vl_analyses[framework]

        html.append(f'<div id="content-{framework}" class="vl-content" style="display: {display};">')
        html.append(f'<h2>{framework} Analysis</h2>')

        if entry.get("status") == "complete" and entry.get("result"):
            html.append(render_analysis(entry["result"], schema, pass_a_result))
        elif entry.get("status") == "error":
            html.append(f'<div class="error">Error: {entry.get("error", "Unknown error")}</div>')
        else:
            html.append('<p>No analysis available.</p>')

        html.append('</div>')

    # Note: Tab switching JavaScript is in the main page template (build_html)

    return "\n".join(html)


def render_pass_b_analysis(data: dict, pass_a_result: dict = None) -> str:
    """Render Pass B analysis result as HTML."""
    html = []

    framework = data.get("framework", "")
    if framework:
        html.append(f'<p><em>Analysis through {framework} lens</em></p>')

    # Negotiation Framework
    neg = data.get("negotiation_framework", {})
    if neg:
        html.append("<h3>Negotiation Framework</h3>")

        if neg.get("shadow_observations"):
            html.append("<h4>Habitual Effort</h4><ul>")
            for obs in neg["shadow_observations"]:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

        if neg.get("foundation_observations"):
            html.append("<h4>The Foundation (Support System)</h4><ul>")
            for obs in neg["foundation_observations"]:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

        if neg.get("washing_gradient"):
            html.append(f"<h4>Washing Gradient</h4><p>{neg['washing_gradient']}</p>")

    # Somatic Narrative
    narrative = data.get("somatic_narrative", {})
    if narrative:
        html.append("<h3>Somatic Narrative</h3>")

        if narrative.get("primary_inquiry"):
            html.append(f"<h4>Primary Inquiry</h4><p>{narrative['primary_inquiry']}</p>")

        if narrative.get("washing_gradient"):
            html.append(f"<h4>Washing Gradient</h4><p>{narrative['washing_gradient']}</p>")

        if narrative.get("threshold_of_quiet"):
            html.append(f"<h4>Threshold of Quiet</h4><p>{narrative['threshold_of_quiet']}</p>")

    # Analytical Table / Card View
    table = data.get("analytical_table", {})
    if table:
        rows = table.get("rows", [])
        column_headers = table.get("column_headers", {})

        if rows:
            # Determine columns
            if column_headers:
                columns = sorted([k for k in column_headers.keys() if column_headers.get(k)],
                                key=lambda x: int(x.replace("column", "")) if x.startswith("column") else 999)
                display_names = {col: column_headers[col] for col in columns}
            else:
                columns = ["column1", "column2", "column3", "column4", "column5"]
                display_names = {c: c for c in columns}

            # Build Pass A step lookup and segment lookup
            pass_a_steps = {}
            segment_lookup = {}
            segment_data = {}
            if pass_a_result:
                for step in pass_a_result.get("steps", []):
                    pass_a_steps[step["step_number"]] = step
                pass_0 = pass_a_result.get("pass_0", {})
                for seg in pass_0.get("segments", []):
                    segment_lookup[seg["id"]] = seg["text"]
                    segment_data[seg["id"]] = seg

            # Tab control: Card View (default) / Table View
            html.append(f'<h3>Analytical Table ({len(rows)} rows)</h3>')
            html.append('''
                <div style="margin-bottom: 10px;">
                    <button onclick="showAnalyticalView('cards')" id="btn-cards"
                            style="padding: 6px 16px; border: none; cursor: pointer; background: #007bff; color: white; border-radius: 4px 4px 0 0; margin-right: 2px;">
                        Card View
                    </button>
                    <button onclick="showAnalyticalView('table')" id="btn-table"
                            style="padding: 6px 16px; border: none; cursor: pointer; background: #e9ecef; color: #333; border-radius: 4px 4px 0 0;">
                        Table View
                    </button>
                </div>
            ''')

            # === TABLE VIEW (hidden by default) ===
            html.append('<div id="analytical-table-view" style="display: none;">')
            html.append("""
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
                <thead style="background-color: #f0f0f0;">
                    <tr>
            """)
            for col in columns:
                html.append(f"<th>{display_names.get(col, col)}</th>")
            html.append("</tr></thead><tbody>")

            for row in rows:
                html.append("<tr>")
                for col in columns:
                    val = row.get(col, "")
                    if isinstance(val, list):
                        val = ", ".join(str(v) for v in val)
                    # Add timestamp annotation to Seq column
                    if col == "column1" and pass_a_steps and segment_data and val:
                        row_steps = _parse_seq_to_steps(str(val), pass_a_steps)
                        if row_steps:
                            sids = [sid for step in row_steps for sid in step.get("segments", [])]
                            ts = [segment_data[s].get("time_start") for s in sids if s in segment_data and "time_start" in segment_data[s]]
                            te = [segment_data[s].get("time_end") for s in sids if s in segment_data and "time_end" in segment_data[s]]
                            if ts and te:
                                time_str = f"{format_timestamp(min(ts))} \u2013 {format_timestamp(max(te))}"
                                val = f'{val}<br><small style="color: #007bff;">{time_str}</small>'
                    html.append(f"<td>{val}</td>")
                html.append("</tr>")

                # Add voice/shadow row if present
                vl_voice = row.get("vl_voice", "")
                shadow_voice = row.get("shadow_voice", "") or row.get("striver_shadow", "")
                if vl_voice or shadow_voice:
                    html.append(f'<tr><td colspan="{len(columns)}" style="background: #fafafa; padding: 8px 12px; font-size: 0.9em;">')
                    if vl_voice:
                        html.append(f'<div style="margin-bottom: 6px;"><span style="color: #007bff; font-weight: bold;">Person Inside:</span> <em>{vl_voice}</em></div>')
                    if shadow_voice:
                        html.append(f'<div><span style="color: #dc3545; font-weight: bold;">Habit:</span> <em>{shadow_voice}</em></div>')
                    html.append('</td></tr>')

            html.append("</tbody></table></div>")

            # === CARD VIEW (shown by default) ===
            html.append('<div id="analytical-card-view">')

            # Navigation
            nav_html = f'''
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 15px;">
                    <button onclick="prevCard()" id="card-prev-btn"
                            style="padding: 10px 20px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; background: white; font-size: 1em;">
                        \u2190 Previous
                    </button>
                    <span id="card-counter" style="font-weight: bold; color: #555; font-size: 1.1em;">1 / {len(rows)}</span>
                    <button onclick="nextCard()" id="card-next-btn"
                            style="padding: 10px 20px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; background: white; font-size: 1em;">
                        Next \u2192
                    </button>
                </div>
            '''
            html.append(nav_html)

            # Render each card
            for i, row in enumerate(rows):
                display = "block" if i == 0 else "none"
                html.append(f'<div class="analytical-card" data-card-index="{i}" style="display: {display}; border: 1px solid #ddd; border-radius: 8px; padding: 20px; background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">')

                # Card header - Seq, Negotiation State, and Timestamp
                seq_val = row.get("column1", "")

                # Compute matched steps and card timestamp early
                matched_steps = _parse_seq_to_steps(seq_val, pass_a_steps) if pass_a_steps and seq_val else []
                card_time_str = ""
                if matched_steps and segment_data:
                    _sids = [sid for step in matched_steps for sid in step.get("segments", [])]
                    _ts = [segment_data[s].get("time_start") for s in _sids if s in segment_data and "time_start" in segment_data[s]]
                    _te = [segment_data[s].get("time_end") for s in _sids if s in segment_data and "time_end" in segment_data[s]]
                    if _ts and _te:
                        card_time_str = f"{format_timestamp(min(_ts))} \u2013 {format_timestamp(max(_te))}"

                neg_state = ""
                # Find the negotiation state / what's happening column (typically column4)
                for col in columns:
                    header = display_names.get(col, "").lower()
                    if "negotiation" in header or "state" in header or "happening" in header:
                        neg_state = row.get(col, "")
                        break

                state_colors = {
                    "domination": "#dc3545",
                    "negotiation": "#fd7e14",
                    "integration": "#28a745",
                }
                state_color = state_colors.get(neg_state.lower(), "#6c757d") if neg_state else "#6c757d"

                time_html = f'<span style="font-size: 0.85em; color: #007bff; margin-left: 10px;">{card_time_str}</span>' if card_time_str else ''
                html.append(f'''
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <div>
                            <span style="font-size: 0.9em; color: #999;">Steps {seq_val}</span>
                            {time_html}
                        </div>
                        <span style="padding: 4px 12px; border-radius: 12px; font-size: 0.85em; font-weight: bold; color: white; background: {state_color};">{neg_state}</span>
                    </div>
                ''')

                # Main content - the row columns (skip seq/column1 and negotiation state)
                for col in columns:
                    header = display_names.get(col, col)
                    val = row.get(col, "")
                    if isinstance(val, list):
                        val = ", ".join(str(v) for v in val)
                    if not val or col == "column1":
                        continue
                    header_lower = header.lower()
                    if "negotiation" in header_lower or "state" in header_lower or "happening" in header_lower:
                        continue  # already shown as badge
                    html.append(f'''
                        <div style="margin-bottom: 12px;">
                            <div style="font-size: 0.8em; font-weight: bold; color: #888; text-transform: uppercase; margin-bottom: 4px;">{header}</div>
                            <div>{val}</div>
                        </div>
                    ''')

                # VL Voice and Shadow Voice
                vl_voice = row.get("vl_voice", "")
                shadow_voice = row.get("shadow_voice", "") or row.get("striver_shadow", "")
                if vl_voice or shadow_voice:
                    html.append('<div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #eee;">')
                    if vl_voice:
                        html.append(f'''
                            <div style="margin-bottom: 12px; padding: 12px 16px; background: #f0f7ff; border-radius: 6px; border-left: 3px solid #007bff;">
                                <div style="font-size: 0.75em; font-weight: bold; color: #007bff; text-transform: uppercase; margin-bottom: 4px;">The Person Inside</div>
                                <div style="font-style: italic; color: #333; line-height: 1.5;">{vl_voice}</div>
                            </div>
                        ''')
                    if shadow_voice:
                        html.append(f'''
                            <div style="padding: 12px 16px; background: #fff5f5; border-radius: 6px; border-left: 3px solid #dc3545;">
                                <div style="font-size: 0.75em; font-weight: bold; color: #dc3545; text-transform: uppercase; margin-bottom: 4px;">The Habit</div>
                                <div style="font-style: italic; color: #333; line-height: 1.5;">{shadow_voice}</div>
                            </div>
                        ''')
                    html.append('</div>')

                # Source text from Pass A steps
                if matched_steps and col == columns[-1]:
                    pass  # handled via card-level matched_steps

                html.append('</div>')  # end card

            html.append('</div>')  # end card view

    return "\n".join(html)


def _parse_seq_to_steps(seq_val: str, pass_a_steps: dict) -> list:
    """Parse a Seq column value (e.g., '1-2', '3', '4-6') and return matching Pass A steps."""
    matched = []
    # Handle ranges like "1-3" and single numbers like "4", and combos like "1-2, 4"
    for part in re.split(r'[,;]\s*', str(seq_val)):
        part = part.strip()
        range_match = re.match(r'(\d+)\s*[-\u2013]\s*(\d+)', part)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            for n in range(start, end + 1):
                if n in pass_a_steps:
                    matched.append(pass_a_steps[n])
        else:
            num_match = re.match(r'(\d+)', part)
            if num_match:
                n = int(num_match.group(1))
                if n in pass_a_steps:
                    matched.append(pass_a_steps[n])
    return matched


def render_analysis(data: dict, schema: dict, pass_a_result: dict = None) -> str:
    """Render analysis dict as HTML based on schema structure.

    Handles both old (washing.schema.json) and new (pass_b.schema.json) formats.
    """
    # Check if this is Pass B format (has negotiation_framework, somatic_narrative, analytical_table)
    if "negotiation_framework" in data or "somatic_narrative" in data or "analytical_table" in data:
        return render_pass_b_analysis(data, pass_a_result)

    # Otherwise, use legacy rendering
    html = []

    # Title
    if data.get("lesson_title"):
        html.append(f"<h1>{data['lesson_title']}</h1>")

    # Get schema properties to understand structure
    props = schema.get("properties", {})
    defs = schema.get("$defs", {})

    # Phase 1: SPIFFER-Q (look for phase1_spifferq or phase1_spiffer)
    phase1_key = next((k for k in data.keys() if k.startswith("phase1")), None)
    if phase1_key and data.get(phase1_key):
        phase1 = data[phase1_key]
        html.append("<h2>Phase 1: SPIFFER-Q Tagging</h2>")
        if phase1.get("summary"):
            html.append(f"<p><em>{phase1['summary']}</em></p>")
        if phase1.get("steps"):
            html.append("<ol>")
            for step in phase1["steps"]:
                tags = ", ".join(step.get("tags", []))
                instruction = step.get("instruction", "")
                notes = f" <small>({step['notes']})</small>" if step.get("notes") else ""
                html.append(f"<li><strong>[{tags}]</strong> {instruction}{notes}</li>")
            html.append("</ol>")

    # Phase 2: Negotiation Framework
    phase2_key = next((k for k in data.keys() if k.startswith("phase2")), None)
    if phase2_key and data.get(phase2_key):
        phase2 = data[phase2_key]
        html.append("<h2>Phase 2: The Negotiation Framework</h2>")

        # Shadow observations (fallback: old "striver" key for cached data)
        shadow_key = next((k for k in phase2.keys() if "shadow" in k.lower() or "striver" in k.lower() or "effort" in k.lower()), None)
        if shadow_key and phase2.get(shadow_key):
            html.append("<h3>Habitual Effort</h3>")
            html.append("<ul>")
            for obs in phase2[shadow_key]:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

        # Foundation observations
        foundation_key = next((k for k in phase2.keys() if "foundation" in k.lower() or "support" in k.lower()), None)
        if foundation_key and phase2.get(foundation_key):
            html.append("<h3>The Foundation (Support System)</h3>")
            html.append("<ul>")
            for obs in phase2[foundation_key]:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

        # Washing gradient in phase 2
        if phase2.get("washing_gradient"):
            html.append("<h3>The Washing Gradient</h3>")
            html.append(f"<p>{phase2['washing_gradient']}</p>")

    # Phase 3: Somatic Narrative (or Washing Dynamic for v1)
    phase3_narrative_key = next((k for k in data.keys() if "narrative" in k.lower() or "somatic" in k.lower()), None)
    if phase3_narrative_key and data.get(phase3_narrative_key):
        narrative = data[phase3_narrative_key]
        html.append("<h2>Phase 3: The Somatic Narrative</h2>")

        if narrative.get("primary_inquiry"):
            html.append("<h3>The Primary Inquiry</h3>")
            html.append(f"<p>{narrative['primary_inquiry']}</p>")

        if narrative.get("washing_gradient"):
            html.append("<h3>The Washing Gradient</h3>")
            html.append(f"<p>{narrative['washing_gradient']}</p>")

        if narrative.get("threshold_of_quiet"):
            html.append("<h3>The Threshold of Quiet</h3>")
            html.append(f"<p>{narrative['threshold_of_quiet']}</p>")

    # Fallback for v1 washing phase
    if data.get("phase3_washing"):
        washing = data["phase3_washing"]
        html.append("<h2>Phase 3: The Washing Dynamic</h2>")
        if washing.get("washing_moment_identified"):
            html.append("<p><strong>Washing moment identified:</strong> Yes</p>")
            if washing.get("description"):
                html.append(f"<p><strong>Description:</strong> {washing['description']}</p>")
            if washing.get("mechanism"):
                html.append(f"<p><strong>Mechanism:</strong> {washing['mechanism']}</p>")
            if washing.get("result"):
                html.append(f"<p><strong>Result:</strong> {washing['result']}</p>")
        else:
            html.append("<p><strong>Washing moment identified:</strong> No</p>")

    # Table: Look for any key containing "table"
    table_key = next((k for k in data.keys() if "table" in k.lower()), None)
    if table_key and data.get(table_key):
        table_data = data[table_key]
        rows = table_data.get("rows", [])
        column_headers = table_data.get("column_headers", {})

        html.append(f"<h2>VL-1 Analytical Table ({len(rows)} rows)</h2>")

        if rows:
            # Determine columns to display
            if column_headers:
                # Use column_headers to determine which columns and their display names
                # Sort by column number (column1, column2, etc.)
                columns = sorted([k for k in column_headers.keys() if column_headers.get(k)],
                                key=lambda x: int(x.replace("column", "")) if x.startswith("column") else 999)
                display_names = {col: column_headers[col] for col in columns}
            else:
                # Fallback: infer columns from first row
                first_row = rows[0]
                columns = list(first_row.keys())
                display_names = {col: col.replace("_", " ").replace("spiffer q", "SPIFFER-Q").title() for col in columns}

            html.append("""
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
                <thead style="background-color: #f0f0f0;">
                    <tr>
            """)
            for col in columns:
                html.append(f"<th>{display_names.get(col, col)}</th>")
            html.append("</tr></thead><tbody>")

            for row in rows:
                html.append("<tr>")
                for col in columns:
                    val = row.get(col, "")
                    # Handle list values (like tags)
                    if isinstance(val, list):
                        val = ", ".join(str(v) for v in val)
                    html.append(f"<td>{val}</td>")
                html.append("</tr>")

            html.append("</tbody></table>")

    return "\n".join(html)


def build_html(lesson_text: str = "", selected_category: str = "", selected_lesson: str = "", result: str = "") -> str:
    """Build the HTML page — delegated to analyzer_pages module."""
    from book.analyzer_pages import build_html as _build_html
    return _build_html(lesson_text, selected_category, selected_lesson, result)


def build_result_html(job: dict | None, job_id: str, schema: dict) -> str:
    """Build HTML for the result page — delegated to analyzer_pages module."""
    from book.analyzer_pages import build_result_html as _build_result_html
    return _build_result_html(job, job_id, schema)
