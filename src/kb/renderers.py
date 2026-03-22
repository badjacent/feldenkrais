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


