"""Dashboard HTML builders for ingestion, analysis, pipeline, and finder pages.

Extracted from web.py to reduce its size. Functions that need web.py
module-level state use a lazy import to avoid circular imports.
"""

import html as html_mod
import json


def _analysis_step_names():
    """Return step names in display order, filtered to those in DB."""
    import web as _web
    _db = _web._db
    steps = _db.get_analysis_steps()
    step_lookup = {s["name"]: s for s in steps}
    # Topological order (DAG depth), alphabetical tiebreaker within each level
    ordered = [
        # Level 0: no dependencies
        "decompose", "extract_steps",
        # Level 1: depend on level 0
        "language", "map_body_parts", "somatic", "spatial_v3",
        # Level 2: depend on level 1
        "concept_tags", "condense", "language_patterns", "spatial",
        # Level 3: depend on level 2
        "embed", "embed_positions", "index_concepts", "index_spatial",
        "axes", "motifs", "raw_motifs",
    ]
    known = [sn for sn in ordered if sn in step_lookup]
    # Any DB step missing from the display list still shows (at the end)
    extra = sorted(sn for sn in step_lookup if sn not in ordered)
    return known + extra, step_lookup


_STEP_DISPLAY_NAMES = {
    "decompose": "decompose", "extract_steps": "extract",
    "language": "language", "map_body_parts": "map-body",
    "somatic": "somatic", "spatial_v3": "spatial-v3",
    "concept_tags": "concepts", "condense": "condense",
    "language_patterns": "lang-pat", "spatial": "spatial",
    "embed": "embed", "embed_positions": "embed-pos",
    "index_concepts": "idx-conc", "index_spatial": "idx-spat",
    "motifs": "motifs", "raw_motifs": "raw-mot", "axes": "axes",
}


def _build_ingestion_html() -> str:
    """Build the ingestion tab content HTML (cards + events + documents tables)."""
    import web as _web
    _db = _web._db

    summary = _db.get_pipeline_summary()
    events = _db.get_pipeline_log(limit=50)
    documents = _db.get_document_pipeline_view(limit=50)

    # Check for permanent failures
    perm_failures = _db.get_pipeline_log(limit=20, event="permanent_failure")

    # Summary cards
    doc_total = summary.get("documents", {}).get("total", 0)
    transcribed = summary.get("transcribed", 0)
    pending_docs = summary.get("documents", {}).get("pending", 0)
    jobs = summary.get("transcription_jobs", {})
    pending_jobs = jobs.get("pending", 0)
    failed_jobs = jobs.get("failed", 0)

    failed_count = sum(1 for d in documents if d.get("pipeline_stage") == "failed")

    # Events table rows
    event_rows = []
    for ev in events:
        created = str(ev.get("created_at", ""))[:19]
        event_type = ev.get("event", "")
        title = ev.get("title", "") or ""
        doc_id = str(ev.get("document_id", "") or "")[:8]
        detail = ev.get("detail", {}) or {}
        detail_str = json.dumps(detail) if detail else ""
        if len(detail_str) > 120:
            detail_str = detail_str[:120] + "..."

        # Color code event types
        color_map = {
            "loaded": "#28a745",
            "staged": "#17a2b8",
            "batch_created": "#007bff",
            "transcription_complete": "#28a745",
            "transcription_failed": "#dc3545",
            "duplicate_skipped": "#6c757d",
            "load_error": "#dc3545",
            "analysis_complete": "#28a745",
            "analysis_error": "#dc3545",
            "embedding_complete": "#17a2b8",
            "permanent_failure": "#dc3545",
            "load_retry": "#ffc107",
            "transcription_retry": "#ffc107",
            "resubmit": "#6610f2",
            "temp_cleanup": "#6c757d",
        }
        color = color_map.get(event_type, "#333")

        event_rows.append(f"""
            <tr>
                <td style="white-space: nowrap;">{created}</td>
                <td><span style="color: {color}; font-weight: bold;">{event_type}</span></td>
                <td>{title}</td>
                <td style="font-family: monospace; font-size: 0.85em;">{doc_id}</td>
                <td style="font-size: 0.85em; color: #666;">{detail_str}</td>
            </tr>
        """)

    # Documents table rows
    doc_rows = []
    for doc in documents:
        created = str(doc.get("created_at", ""))[:10]
        title = doc.get("title", "")
        if len(title) > 60:
            title = title[:57] + "..."
        category = doc.get("display_category", "")
        has_tx = doc.get("has_transcription", False)
        latest_job = doc.get("latest_job_status", "")
        latest_error = doc.get("latest_error", "")

        stage = doc.get("pipeline_stage", "")
        stage_styles = {
            "transcribed": ("Transcribed", "#d4edda", "#155724"),
            "transcribing": ("Transcribing", "#fff3cd", "#856404"),
            "failed": ("Failed", "#f8d7da", "#721c24"),
            "awaiting_transcription": ("New", "#e2e3e5", "#383d41"),
        }
        label, bg, fg = stage_styles.get(stage, ("Unknown", "#e2e3e5", "#383d41"))
        title_attr = f' title="{latest_error}"' if latest_error and stage == "failed" else ""
        status_badge = f'<span style="padding: 2px 8px; border-radius: 10px; font-size: 0.8em; background: {bg}; color: {fg};"{title_attr}>{label}</span>'

        doc_id = str(doc.get("id", ""))
        can_resubmit = stage in ("failed", "transcribed")
        resubmit_btn = (
            f'<button class="resubmit-btn" data-docid="{doc_id}" '
            f'style="padding: 2px 8px; font-size: 0.8em; border: 1px solid #dc3545; '
            f'background: white; color: #dc3545; border-radius: 4px; cursor: pointer;">'
            f'Resubmit</button>'
        ) if can_resubmit else ""

        doc_rows.append(f"""
            <tr>
                <td>{created}</td>
                <td>{title}</td>
                <td style="font-size: 0.85em;">{category}</td>
                <td>{status_badge}</td>
                <td style="text-align: center;">{doc.get("job_count", 0)}</td>
                <td style="text-align: center;">{resubmit_btn}</td>
            </tr>
        """)

    events_html = "\n".join(event_rows) if event_rows else '<tr><td colspan="5" style="text-align: center; color: #999;">No events yet</td></tr>'
    docs_html = "\n".join(doc_rows) if doc_rows else '<tr><td colspan="6" style="text-align: center; color: #999;">No documents yet</td></tr>'

    # Permanent failure banner
    pf_banner = ""
    if perm_failures:
        pf_items = []
        seen = set()
        for pf in perm_failures:
            title = pf.get("title", "Unknown")
            if title in seen:
                continue
            seen.add(title)
            stage = (pf.get("detail") or {}).get("stage", "unknown")
            created = str(pf.get("created_at", ""))[:19]
            pf_items.append(f"<li><strong>{title}</strong> — {stage} (failed {created})</li>")
        pf_banner = f"""
        <div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 6px; padding: 12px 16px; margin-bottom: 15px; color: #721c24;">
            <strong>Permanent Failures ({len(seen)})</strong> — these documents exceeded retry limits:
            <ul style="margin: 8px 0 0 0; padding-left: 20px;">{"".join(pf_items)}</ul>
        </div>
        """

    return f"""
        {pf_banner}
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 15px;">
            <button id="refresh-ingestion" style="padding: 4px 12px; border: 1px solid #007bff; background: white; color: #007bff; border-radius: 4px; cursor: pointer; font-size: 0.85em;">Refresh</button>
            {f'<button id="retry-all-failed" style="padding: 4px 12px; border: 1px solid #dc3545; background: white; color: #dc3545; border-radius: 4px; cursor: pointer; font-size: 0.85em;">Retry all failed ({failed_count})</button>' if failed_count else ''}
            <span id="refresh-status" style="font-size: 0.8em; color: #999;"></span>
        </div>
        <div class="cards">
            <div class="card">
                <div class="value">{doc_total}</div>
                <div class="label">Total Documents</div>
            </div>
            <div class="card">
                <div class="value">{transcribed}</div>
                <div class="label">Transcribed</div>
            </div>
            <div class="card">
                <div class="value">{pending_jobs}</div>
                <div class="label">Pending Jobs</div>
            </div>
            <div class="card">
                <div class="value" style="color: {('#dc3545' if failed_jobs else '#333')};">{failed_jobs}</div>
                <div class="label">Failed Jobs</div>
            </div>
        </div>

        <h2>Recent Events</h2>
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Event</th>
                    <th>Title</th>
                    <th>Doc ID</th>
                    <th>Detail</th>
                </tr>
            </thead>
            <tbody>
                {events_html}
            </tbody>
        </table>

        <h2>Documents</h2>
        <table>
            <thead>
                <tr>
                    <th>Created</th>
                    <th>Title</th>
                    <th>Category</th>
                    <th>Status</th>
                    <th>Jobs</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                {docs_html}
            </tbody>
        </table>
    """


def _build_ingestion_page() -> str:
    """Build the standalone ingestion dashboard HTML page."""
    import web as _web
    _db = _web._db

    if not _db:
        return """
<!DOCTYPE html><html><head><title>Ingestion Dashboard</title>
<style>body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }</style>
</head><body><h1>Ingestion Dashboard</h1><p>Database not configured. Set DATABASE_URL to enable.</p>
<p><a href="/">&larr; Home</a></p></body></html>"""

    try:
        ingestion_html = _build_ingestion_html()
    except Exception as e:
        ingestion_html = f'<div style="color: red;">Error loading ingestion data: {e}</div>'

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Ingestion Dashboard - Feldenkrais</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; }}
        h2 {{ color: #555; border-bottom: 1px solid #ddd; padding-bottom: 5px; margin-top: 30px; }}
        .nav {{ margin-bottom: 20px; }}
        .nav a {{ color: #007bff; text-decoration: none; }}
        .nav a:hover {{ text-decoration: underline; }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}
        .card {{
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
        }}
        .card .value {{
            font-size: 2em;
            font-weight: bold;
            color: #333;
        }}
        .card .label {{
            font-size: 0.85em;
            color: #666;
            margin-top: 5px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        th, td {{
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        th {{
            background: #f0f0f0;
            font-weight: bold;
            font-size: 0.9em;
        }}
        tr:hover {{ background: #f8f9fa; }}
    </style>
</head>
<body>
    <div class="nav"><a href="/">&larr; Home</a> <a href="/analysis">Analysis</a></div>
    <h1>Ingestion Dashboard</h1>

    <div id="ingestion-content">
        {ingestion_html}
    </div>

    <script>
        document.addEventListener('click', function(e) {{
            if (e.target && e.target.id === 'refresh-ingestion') {{
                var btn = e.target;
                var statusEl = document.getElementById('refresh-status');
                btn.disabled = true;
                btn.textContent = 'Refreshing...';
                if (statusEl) statusEl.textContent = '';
                fetch('/api/pipeline/ingestion')
                    .then(function(r) {{ return r.text(); }})
                    .then(function(html) {{
                        document.getElementById('ingestion-content').innerHTML = html;
                    }})
                    .catch(function(err) {{
                        if (statusEl) statusEl.textContent = 'Error: ' + err;
                        btn.disabled = false;
                        btn.textContent = 'Refresh';
                    }});
            }}
            if (e.target && e.target.id === 'retry-all-failed') {{
                var btn = e.target;
                if (!confirm('Retry all failed documents? Their transcription state will be cleared for reprocessing.')) return;
                btn.disabled = true;
                btn.textContent = 'Retrying...';
                fetch('/api/ingestion/retry-all-failed', {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        if (data.status === 'ok') {{
                            btn.textContent = 'Retried ' + data.count;
                            btn.style.color = '#28a745';
                            btn.style.borderColor = '#28a745';
                            setTimeout(function() {{
                                fetch('/api/pipeline/ingestion')
                                    .then(function(r) {{ return r.text(); }})
                                    .then(function(html) {{
                                        document.getElementById('ingestion-content').innerHTML = html;
                                    }});
                            }}, 500);
                        }} else {{
                            btn.textContent = data.error || 'Error';
                            btn.disabled = false;
                        }}
                    }})
                    .catch(function(err) {{
                        btn.textContent = 'Error';
                        btn.disabled = false;
                    }});
            }}
            if (e.target && e.target.classList.contains('resubmit-btn')) {{
                var btn = e.target;
                var docId = btn.dataset.docid;
                if (!confirm('Resubmit this document? All existing transcription, analysis, and embeddings will be cleared.')) return;
                btn.disabled = true;
                btn.textContent = 'Clearing...';
                fetch('/api/ingestion/resubmit/' + encodeURIComponent(docId), {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        if (data.status === 'ok') {{
                            btn.textContent = 'Done';
                            btn.style.color = '#28a745';
                            btn.style.borderColor = '#28a745';
                            // Refresh the page content
                            setTimeout(function() {{
                                fetch('/api/pipeline/ingestion')
                                    .then(function(r) {{ return r.text(); }})
                                    .then(function(html) {{
                                        document.getElementById('ingestion-content').innerHTML = html;
                                    }});
                            }}, 500);
                        }} else {{
                            btn.textContent = data.error || 'Error';
                            btn.disabled = false;
                        }}
                    }})
                    .catch(function(err) {{
                        btn.textContent = 'Error';
                        btn.disabled = false;
                    }});
            }}
        }});
    </script>
</body>
</html>
"""


def _build_analysis_html() -> str:
    """Build the group-level analysis dashboard content HTML."""
    import web as _web
    _db = _web._db

    groups = _db.get_group_dashboard()
    step_names, step_lookup = _analysis_step_names()

    def step_tooltip(sn):
        s = step_lookup[sn]
        deps = ", ".join(s.get("depends_on") or []) or "none"
        desc = s.get("description") or ""
        return f"{sn} (v{s['current_version']}): {desc}. Depends on: {deps}"

    header_cells = "".join(
        f'<th style="text-align:center;padding:4px 6px;font-size:0.8em;cursor:help;" title="{step_tooltip(sn)}">'
        f'{_STEP_DISPLAY_NAMES.get(sn, sn)}</th>'
        for sn in step_names
    )

    rows = []
    for group in groups:
        group_id = group["id"]
        name = html_mod.escape(group.get("name", ""))
        doc_count = group.get("doc_count", 0)
        gate_cells = ""
        for sn in step_names:
            permitted = group["gates"].get(sn, False)
            sv = step_lookup[sn]["current_version"]
            if permitted:
                label, bg, fg = f"v{sv}", "#d4edda", "#155724"
                title = f"Permitted (v{sv}) — click to disable"
            else:
                label, bg, fg = "off", "#e2e3e5", "#6c757d"
                title = f"Click to permit v{sv}"
            onclick = f"toggleGroupGate({group_id}, '{sn}', {str(not permitted).lower()})"
            gate_cells += (
                f'<td style="text-align:center;padding:4px 6px;cursor:pointer;" title="{title}" onclick="{onclick}">'
                f'<span style="padding:2px 6px;border-radius:8px;font-size:0.75em;'
                f'background:{bg};color:{fg};">{label}</span></td>'
            )

        rows.append(
            f'<tr>'
            f'<td style="font-size:0.85em;"><a href="/analysis?group={group_id}" style="color:#007bff;text-decoration:none;">{name}</a></td>'
            f'<td style="text-align:center;font-size:0.85em;color:#666;">{doc_count}</td>'
            f'{gate_cells}'
            f'</tr>'
        )

    rows_html = "\n".join(rows) if rows else (
        f'<tr><td colspan="{2 + len(step_names)}" style="text-align:center;color:#999;">No groups found. Run sync to create groups from sources.</td></tr>'
    )

    runnable_count = _db.count_runnable_steps()

    return f"""
    <div style="display:flex;gap:16px;margin-bottom:16px;align-items:center;">
        <div style="background:#f0f7ff;padding:8px 16px;border-radius:8px;">
            <strong>{len(groups)}</strong> groups
        </div>
        <div style="background:{'#fff3cd' if runnable_count > 0 else '#d4edda'};padding:8px 16px;border-radius:8px;">
            <strong>{runnable_count}</strong> runnable steps
        </div>
        <button id="pipeline-toggle" onclick="togglePipeline()"
                style="padding:6px 16px;background:{'#dc3545' if _web._pipeline_is_running() else '#007bff'};color:white;border:none;border-radius:4px;cursor:pointer;"
                {'disabled' if runnable_count == 0 and not _web._pipeline_is_running() else ''}>
            {'Stop Pipeline' if _web._pipeline_is_running() else 'Start Pipeline'}
        </button>
        <button onclick="refreshDashboard()" style="padding:6px 16px;background:#6c757d;color:white;border:none;border-radius:4px;cursor:pointer;">
            Refresh
        </button>
    </div>

    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;">
        <thead>
            <tr style="border-bottom:2px solid #dee2e6;">
                <th style="text-align:left;padding:4px 6px;">Group</th>
                <th style="text-align:center;padding:4px 6px;font-size:0.8em;">docs</th>
                {header_cells}
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    </div>

    <div style="margin-top:16px;font-size:0.8em;color:#999;">
        Click a cell to toggle permission for the group. Click a group name to see document details.
    </div>

    {_build_atlas_projection_panel(_db)}
    """


def _build_atlas_projection_panel(_db) -> str:
    """Corpus-level Atlas projection publish step (runs after motif/axes sweeps)."""
    try:
        from atlas_projection import LAST_PUBLICATION_KEY, PUBLIC_SOURCES
        last = _db.get_cache(LAST_PUBLICATION_KEY)
    except Exception:
        last, PUBLIC_SOURCES = None, []
    if last:
        status = last.get("status", "?")
        color = {"published": "#155724", "unchanged": "#155724",
                 "dry-run": "#856404"}.get(status.split(" ")[0], "#721c24")
        detail = (f"{status} · {last.get('exported', '?')} lessons exported "
                  f"({last.get('missing_analysis', '?')} missing analysis) · "
                  f"{last.get('registry_version', '')} · {last.get('at', '')}")
    else:
        color, detail = "#6c757d", "never run"
    srcs = html_mod.escape(", ".join(PUBLIC_SOURCES))
    return f"""
    <div style="margin-top:20px;padding:12px 16px;background:#f8f9fa;border-radius:8px;">
        <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;">
            <strong>Atlas projection</strong>
            <span style="font-size:0.85em;color:{color};">{html_mod.escape(detail)}</span>
            <button onclick="fetch('/api/analysis/publish-atlas', {{method:'POST'}})
                        .then(r=>r.json()).then(d=>alert(JSON.stringify(d)))
                        .then(()=>setTimeout(refreshDashboard, 3000))"
                    style="padding:4px 12px;background:#007bff;color:white;border:none;border-radius:4px;cursor:pointer;font-size:0.85em;">
                Publish now
            </button>
        </div>
        <div style="margin-top:6px;font-size:0.75em;color:#999;">
            Publishes automatically after pipeline sweeps that complete motifs/axes steps.
            Public sources: {srcs}. Requires ATLAS_PUBLISH credentials in .env for a real publish.
        </div>
    </div>
    """


def _build_analysis_group_html(group_id: int) -> str:
    """Build the document drill-down view for a specific group."""
    import web as _web
    _db = _web._db

    local_controls = _web.PIPELINE_LOCAL_CONTROLS

    group = _db.get_lesson_group(group_id)
    if not group:
        return "<p>Group not found.</p>"

    docs = _db.get_document_dashboard(group_id)
    step_names, step_lookup = _analysis_step_names()

    def step_tooltip(sn):
        s = step_lookup[sn]
        deps = ", ".join(s.get("depends_on") or []) or "none"
        desc = s.get("description") or ""
        return f"{sn} (v{s['current_version']}): {desc}. Depends on: {deps}"

    header_cells = "".join(
        f'<th style="text-align:center;padding:4px 6px;font-size:0.8em;cursor:help;" title="{step_tooltip(sn)}">'
        f'{_STEP_DISPLAY_NAMES.get(sn, sn)}</th>'
        for sn in step_names
    )

    rows = []
    for doc in docs:
        doc_id = str(doc["document_id"])
        title = doc.get("title", "")
        if len(title) > 50:
            title = title[:47] + "..."

        gate_cells = ""
        for sn in step_names:
            gate = doc["gates"].get(sn, {"state": "inactive"})
            state = gate["state"]
            cv = gate.get("completed_version")
            sv = gate.get("current_version")
            err = gate.get("error")

            if state == "current":
                label = f"v{cv}"
                bg, fg = "#d4edda", "#155724"
                title_attr = f"Current (v{cv})"
            elif state == "stale":
                label = f"v{cv}" if cv else "new"
                bg, fg = "#fff3cd", "#856404"
                title_attr = f"Stale — will run (current: v{sv})"
            elif state == "error":
                label = "err"
                bg, fg = "#f8d7da", "#721c24"
                err_safe = (err or 'unknown').replace('"', '&quot;').replace("'", "\\'")[:200]
                title_attr = f"Error: {err_safe} (click to clear)"
            else:
                label = "—"
                bg, fg = "#f8f9fa", "#adb5bd"
                title_attr = "Not started"

            onclick = ""
            if state == "error":
                onclick = f" onclick=\"clearError('{doc_id}', '{sn}')\" style=\"cursor:pointer;\""

            run_btn = ""
            if local_controls:
                run_btn = (
                    f'<button onclick="event.stopPropagation();runStep(\'{doc_id}\',\'{sn}\')" '
                    f'title="Run {sn}" '
                    f'style="font-size:0.6em;padding:1px 4px;margin-left:3px;border:1px solid #007bff;'
                    f'border-radius:3px;background:white;color:#007bff;cursor:pointer;vertical-align:middle;"'
                    f'>&#9654;</button>'
                )

            gate_cells += (
                f'<td style="text-align:center;padding:4px 6px;{"cursor:pointer;" if state == "error" else ""}" '
                f'title="{title_attr}"{onclick}>'
                f'<span style="padding:2px 6px;border-radius:8px;font-size:0.75em;'
                f'background:{bg};color:{fg};">{label}</span>{run_btn}</td>'
            )

        rows.append(
            f'<tr>'
            f'<td style="font-size:0.85em;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{html_mod.escape(doc.get("title", ""))}">'
            f'<a href="/lesson/{doc_id}" style="color:#007bff;text-decoration:none;">{html_mod.escape(title)}</a></td>'
            f'{gate_cells}'
            f'</tr>'
        )

    rows_html = "\n".join(rows) if rows else (
        f'<tr><td colspan="{1 + len(step_names)}" style="text-align:center;color:#999;">No documents in this group</td></tr>'
    )

    group_name = html_mod.escape(group.get("name", ""))

    return f"""
    <div style="margin-bottom:16px;">
        <a href="/analysis" style="color:#007bff;text-decoration:none;">&larr; Back to groups</a>
        <h2 style="margin:8px 0;">{group_name}</h2>
        <span style="color:#666;font-size:0.9em;">{len(docs)} documents</span>
    </div>

    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;">
        <thead>
            <tr style="border-bottom:2px solid #dee2e6;">
                <th style="text-align:left;padding:4px 6px;">Document</th>
                {header_cells}
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    </div>

    <div style="margin-top:16px;font-size:0.8em;color:#999;">
        Colors:
        <span style="background:#d4edda;padding:1px 6px;border-radius:4px;">current</span>
        <span style="background:#fff3cd;padding:1px 6px;border-radius:4px;">stale (will run)</span>
        <span style="background:#f8d7da;padding:1px 6px;border-radius:4px;">error (click to clear)</span>
        <span style="background:#f8f9fa;padding:1px 6px;border-radius:4px;color:#adb5bd;">not started</span>
    </div>
    """


def _build_analysis_page(group_id: int | None = None) -> str:
    """Build the full analysis dashboard HTML page."""
    import web as _web
    _db = _web._db

    if not _db:
        return """
<!DOCTYPE html><html><head><title>Analysis Pipeline</title>
<style>body { font-family: -apple-system, sans-serif; max-width: 1400px; margin: 0 auto; padding: 20px; }</style>
</head><body><h1>Analysis Pipeline</h1><p>Database not configured.</p></body></html>"""

    if group_id is not None:
        content = _build_analysis_group_html(group_id)
        status_panels = ""
    else:
        content = _build_analysis_html()
        status_panels = _build_pipeline_status_html()

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Analysis Pipeline - Feldenkrais</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; }}
        .nav {{ margin-bottom: 20px; }}
        .nav a {{ color: #007bff; text-decoration: none; margin-right: 16px; }}
        .nav a:hover {{ text-decoration: underline; }}
        table {{ border-collapse: collapse; }}
        th, td {{ border-bottom: 1px solid #eee; }}
        th {{ position: sticky; top: 0; background: white; z-index: 1; }}
        tr:hover {{ background: #f8f9fa; }}
    </style>
</head>
<body>
    <div class="nav">
        <a href="/">&larr; Home</a>
        <a href="/ingestion">Ingestion</a>
        <a href="/analysis">Analysis</a>
    </div>
    <h1>Analysis Pipeline</h1>
    <p style="color:#666;font-size:0.9em;margin-bottom:16px;">
        Toggle steps at the group level. Click a group name to see per-document completion states.
        Only permitted + pending steps will run when you click "Run Pipeline".
    </p>
    <details style="margin-bottom:16px;font-size:0.85em;">
        <summary style="cursor:pointer;color:#007bff;">Show step dependency graph</summary>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px;overflow-x:auto;font-size:0.8em;line-height:1.4;">
decompose ──┬──► somatic ──┬──► condense ──► embed
            │              │
            ├──► language ──┘
            │
            ├──► concept_tags ──► index_concepts
            │    (needs decompose + somatic)
            │
            └──► spatial ──► index_spatial
                 (needs decompose + somatic)

language_patterns (depends on decompose + language)
extract_steps ──► map_body_parts (independent)</pre>
    </details>

    <div id="analysis-content">
        {content}
    </div>

    <div id="analysis-status">
        {status_panels}
    </div>

    <script>
    function clearAllErrors() {{
        if (!confirm('Clear all errors? These steps will become runnable again.')) return;
        fetch('/api/analysis/clear-all-errors', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: '{{}}'
        }})
        .then(r => r.json())
        .then(data => {{
            if (data.status === 'ok') {{
                refreshDashboard();
            }} else alert(data.error || 'Error');
        }})
        .catch(err => alert('Error: ' + err));
    }}

    function toggleGroupGate(groupId, stepName, newPermitted) {{
        fetch('/api/analysis/gate', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{group_id: groupId, step_name: stepName, permitted: newPermitted}})
        }})
        .then(r => r.json())
        .then(data => {{
            if (data.status === 'ok') refreshDashboard();
            else alert(data.error || 'Error');
        }})
        .catch(err => alert('Error: ' + err));
    }}

    function clearError(docId, stepName) {{
        fetch('/api/analysis/clear-error', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{document_id: docId, step_name: stepName}})
        }})
        .then(r => r.json())
        .then(data => {{
            if (data.status === 'ok') refreshDashboard();
            else alert(data.error || 'Error');
        }})
        .catch(err => alert('Error: ' + err));
    }}

    function runStep(docId, stepName) {{
        const btn = event.target;
        btn.disabled = true;
        btn.textContent = '...';
        fetch('/api/analysis/run-step', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{document_id: docId, step_name: stepName}})
        }})
        .then(r => r.json())
        .then(data => {{
            if (data.status === 'running') {{
                // Start polling for completion
                pollStepStatus(docId, stepName, btn);
            }} else {{
                alert(data.error || 'Error');
                btn.disabled = false;
                btn.textContent = '\\u25b6';
            }}
        }})
        .catch(err => {{
            alert('Error: ' + err);
            btn.disabled = false;
            btn.textContent = '\\u25b6';
        }});
    }}

    function pollStepStatus(docId, stepName, btn) {{
        // Refresh the dashboard every 2s until the step is no longer stale
        let polls = 0;
        const maxPolls = 90; // 3 minutes
        const timer = setInterval(() => {{
            polls++;
            refreshDashboard();
            if (polls >= maxPolls) {{
                clearInterval(timer);
                btn.disabled = false;
                btn.textContent = '\\u25b6';
            }}
        }}, 2000);

        // Also check completion by looking at the step state
        const checkDone = setInterval(() => {{
            fetch('/api/analysis/group/' + new URL(window.location).searchParams.get('group'))
                .then(r => r.text())
                .then(html => {{
                    // If step is no longer stale (it completed or errored), stop polling
                    // We detect this by checking if the button still says '...'
                    const newBtn = document.querySelector(`button[onclick*="runStep('${{docId}}','${{stepName}}')"]`);
                    if (!newBtn || newBtn.textContent !== '...') {{
                        clearInterval(timer);
                        clearInterval(checkDone);
                    }}
                }});
        }}, 3000);
    }}

    function refreshDashboard() {{
        const url = new URL(window.location);
        const groupId = url.searchParams.get('group');
        const endpoint = groupId
            ? '/api/analysis/group/' + groupId
            : '/api/analysis/dashboard';
        fetch(endpoint)
            .then(r => r.text())
            .then(html => {{
                document.getElementById('analysis-content').innerHTML = html;
            }});
        // Refresh status panels (only on group view)
        const statusEl = document.getElementById('analysis-status');
        if (statusEl && !groupId) {{
            fetch('/api/analysis/status')
                .then(r => r.text())
                .then(html => {{ statusEl.innerHTML = html; }});
        }}
        // Update pipeline button state
        fetch('/api/analysis/pipeline-status')
            .then(r => r.json())
            .then(data => {{
                const btn = document.getElementById('pipeline-toggle');
                if (btn) {{
                    btn.style.background = data.running ? '#dc3545' : '#007bff';
                    btn.textContent = data.running ? 'Stop Pipeline' : 'Start Pipeline';
                    btn.disabled = false;
                }}
            }});
    }}

    // Auto-refresh every 10s while the pipeline is running. Also refresh once
    // after running -> stopped so the dashboard catches the final step that
    // completed in the same tick the worker shut down.
    let _prevPipelineRunning = false;
    setInterval(() => {{
        fetch('/api/analysis/pipeline-status')
            .then(r => r.json())
            .then(data => {{
                if (data.running || _prevPipelineRunning) refreshDashboard();
                _prevPipelineRunning = data.running;
            }});
    }}, 10000);

    function togglePipeline() {{
        fetch('/api/analysis/run', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: '{{}}'
        }})
        .then(r => r.json())
        .then(data => {{
            refreshDashboard();
        }})
        .catch(err => alert('Error: ' + err));
    }}
    </script>
</body>
</html>
"""


def _build_pipeline_status_html() -> str:
    """Build the pipeline progress + errors panels."""
    import web as _web
    _db = _web._db

    progress = _db.get_pipeline_progress()
    errors = _db.get_pipeline_errors()

    # Progress table: group x step with completion bars
    if progress:
        # Collect unique groups and steps in order
        groups_seen = {}
        steps_seen = []
        for row in progress:
            if row["group_id"] not in groups_seen:
                groups_seen[row["group_id"]] = row["group_name"]
            if row["step_name"] not in steps_seen:
                steps_seen.append(row["step_name"])

        # Build a lookup
        lookup = {}
        for row in progress:
            lookup[(row["group_id"], row["step_name"])] = row

        header = "".join(
            f'<th style="text-align:center;padding:4px 8px;font-size:0.8em;">'
            f'{_STEP_DISPLAY_NAMES.get(sn, sn)}</th>'
            for sn in steps_seen
        )

        rows = []
        for gid, gname in groups_seen.items():
            cells = ""
            for sn in steps_seen:
                r = lookup.get((gid, sn))
                if not r:
                    cells += '<td style="text-align:center;color:#ccc;">—</td>'
                    continue
                total = r["total"]
                done = r["completed"]
                stale = r["stale"]
                errs = r["errors"]
                pending = total - done - stale - errs
                pct = int(100 * done / total) if total > 0 else 0

                if errs > 0:
                    color = "#dc3545"
                    label = f"{done}/{total} ({errs} err)"
                elif done == total:
                    color = "#28a745"
                    label = f"{done}/{total}"
                elif done > 0 or stale > 0:
                    color = "#ffc107"
                    label = f"{done}/{total}"
                else:
                    color = "#6c757d"
                    label = f"0/{total}"

                cells += (
                    f'<td style="text-align:center;padding:4px 8px;font-size:0.8em;">'
                    f'<div style="background:#e9ecef;border-radius:4px;overflow:hidden;height:18px;position:relative;">'
                    f'<div style="background:{color};height:100%;width:{pct}%;"></div>'
                    f'<span style="position:absolute;top:0;left:0;right:0;text-align:center;font-size:0.75em;line-height:18px;">{label}</span>'
                    f'</div></td>'
                )

            rows.append(
                f'<tr><td style="font-size:0.85em;padding:4px 8px;">{html_mod.escape(gname)}</td>{cells}</tr>'
            )

        progress_html = f"""
        <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="border-bottom:2px solid #dee2e6;">
                <th style="text-align:left;padding:4px 8px;">Group</th>{header}
            </tr></thead>
            <tbody>{"".join(rows)}</tbody>
        </table>
        """
    else:
        progress_html = '<p style="color:#999;">No steps are permitted yet. Toggle gates above to enable analysis.</p>'

    # Errors list
    if errors:
        error_rows = []
        for e in errors:
            doc_id = str(e["document_id"])
            title = html_mod.escape(e.get("title", "")[:50])
            step = e["step_name"]
            err_text = html_mod.escape((e.get("error") or "")[:120])
            updated = str(e.get("updated_at") or "")[:16]
            error_rows.append(
                f'<tr>'
                f'<td style="font-size:0.85em;padding:4px 8px;">{title}</td>'
                f'<td style="font-size:0.8em;padding:4px 8px;">{step}</td>'
                f'<td style="font-size:0.8em;padding:4px 8px;color:#721c24;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{err_text}">{err_text}</td>'
                f'<td style="font-size:0.75em;padding:4px 8px;color:#999;">{updated}</td>'
                f'<td style="padding:4px 8px;"><button onclick="clearError(\'{doc_id}\', \'{step}\')" '
                f'style="font-size:0.75em;padding:2px 8px;border:1px solid #ccc;border-radius:4px;cursor:pointer;background:white;">clear</button></td>'
                f'</tr>'
            )
        errors_html = f"""
        <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="border-bottom:2px solid #dee2e6;">
                <th style="text-align:left;padding:4px 8px;">Document</th>
                <th style="text-align:left;padding:4px 8px;">Step</th>
                <th style="text-align:left;padding:4px 8px;">Error</th>
                <th style="text-align:left;padding:4px 8px;">When</th>
                <th></th>
            </tr></thead>
            <tbody>{"".join(error_rows)}</tbody>
        </table>
        """
    else:
        errors_html = '<p style="color:#999;">No errors.</p>'

    # Overall counts
    total_steps = sum(r["total"] for r in progress)
    completed_steps = sum(r["completed"] for r in progress)
    error_steps = sum(r["errors"] for r in progress)
    remaining = total_steps - completed_steps - error_steps

    if progress:
        rate = _db.get_recent_completion_rate(5)
        if rate > 0 and remaining > 0:
            eta_minutes = remaining / rate
            if eta_minutes >= 60:
                eta_str = f" — ~{int(eta_minutes // 60)}h{int(eta_minutes % 60)}m"
            else:
                eta_str = f" — ~{int(eta_minutes)}m"
        else:
            eta_str = ""
        remaining_badge = (
            f'<span style="font-size:0.75em;font-weight:normal;margin-left:12px;'
            f'padding:2px 8px;border-radius:8px;'
            f'background:{"#fff3cd" if remaining > 0 else "#d4edda"};">'
            f'{remaining} remaining{eta_str}</span>'
        )
    else:
        remaining_badge = ""

    # Active steps
    import datetime
    active_data = _db.get_cache(_web._PIPELINE_ACTIVE_KEY) if _db else None
    active_steps = (active_data or {}).get("steps", [])
    now = datetime.datetime.utcnow()
    if active_steps and _web._pipeline_is_running():
        active_items = ""
        for s in active_steps:
            started = s.get("started")
            if started:
                try:
                    elapsed = (now - datetime.datetime.fromisoformat(started)).total_seconds()
                    if elapsed >= 60:
                        elapsed_str = f"{int(elapsed // 60)}m{int(elapsed % 60)}s"
                    else:
                        elapsed_str = f"{int(elapsed)}s"
                except (ValueError, TypeError):
                    elapsed_str = ""
            else:
                elapsed_str = ""
            time_span = f' <span style="color:#999;font-size:0.8em;">({elapsed_str})</span>' if elapsed_str else ""
            active_items += f'<li style="font-size:0.85em;"><strong>{html_mod.escape(s["step"])}</strong> — {html_mod.escape(s["title"])}{time_span}</li>'
        active_html = f'<ul style="margin:0;padding-left:20px;">{active_items}</ul>'
    else:
        active_html = ""

    return f"""
    <div style="margin-top:32px;">
        <h2 style="font-size:1.1em;margin-bottom:12px;">Pipeline Progress{remaining_badge}</h2>
        {progress_html}
    </div>

    {f'<div style="margin-top:32px;"><h2 style="font-size:1.1em;margin-bottom:8px;">Currently Running</h2>{active_html}</div>' if active_html else ''}

    <div style="margin-top:32px;">
        <h2 style="font-size:1.1em;margin-bottom:12px;">
            Errors
            <span style="font-size:0.8em;color:#dc3545;font-weight:normal;">
                {f'({len(errors)})' if errors else ''}
            </span>
            {f'<button onclick="clearAllErrors()" style="font-size:0.7em;padding:2px 10px;margin-left:8px;border:1px solid #ccc;border-radius:4px;cursor:pointer;background:white;vertical-align:middle;">Clear all &amp; retry</button>' if errors else ''}
        </h2>
        {errors_html}
    </div>
    """


def build_pipeline_html() -> str:
    """Build the pipeline dashboard HTML page (book pipeline only)."""
    import web as _web
    _db = _web._db

    if not _db:
        return """
<!DOCTYPE html><html><head><title>Pipeline Dashboard</title>
<style>body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }</style>
</head><body><h1>Pipeline Dashboard</h1><p>Database not configured. Set DATABASE_URL to enable.</p>
<p><a href="/">&larr; Home</a></p></body></html>"""

    # Book pipeline section (injected by web_book router when available)
    book_pipeline_fn = getattr(_web.app.state, 'build_book_pipeline_section', None)
    if book_pipeline_fn:
        try:
            book_pipeline_html = book_pipeline_fn()
        except Exception as e:
            book_pipeline_html = f'<p style="color: #999;">Book pipeline error: {e}</p>'
    else:
        book_pipeline_html = '<p style="color: #999;">Book pipeline not available.</p>'

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Pipeline Dashboard - Feldenkrais</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; }}
        h2 {{ color: #555; border-bottom: 1px solid #ddd; padding-bottom: 5px; margin-top: 30px; }}
        .nav {{ margin-bottom: 20px; }}
        .nav a {{ color: #007bff; text-decoration: none; }}
        .nav a:hover {{ text-decoration: underline; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        th, td {{
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        th {{
            background: #f0f0f0;
            font-weight: bold;
            font-size: 0.9em;
        }}
        tr:hover {{ background: #f8f9fa; }}
        table.compact th, table.compact td {{
            padding: 3px 10px;
            font-size: 0.88em;
            line-height: 1.3;
        }}
        th.sortable {{
            cursor: pointer;
            user-select: none;
        }}
        th.sortable:hover {{
            background: #e0e0e0;
        }}
    </style>
</head>
<body>
    <div class="nav"><a href="/">&larr; Home</a></div>
    <h1>Pipeline Dashboard</h1>

    <div id="tab-analysis">
        {book_pipeline_html}
    </div>

    <script>
        // Event delegation for toggle, rebuild, run, add-lesson, and remove-lesson buttons
        var analysisTab = document.getElementById('tab-analysis');
        if (analysisTab) analysisTab.addEventListener('click', function(e) {{
            var btn = e.target;
            if (btn.classList.contains('toggle-btn')) {{
                var node = btn.dataset.node;
                btn.disabled = true;
                btn.textContent = '...';
                fetch('/api/pipeline/toggle/' + encodeURIComponent(node), {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        var row = btn.closest('tr');
                        if (data.enabled) {{
                            btn.textContent = 'on';
                            btn.style.color = '#28a745';
                            btn.style.borderColor = '#28a745';
                            row.style.opacity = '1';
                        }} else {{
                            btn.textContent = 'off';
                            btn.style.color = '#999';
                            btn.style.borderColor = '#999';
                            row.style.opacity = '0.4';
                        }}
                        btn.disabled = false;
                    }})
                    .catch(function() {{
                        btn.textContent = 'err';
                        btn.disabled = false;
                    }});
            }}
            if (btn.classList.contains('rebuild-btn')) {{
                var node = btn.dataset.node;
                btn.disabled = true;
                btn.textContent = '...';
                fetch('/api/pipeline/rebuild/' + encodeURIComponent(node), {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        if (data.status === 'invalidated') {{
                            var row = btn.closest('tr');
                            var statusCell = row.querySelectorAll('td')[1];
                            if (statusCell) {{
                                statusCell.innerHTML = '<span style="color: #ffc107; font-weight: bold;">STALE</span>';
                            }}
                        }}
                        btn.textContent = 'invalidate';
                        btn.disabled = false;
                    }})
                    .catch(function() {{
                        btn.textContent = 'err';
                        btn.style.color = '#dc3545';
                        btn.disabled = false;
                    }});
            }}
            // Run pipeline for a lesson
            if (btn.classList.contains('run-lesson-btn')) {{
                var lessonId = btn.dataset.lesson;
                btn.disabled = true;
                var orig = btn.textContent;
                btn.textContent = 'Starting...';
                fetch('/api/pipeline/run/' + encodeURIComponent(lessonId), {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        btn.textContent = data.status === 'started' ? 'Running...' : (data.status || 'done');
                    }})
                    .catch(function() {{
                        btn.textContent = 'Error';
                        btn.disabled = false;
                    }});
            }}
            // Remove lesson from book
            if (btn.classList.contains('remove-lesson-btn')) {{
                var lessonId = btn.dataset.lesson;
                if (!confirm('Remove this lesson from the book?')) return;
                btn.disabled = true;
                btn.textContent = '...';
                fetch('/api/pipeline/book-lessons/remove', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{lesson_id: lessonId}})
                }})
                    .then(function() {{ location.reload(); }})
                    .catch(function() {{ btn.textContent = 'err'; btn.disabled = false; }});
            }}
            // Add lesson to book
            if (btn.id === 'add-lesson-btn') {{
                var sel = document.getElementById('add-lesson-select');
                var val = sel.value;
                if (!val) return;
                btn.disabled = true;
                btn.textContent = '...';
                fetch('/api/pipeline/book-lessons', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{lesson_id: val, title: sel.options[sel.selectedIndex].text}})
                }})
                    .then(function() {{ location.reload(); }})
                    .catch(function() {{ btn.textContent = 'err'; btn.disabled = false; }});
            }}
        }});

        // Source filter: rebuild lesson select based on selected source
        (function() {{
            var sel = document.getElementById('add-lesson-select');
            var filter = document.getElementById('source-filter');
            if (!sel || !filter) return;
            var allOpts = [];
            for (var i = 1; i < sel.options.length; i++) {{
                var o = sel.options[i];
                allOpts.push({{value: o.value, text: o.text, source: o.dataset.source}});
            }}
            filter.addEventListener('change', function() {{
                var sourceId = filter.value;
                while (sel.options.length > 1) sel.remove(1);
                allOpts.forEach(function(item) {{
                    if (sourceId === 'all' || item.source === sourceId) {{
                        var opt = document.createElement('option');
                        opt.value = item.value;
                        opt.textContent = item.text;
                        opt.dataset.source = item.source;
                        sel.appendChild(opt);
                    }}
                }});
            }});
        }})();

        // Auto-refresh: poll pipeline status every 5 seconds
        (function() {{
            var container = document.getElementById('tab-analysis');
            if (!container) return;
            var refreshInterval = 5000;
            var paused = false;

            function refresh() {{
                if (paused) return;
                fetch('/api/pipeline/status')
                    .then(function(r) {{ return r.text(); }})
                    .then(function(html) {{
                        container.innerHTML = html;
                    }})
                    .catch(function(e) {{
                        console.error('Pipeline refresh failed:', e);
                    }});
            }}

            setInterval(refresh, refreshInterval);

            container.addEventListener('mousedown', function() {{ paused = true; }});
            document.addEventListener('mouseup', function() {{
                setTimeout(function() {{ paused = false; }}, 1000);
            }});
        }})();
    </script>
</body>
</html>
"""


def _build_finder_html():
    """Build the finder landing page HTML."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Feldenkrais Lesson Finder</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #fafafa; color: #333; min-height: 100vh;
         display: flex; flex-direction: column; align-items: center; }}
  .hero {{ text-align: center; padding: 60px 20px 30px; max-width: 640px; }}
  .hero h1 {{ font-size: 1.8rem; font-weight: 600; margin-bottom: 8px; }}
  .hero p {{ color: #666; font-size: 1rem; margin-bottom: 30px; }}
  .search-box {{ display: flex; gap: 8px; max-width: 560px; width: 100%; margin: 0 auto; }}
  .search-box input {{ flex: 1; padding: 12px 16px; font-size: 1rem; border: 1px solid #ccc;
                       border-radius: 8px; outline: none; }}
  .search-box input:focus {{ border-color: #4a90d9; box-shadow: 0 0 0 2px rgba(74,144,217,0.2); }}
  .search-box button {{ padding: 12px 24px; font-size: 1rem; background: #4a90d9; color: #fff;
                        border: none; border-radius: 8px; cursor: pointer; font-weight: 500; }}
  .search-box button:hover {{ background: #3a7bc8; }}
  .results {{ max-width: 640px; width: 100%; padding: 20px; }}
  .result-card {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 8px;
                  padding: 16px; margin-bottom: 12px; }}
  .result-card h3 {{ font-size: 1rem; margin-bottom: 4px; }}
  .result-card h3 a {{ color: #2c5282; text-decoration: none; }}
  .result-card h3 a:hover {{ text-decoration: underline; }}
  .result-card .desc {{ color: #555; font-size: 0.9rem; margin-bottom: 8px; }}
  .result-card .meta {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .tag {{ background: #edf2f7; color: #4a5568; padding: 2px 8px; border-radius: 4px;
          font-size: 0.78rem; }}
  .source {{ color: #888; font-size: 0.82rem; }}
  .score {{ color: #aaa; font-size: 0.78rem; }}
  .empty {{ text-align: center; color: #999; padding: 40px 0; }}
  .loading {{ text-align: center; padding: 30px 0; color: #888; }}
  .stats {{ text-align: center; color: #aaa; font-size: 0.82rem; margin-top: 4px; }}
</style>
</head>
<body>
<div class="hero">
  <h1>Feldenkrais Lesson Finder</h1>
  <p>Search across hundreds of Feldenkrais lessons from around the web.</p>
  <div class="search-box">
    <input type="text" id="q" placeholder="e.g. rolling from back to sitting, or jaw and tongue..."
           autofocus autocomplete="off">
    <button onclick="doSearch()">Search</button>
  </div>
  <div class="stats" id="stats"></div>
</div>
<div class="results" id="results"></div>
<script>
const input = document.getElementById('q');
const results = document.getElementById('results');
const stats = document.getElementById('stats');

// Load stats on page load
fetch('/api/finder/stats').then(r=>r.json()).then(d=>{{
  stats.textContent = d.total + ' lessons indexed from ' + d.sources + ' sources';
}}).catch(()=>{{}});

input.addEventListener('keydown', e => {{ if (e.key === 'Enter') doSearch(); }});

async function doSearch() {{
  const q = input.value.trim();
  if (!q) return;
  results.innerHTML = '<div class="loading">Searching...</div>';
  try {{
    const resp = await fetch('/api/finder/search?q=' + encodeURIComponent(q));
    const data = await resp.json();
    if (!data.results || data.results.length === 0) {{
      results.innerHTML = '<div class="empty">No results found. Try different keywords.</div>';
      return;
    }}
    results.innerHTML = data.results.map(r => {{
      const tags = (r.tags || []).slice(0, 6).map(t => '<span class="tag">' + t + '</span>').join('');
      const desc = r.description ? '<div class="desc">' + r.description.slice(0, 200) +
                   (r.description.length > 200 ? '...' : '') + '</div>' : '';
      const link = r.url ? '<a href="' + r.url + '" target="_blank" rel="noopener">' + r.title + '</a>'
                         : r.title;
      const source = r.source_name ? '<span class="source">' + r.source_name + '</span>' : '';
      return '<div class="result-card"><h3>' + link + '</h3>' + desc +
             '<div class="meta">' + source + tags + '</div></div>';
    }}).join('');
  }} catch(e) {{
    results.innerHTML = '<div class="empty">Search failed. Please try again.</div>';
  }}
}}
</script>
</body>
</html>"""
