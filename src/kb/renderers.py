"""Pass0b renderers and debug log renderer.

Extracted from web.py to reduce its size. All functions that need web.py
module-level state (e.g. format_timestamp) use a lazy import to avoid
circular imports.
"""

import json


def render_debug_log(debug_log: list) -> str:
    """Render raw LLM input/output debug log as a flat tabbed UI.

    Self-contained: embeds its own JS and uses unique IDs so it works on any
    page and can be rendered multiple times without collisions.
    """
    if not debug_log:
        return ""

    import html as html_module
    import random
    import string

    # Filter out cached-only entries that have no useful content
    entries = [
        e for e in debug_log
        if e.get("prompt") or e.get("output") or e.get("error")
    ]
    if not entries:
        return ""

    # Unique prefix to avoid ID collisions
    uid = "dbg_" + "".join(random.choices(string.ascii_lowercase, k=6))

    pre_style = (
        "background: {bg}; padding: 12px; border-radius: 6px; "
        "white-space: pre-wrap; word-wrap: break-word; "
        "max-height: 600px; overflow-y: auto; font-size: 0.8em; margin: 0;"
    )

    parts = [
        f'<div id="{uid}" style="margin-top: 30px; border-top: 2px solid #ddd; padding-top: 15px;">',
        f'<h2 style="cursor:pointer;" '
        f"""onclick="var c=document.getElementById('{uid}_body');c.style.display=c.style.display==='none'?'block':'none';">"""
        'Raw I/O <span style="font-size:0.7em;color:#999;">(click to toggle)</span></h2>',
        f'<div id="{uid}_body" style="display:none;">',
    ]

    # --- Tab bar ---
    parts.append('<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:12px;">')
    for i, entry in enumerate(entries):
        name = entry.get("name", f"Query {i+1}")
        provider = entry.get("provider", "")
        has_error = "error" in entry
        label = f'{name} [{provider}]' if provider else name
        if has_error:
            label += " (err)"
        active_bg = "#007bff" if i == 0 else "#e9ecef"
        active_fg = "white" if i == 0 else ("#dc3545" if has_error else "#6c757d")
        parts.append(
            f'<button id="{uid}_tab{i}" onclick="{uid}_show({i})" '
            f'style="padding:6px 12px;border:1px solid #ccc;cursor:pointer;'
            f'background:{active_bg};color:{active_fg};border-radius:4px;font-size:0.85em;">'
            f'{label}</button>'
        )
    parts.append('</div>')

    # --- Tab panels ---
    for i, entry in enumerate(entries):
        display = "block" if i == 0 else "none"
        parts.append(f'<div id="{uid}_panel{i}" style="display:{display};">')

        # Input section
        prompt = entry.get("prompt", "")
        system = entry.get("system", "")
        schema = entry.get("schema")
        if prompt or system:
            parts.append('<div style="margin-bottom:12px;">')
            parts.append('<strong style="color:#555;">Input</strong>')
            if system:
                parts.append(f'<div style="margin-top:6px;font-size:0.8em;color:#888;">System ({len(system):,} chars)</div>')
                parts.append(f'<pre style="{pre_style.format(bg="#f0f8ff")}">{html_module.escape(system)}</pre>')
            if prompt:
                parts.append(f'<div style="margin-top:6px;font-size:0.8em;color:#888;">Prompt ({len(prompt):,} chars)</div>')
                parts.append(f'<pre style="{pre_style.format(bg="#f8f9fa")}">{html_module.escape(prompt)}</pre>')
            if schema:
                schema_str = json.dumps(schema, indent=2)
                parts.append(f'<div style="margin-top:6px;font-size:0.8em;color:#888;">Schema ({len(schema_str):,} chars)</div>')
                parts.append(f'<pre style="{pre_style.format(bg="#fff8f0")}">{html_module.escape(schema_str)}</pre>')
            parts.append('</div>')

        # Output section
        if "error" in entry:
            error_str = entry["error"]
            parts.append('<strong style="color:#dc3545;">Output (ERROR)</strong>')
            parts.append(f'<pre style="{pre_style.format(bg="#fff5f5")}border:1px solid #f5c6cb;">{html_module.escape(error_str)}</pre>')
        elif "output" in entry:
            output_str = json.dumps(entry["output"], indent=2)
            parts.append(f'<strong style="color:#555;">Output ({len(output_str):,} chars)</strong>')
            parts.append(f'<pre style="{pre_style.format(bg="#f0fff0")}">{html_module.escape(output_str)}</pre>')

        parts.append('</div>')

    # --- Inline JS for tab switching ---
    parts.append(f'''<script>
function {uid}_show(idx) {{
  for (var i = 0; i < {len(entries)}; i++) {{
    var p = document.getElementById("{uid}_panel" + i);
    var t = document.getElementById("{uid}_tab" + i);
    if (p) p.style.display = i === idx ? "block" : "none";
    if (t) {{ t.style.background = i === idx ? "#007bff" : "#e9ecef"; t.style.color = i === idx ? "white" : "#6c757d"; }}
  }}
}}
</script>''')

    parts.append('</div></div>')
    return "\n".join(parts)


def _render_step_bullets(e: dict, html_module) -> str:
    """Render a single enriched step as clarified text + bullet points."""
    esc = html_module.escape
    parts = []

    # Clarified text as the step body
    cl = e.get("clarified", "")
    if cl:
        parts.append(f'<div style="margin-bottom: 6px;">{esc(cl)}</div>')

    # Bullet list of enrichment fields -- ordered abstract-to-concrete
    bullets = []
    intent = e.get("intent", "")
    if intent:
        bullets.append(f"<strong>Intent:</strong> {esc(intent)}")

    diff = e.get("differentiation", "")
    if diff and diff.lower() != "none":
        bullets.append(f"<strong>Differentiation:</strong> {esc(diff)}")

    act = e.get("activation", "")
    if act and act.lower() != "none":
        bullets.append(f"<strong>Activation:</strong> {esc(act)}")

    inhib = e.get("inhibition", "")
    if inhib and inhib.lower() != "none":
        bullets.append(f"<strong>Inhibition:</strong> {esc(inhib)}")

    tr = e.get("transmission_route", "")
    if tr and tr.lower() != "none":
        bullets.append(f"<strong>Transmission Route:</strong> {esc(tr)}")

    gp = e.get("ground_push", "")
    if gp and gp.lower() != "none":
        bullets.append(f"<strong>Ground Push:</strong> {esc(gp)}")

    md = e.get("mechanical_directive", "")
    if md and not md.lower().startswith("none"):
        bullets.append(f"<strong>Mechanical Directive:</strong> {esc(md)}")

    qc = e.get("attentional_cue", "")
    if qc and qc.lower() != "none":
        bullets.append(f"<strong>Attentional Cue:</strong> {esc(qc)}")

    si = e.get("sensory_inquiry", "")
    if si:
        bullets.append(f"<strong>Sensory Inquiry:</strong> {esc(si)}")

    if bullets:
        items = "".join(f"<li>{b}</li>" for b in bullets)
        parts.append(f'<ul style="margin: 4px 0 0 0; padding-left: 20px; font-size: 0.9em; '
                     f'color: #444;">{items}</ul>')

    return "\n".join(parts)


def _render_original_segments(pa_step: dict, seg_lookup: dict, html_module) -> str:
    """Render a collapsible <details> with the original transcript segments for a step."""
    seg_ids = pa_step.get("segments", [])
    if not seg_ids:
        return ""
    esc = html_module.escape
    n = len(seg_ids)
    label = f"Original text ({n} segment{'s' if n != 1 else ''})"
    lines = []
    for sid in seg_ids:
        text = seg_lookup.get(sid, "")
        if text:
            lines.append(f'<div style="margin: 2px 0;"><span style="color: #999; font-size: 0.85em;">[{sid}]</span> {esc(text)}</div>')
    if not lines:
        return ""
    return (
        f'<details style="margin-top: 6px;">'
        f'<summary style="cursor: pointer; font-size: 0.85em; color: #888;">{label}</summary>'
        f'<div style="margin-top: 4px; padding: 6px 10px; background: #fafafa; '
        f'border-radius: 4px; font-size: 0.85em; color: #555;">{"".join(lines)}</div>'
        f'</details>'
    )


def render_pass0b_result(result: dict) -> str:
    """Render Pass A + Pass 0b results as grouped steps with bullet-point enrichment."""
    import html as html_module
    import web as _web

    pass_a = result.get("pass_a", {})
    groups = pass_a.get("groups", [])
    pa_steps = pass_a.get("steps", [])
    enriched = result.get("enriched_steps", [])

    enriched_lookup = {e["step_number"]: e for e in enriched}
    pa_step_lookup = {s["step_number"]: s for s in pa_steps}

    # Build segment text and time lookups from Pass 0
    pass_0 = pass_a.get("pass_0", {})
    seg_lookup = {s["id"]: s["text"] for s in pass_0.get("segments", [])}
    seg_time_lookup = {s["id"]: (s.get("time_start"), s.get("time_end"))
                       for s in pass_0.get("segments", [])
                       if s.get("time_start") is not None}

    def _step_time_str(pa_step):
        """Get formatted time range for a step from its segments."""
        seg_ids = pa_step.get("segments", [])
        if not seg_ids or not seg_time_lookup:
            return ""
        starts = [seg_time_lookup[sid][0] for sid in seg_ids if sid in seg_time_lookup]
        ends = [seg_time_lookup[sid][1] for sid in seg_ids if sid in seg_time_lookup]
        if not starts or not ends:
            return ""
        return f" ({_web.format_timestamp(min(starts))} – {_web.format_timestamp(max(ends))})"

    # Count totals
    total_pa_steps = len(pa_steps)
    total_enriched = len(enriched)
    irrelevant = sum(1 for e in enriched if not e.get("relevance", True))
    relevant = total_enriched - irrelevant
    num_groups = len(groups)
    missing_count = total_pa_steps - total_enriched

    out = []

    # Segment gap warning (Pass A dropped segments)
    segment_gaps = pass_a.get("segment_gaps", [])
    if segment_gaps:
        gap_str = ", ".join(str(s) for s in segment_gaps[:30])
        if len(segment_gaps) > 30:
            gap_str += f"... ({len(segment_gaps)} total)"
        out.append(
            f'<div style="margin-bottom: 12px; padding: 10px 14px; '
            f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            f'color: #856404;">'
            f'<strong>Warning — Pass A segment gaps:</strong> '
            f'{len(segment_gaps)} transcript segments were not assigned to any step: '
            f'[{html_module.escape(gap_str)}]. '
            f'Clear the Pass A cache and re-run.'
            f'</div>'
        )

    # Truncation warning
    truncation_warning = result.get("truncation_warning")
    if missing_count > 0 or truncation_warning:
        warning_msg = truncation_warning or (
            f"LLM returned {total_enriched} of {total_pa_steps} steps "
            f"({missing_count} steps missing)."
        )
        out.append(
            f'<div style="margin-bottom: 12px; padding: 10px 14px; '
            f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            f'color: #856404;">'
            f'<strong>Warning — Incomplete output:</strong> {html_module.escape(warning_msg)} '
            f'Clear the 0b cache and re-run to retry.'
            f'</div>'
        )

    out.append(f'<p style="color: #666; font-size: 0.9em;">{total_enriched} of {total_pa_steps} steps enriched across '
               f'{num_groups} groups, {relevant} lesson-relevant, {irrelevant} omitted'
               f'{f", {missing_count} missing" if missing_count > 0 else ""}</p>')

    # Render by group
    rendered_steps = set()
    for g in groups:
        gnum = g.get("group_number", 0)
        label = g.get("label", "")
        desc = g.get("description", "")

        group_step_nums = g.get("steps", [])
        group_relevant = [sn for sn in group_step_nums
                          if enriched_lookup.get(sn, {}).get("relevance", True)]
        if not group_relevant:
            for sn in group_step_nums:
                rendered_steps.add(sn)
            continue

        # Group header
        out.append(
            f'<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            f'background: #e8f0fe; border-left: 4px solid #1a73e8; border-radius: 4px;">'
            f'<strong style="color: #1a73e8;">Group {gnum}: {html_module.escape(label)}</strong>'
        )
        if desc:
            out.append(f'<div style="color: #555; font-size: 0.85em; margin-top: 4px;">'
                        f'{html_module.escape(desc)}</div>')
        out.append('</div>')

        for sn in group_step_nums:
            rendered_steps.add(sn)
            e = enriched_lookup.get(sn)
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")

            if e is None:
                # Step exists in Pass A but missing from LLM output (truncation)
                time_str = _step_time_str(pa_step)
                orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
                out.append(
                    f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                    f'background: #fce4ec; border-radius: 4px; border: 1px solid #ef9a9a;">'
                    f'<strong>Step {sn}</strong>'
                    f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                    f' &mdash; <em>{html_module.escape(instruction)}</em>'
                    f'<div style="color: #c62828; font-size: 0.85em; margin-top: 4px;">'
                    f'Not enriched (LLM output truncated)</div>'
                    f'{orig_html}'
                    f'</div>'
                )
                continue

            if not e.get("relevance", True):
                continue

            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                f' &mdash; <em>{html_module.escape(instruction)}</em>'
                f'<div style="margin-top: 6px;">{_render_step_bullets(e, html_module)}</div>'
                f'{orig_html}'
                f'</div>'
            )

    # Render any ungrouped steps
    ungrouped = [e for e in enriched if e["step_number"] not in rendered_steps]
    if ungrouped:
        out.append(
            '<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            'background: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px;">'
            '<strong style="color: #856404;">Ungrouped Steps</strong></div>'
        )
        for e in ungrouped:
            if not e.get("relevance", True):
                continue
            sn = e["step_number"]
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")
            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                f' &mdash; <em>{html_module.escape(instruction)}</em>'
                f'<div style="margin-top: 6px;">{_render_step_bullets(e, html_module)}</div>'
                f'{orig_html}'
                f'</div>'
            )

    return "\n".join(out)


def _render_lang_step_bullets(e: dict, html_module) -> str:
    """Render bullet list of language extraction fields for a step.

    Ordered abstract-to-concrete: explanatory first, literal quotations last.
    """
    esc = html_module.escape
    bullets = []

    el = e.get("explanatory_language", "")
    if el and el.lower() != "none":
        bullets.append(f"<strong>Explanatory:</strong> {esc(el)}")

    ac = e.get("attention_cues", "")
    if ac and ac.lower() != "none":
        bullets.append(f"<strong>Attention Cues:</strong> {esc(ac)}")

    ef = e.get("effort_language", "")
    if ef and ef.lower() != "none":
        bullets.append(f"<strong>Effort:</strong> {esc(ef)}")

    il = e.get("image_language", "")
    if il and il.lower() != "none":
        bullets.append(f"<strong>Imagery:</strong> {esc(il)}")

    dp = e.get("distinctive_phrasing", "")
    if dp and dp.lower() != "none":
        bullets.append(f"<strong>Distinctive:</strong> {esc(dp)}")

    if bullets:
        items = "".join(f"<li>{b}</li>" for b in bullets)
        return (f'<ul style="margin: 4px 0 0 0; padding-left: 20px; font-size: 0.9em; '
                f'color: #444;">{items}</ul>')
    return ""


def _render_lang_summary(summary: dict, html_module) -> str:
    """Render the lesson-level language summary block."""
    esc = html_module.escape
    out = []
    out.append(
        '<div style="margin-bottom: 24px; padding: 16px 20px; '
        'background: #f0f7ff; border: 1px solid #b3d4fc; border-radius: 8px;">'
        '<h3 style="margin: 0 0 12px 0; color: #1a56db;">Lesson-Level Language Summary</h3>'
    )

    # Teaching voice summary
    tvs = summary.get("teaching_voice_summary", "")
    if tvs:
        out.append(
            f'<div style="margin-bottom: 14px; padding: 10px 14px; '
            f'background: white; border-radius: 6px; border-left: 3px solid #1a56db; '
            f'font-style: italic; color: #333;">{esc(tvs)}</div>'
        )

    # Speaker principles
    principles = summary.get("speaker_principles", [])
    if principles:
        out.append('<div style="margin-bottom: 12px;"><strong>Speaker Principles:</strong>')
        out.append('<ol style="margin: 6px 0 0 0; padding-left: 24px;">')
        for p in principles:
            out.append(f'<li style="margin-bottom: 6px;">{esc(p)}</li>')
        out.append('</ol></div>')

    # Signature vocabulary
    vocab = summary.get("signature_vocabulary", [])
    if vocab:
        out.append('<div style="margin-bottom: 12px;"><strong>Signature Vocabulary:</strong>')
        out.append('<ul style="margin: 6px 0 0 0; padding-left: 24px;">')
        for v in vocab:
            out.append(f'<li>{esc(v)}</li>')
        out.append('</ul></div>')

    # Dominant attention mode
    dsm = summary.get("dominant_attention_mode", "")
    if dsm:
        out.append(f'<div style="margin-bottom: 8px;"><strong>Dominant Attention Mode:</strong> {esc(dsm)}</div>')

    # Effort philosophy
    ep = summary.get("effort_philosophy", "")
    if ep:
        out.append(f'<div style="margin-bottom: 8px;"><strong>Effort Philosophy:</strong> {esc(ep)}</div>')

    out.append('</div>')
    return "\n".join(out)


def _render_somatic_summary(summary: dict, html_module) -> str:
    """Render the lesson-level somatic architecture summary block."""
    esc = html_module.escape
    out = []
    out.append(
        '<div style="margin-bottom: 24px; padding: 16px 20px; '
        'background: #f0fff4; border: 1px solid #a3d9a5; border-radius: 8px;">'
        '<h3 style="margin: 0 0 12px 0; color: #2d6a4f;">Lesson-Level Somatic Summary</h3>'
    )

    # Somatic architecture summary
    sas = summary.get("somatic_architecture_summary", "")
    if sas:
        out.append(
            f'<div style="margin-bottom: 14px; padding: 10px 14px; '
            f'background: white; border-radius: 6px; border-left: 3px solid #2d6a4f; '
            f'font-style: italic; color: #333;">{esc(sas)}</div>'
        )

    # Primary transmission routes
    routes = summary.get("primary_transmission_routes", [])
    if routes:
        out.append('<div style="margin-bottom: 12px;"><strong>Primary Transmission Routes:</strong>')
        out.append('<ul style="margin: 6px 0 0 0; padding-left: 24px;">')
        for r in routes:
            out.append(f'<li>{esc(r)}</li>')
        out.append('</ul></div>')

    # Differentiation strategy
    ds = summary.get("differentiation_strategy", "")
    if ds:
        out.append(f'<div style="margin-bottom: 8px;"><strong>Differentiation Strategy:</strong> {esc(ds)}</div>')

    # Key inhibitions
    inhib = summary.get("key_inhibitions", [])
    if inhib:
        out.append('<div style="margin-bottom: 12px;"><strong>Key Inhibitions:</strong>')
        out.append('<ul style="margin: 6px 0 0 0; padding-left: 24px;">')
        for i in inhib:
            out.append(f'<li>{esc(i)}</li>')
        out.append('</ul></div>')

    # Ground force strategy
    gfs = summary.get("ground_force_strategy", "")
    if gfs:
        out.append(f'<div style="margin-bottom: 8px;"><strong>Ground Force Strategy:</strong> {esc(gfs)}</div>')

    out.append('</div>')
    return "\n".join(out)


def render_pass0b_lang_result(result: dict) -> str:
    """Render Pass A + Pass 0b-lang results as summary + grouped steps with language bullets."""
    import html as html_module
    import web as _web

    pass_a = result.get("pass_a", {})
    groups = pass_a.get("groups", [])
    pa_steps = pass_a.get("steps", [])
    enriched = result.get("enriched_steps", [])
    summary = result.get("summary", {})

    enriched_lookup = {e["step_number"]: e for e in enriched}
    pa_step_lookup = {s["step_number"]: s for s in pa_steps}

    # Build segment text and time lookups from Pass 0
    pass_0 = pass_a.get("pass_0", {})
    seg_lookup = {s["id"]: s["text"] for s in pass_0.get("segments", [])}
    seg_time_lookup = {s["id"]: (s.get("time_start"), s.get("time_end"))
                       for s in pass_0.get("segments", [])
                       if s.get("time_start") is not None}

    def _step_time_str(pa_step):
        seg_ids = pa_step.get("segments", [])
        if not seg_ids or not seg_time_lookup:
            return ""
        starts = [seg_time_lookup[sid][0] for sid in seg_ids if sid in seg_time_lookup]
        ends = [seg_time_lookup[sid][1] for sid in seg_ids if sid in seg_time_lookup]
        if not starts or not ends:
            return ""
        return f" ({_web.format_timestamp(min(starts))} – {_web.format_timestamp(max(ends))})"

    total_pa_steps = len(pa_steps)
    total_enriched = len(enriched)
    num_groups = len(groups)
    missing_count = total_pa_steps - total_enriched

    out = []

    # Lesson-level summary at the top
    if summary:
        out.append(_render_lang_summary(summary, html_module))

    # Truncation warning
    truncation_warning = result.get("truncation_warning")
    if missing_count > 0 or truncation_warning:
        warning_msg = truncation_warning or (
            f"LLM returned {total_enriched} of {total_pa_steps} steps "
            f"({missing_count} steps missing)."
        )
        out.append(
            f'<div style="margin-bottom: 12px; padding: 10px 14px; '
            f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            f'color: #856404;">'
            f'<strong>Warning — Incomplete output:</strong> {html_module.escape(warning_msg)} '
            f'Clear the cache and re-run to retry.'
            f'</div>'
        )

    out.append(f'<p style="color: #666; font-size: 0.9em;">{total_enriched} of {total_pa_steps} steps analyzed across '
               f'{num_groups} groups'
               f'{f", {missing_count} missing" if missing_count > 0 else ""}</p>')

    # Render by group
    rendered_steps = set()
    for g in groups:
        gnum = g.get("group_number", 0)
        label = g.get("label", "")
        desc = g.get("description", "")
        group_step_nums = g.get("steps", [])

        # Group header
        out.append(
            f'<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            f'background: #e8f0fe; border-left: 4px solid #1a73e8; border-radius: 4px;">'
            f'<strong style="color: #1a73e8;">Group {gnum}: {html_module.escape(label)}</strong>'
        )
        if desc:
            out.append(f'<div style="color: #555; font-size: 0.85em; margin-top: 4px;">'
                        f'{html_module.escape(desc)}</div>')
        out.append('</div>')

        for sn in group_step_nums:
            rendered_steps.add(sn)
            e = enriched_lookup.get(sn)
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")

            if e is None:
                time_str = _step_time_str(pa_step)
                orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
                out.append(
                    f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                    f'background: #fce4ec; border-radius: 4px; border: 1px solid #ef9a9a;">'
                    f'<strong>Step {sn}</strong>'
                    f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                    f' &mdash; <em>{html_module.escape(instruction)}</em>'
                    f'<div style="color: #c62828; font-size: 0.85em; margin-top: 4px;">'
                    f'Not analyzed (LLM output truncated)</div>'
                    f'{orig_html}'
                    f'</div>'
                )
                continue

            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                f' &mdash; <em>{html_module.escape(instruction)}</em>'
                f'<div style="margin-top: 6px;">{_render_lang_step_bullets(e, html_module)}</div>'
                f'{orig_html}'
                f'</div>'
            )

    # Render any ungrouped steps
    ungrouped = [e for e in enriched if e["step_number"] not in rendered_steps]
    if ungrouped:
        out.append(
            '<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            'background: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px;">'
            '<strong style="color: #856404;">Ungrouped Steps</strong></div>'
        )
        for e in ungrouped:
            sn = e["step_number"]
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")
            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                f' &mdash; <em>{html_module.escape(instruction)}</em>'
                f'<div style="margin-top: 6px;">{_render_lang_step_bullets(e, html_module)}</div>'
                f'{orig_html}'
                f'</div>'
            )

    return "\n".join(out)


def render_pass0b_combined_result(somatic_result: dict, lang_result: dict) -> str:
    """Render merged somatic + language results as a single step list.

    Language summary at top, then each step shows somatic bullets followed by language bullets.
    """
    import html as html_module
    import web as _web
    esc = html_module.escape

    pass_a = somatic_result.get("pass_a", {})
    groups = pass_a.get("groups", [])
    pa_steps = pass_a.get("steps", [])

    somatic_enriched = somatic_result.get("enriched_steps", [])
    lang_enriched = lang_result.get("enriched_steps", [])
    lang_summary = lang_result.get("summary", {})
    somatic_summary = somatic_result.get("summary", {})

    somatic_lookup = {e["step_number"]: e for e in somatic_enriched}
    lang_lookup = {e["step_number"]: e for e in lang_enriched}
    pa_step_lookup = {s["step_number"]: s for s in pa_steps}

    # Build segment text and time lookups from Pass 0
    pass_0 = pass_a.get("pass_0", {})
    seg_lookup = {s["id"]: s["text"] for s in pass_0.get("segments", [])}
    seg_time_lookup = {s["id"]: (s.get("time_start"), s.get("time_end"))
                       for s in pass_0.get("segments", [])
                       if s.get("time_start") is not None}

    def _step_time_str(pa_step):
        seg_ids = pa_step.get("segments", [])
        if not seg_ids or not seg_time_lookup:
            return ""
        starts = [seg_time_lookup[sid][0] for sid in seg_ids if sid in seg_time_lookup]
        ends = [seg_time_lookup[sid][1] for sid in seg_ids if sid in seg_time_lookup]
        if not starts or not ends:
            return ""
        return f" ({_web.format_timestamp(min(starts))} – {_web.format_timestamp(max(ends))})"

    total_pa_steps = len(pa_steps)
    total_somatic = len(somatic_enriched)
    total_lang = len(lang_enriched)
    num_groups = len(groups)

    out = []

    # Summaries at top
    if somatic_summary:
        out.append(_render_somatic_summary(somatic_summary, html_module))
    if lang_summary:
        out.append(_render_lang_summary(lang_summary, html_module))

    # Warnings
    for label, total in [("Somatic", total_somatic), ("Language", total_lang)]:
        missing = total_pa_steps - total
        if missing > 0:
            out.append(
                f'<div style="margin-bottom: 12px; padding: 10px 14px; '
                f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
                f'color: #856404;">'
                f'<strong>Warning — {label}:</strong> {total} of {total_pa_steps} steps '
                f'({missing} missing). Clear cache and re-run.'
                f'</div>'
            )

    somatic_warning = somatic_result.get("truncation_warning")
    if somatic_warning:
        out.append(
            f'<div style="margin-bottom: 12px; padding: 10px 14px; '
            f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            f'color: #856404;">'
            f'<strong>Warning — Somatic:</strong> {esc(somatic_warning)}'
            f'</div>'
        )

    # Segment gap warning
    segment_gaps = pass_a.get("segment_gaps", [])
    if segment_gaps:
        gap_str = ", ".join(str(s) for s in segment_gaps[:30])
        if len(segment_gaps) > 30:
            gap_str += f"... ({len(segment_gaps)} total)"
        out.append(
            f'<div style="margin-bottom: 12px; padding: 10px 14px; '
            f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            f'color: #856404;">'
            f'<strong>Warning — Pass A segment gaps:</strong> '
            f'{len(segment_gaps)} transcript segments not assigned to any step: '
            f'[{esc(gap_str)}].'
            f'</div>'
        )

    irrelevant = sum(1 for e in somatic_enriched if not e.get("relevance", True))
    relevant = total_somatic - irrelevant
    out.append(f'<p style="color: #666; font-size: 0.9em;">{total_pa_steps} steps across '
               f'{num_groups} groups, {relevant} lesson-relevant, {irrelevant} omitted</p>')

    # Render by group
    rendered_steps = set()
    for g in groups:
        gnum = g.get("group_number", 0)
        label = g.get("label", "")
        desc = g.get("description", "")
        group_step_nums = g.get("steps", [])

        group_relevant = [sn for sn in group_step_nums
                          if somatic_lookup.get(sn, {}).get("relevance", True)]
        if not group_relevant:
            for sn in group_step_nums:
                rendered_steps.add(sn)
            continue

        # Group header
        out.append(
            f'<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            f'background: #e8f0fe; border-left: 4px solid #1a73e8; border-radius: 4px;">'
            f'<strong style="color: #1a73e8;">Group {gnum}: {esc(label)}</strong>'
        )
        if desc:
            out.append(f'<div style="color: #555; font-size: 0.85em; margin-top: 4px;">'
                        f'{esc(desc)}</div>')
        out.append('</div>')

        for sn in group_step_nums:
            rendered_steps.add(sn)
            som = somatic_lookup.get(sn)
            lang = lang_lookup.get(sn)
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")
            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)

            if som is None and lang is None:
                out.append(
                    f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                    f'background: #fce4ec; border-radius: 4px; border: 1px solid #ef9a9a;">'
                    f'<strong>Step {sn}</strong>'
                    f'<span style="color: #888; font-size: 0.85em;">{esc(time_str)}</span>'
                    f' &mdash; <em>{esc(instruction)}</em>'
                    f'<div style="color: #c62828; font-size: 0.85em; margin-top: 4px;">'
                    f'Not enriched (LLM output truncated)</div>'
                    f'{orig_html}'
                    f'</div>'
                )
                continue

            # Skip irrelevant steps (somatic pass marks relevance)
            if som and not som.get("relevance", True):
                continue

            # Build merged bullet content
            parts = []
            if som:
                somatic_html = _render_step_bullets(som, html_module)
                if somatic_html:
                    parts.append(somatic_html)
            if lang:
                lang_html = _render_lang_step_bullets(lang, html_module)
                if lang_html:
                    parts.append(lang_html)

            merged = "\n".join(parts)

            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{esc(time_str)}</span>'
                f' &mdash; <em>{esc(instruction)}</em>'
                f'<div style="margin-top: 6px;">{merged}</div>'
                f'{orig_html}'
                f'</div>'
            )

    # Ungrouped steps
    ungrouped_nums = [s["step_number"] for s in pa_steps if s["step_number"] not in rendered_steps]
    if ungrouped_nums:
        out.append(
            '<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            'background: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px;">'
            '<strong style="color: #856404;">Ungrouped Steps</strong></div>'
        )
        for sn in ungrouped_nums:
            som = somatic_lookup.get(sn)
            lang = lang_lookup.get(sn)
            if som and not som.get("relevance", True):
                continue
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")
            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
            parts = []
            if som:
                somatic_html = _render_step_bullets(som, html_module)
                if somatic_html:
                    parts.append(somatic_html)
            if lang:
                lang_html = _render_lang_step_bullets(lang, html_module)
                if lang_html:
                    parts.append(lang_html)
            merged = "\n".join(parts)
            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{esc(time_str)}</span>'
                f' &mdash; <em>{esc(instruction)}</em>'
                f'<div style="margin-top: 6px;">{merged}</div>'
                f'{orig_html}'
                f'</div>'
            )

    return "\n".join(out)
