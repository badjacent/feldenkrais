"""Pipeline dashboard section builder."""

from book.state import _db


def _build_book_pipeline_section() -> str:
    """Build the Book Pipeline status section for the dashboard."""
    from pipeline_graph import build_book_pipeline, get_book_lessons
    from pipeline import PipelineStateStore, PipelineExecutor, NodeStatus

    lessons = get_book_lessons(_db)
    graph = build_book_pipeline(_db, lesson_entries=lessons)
    store = PipelineStateStore(_db)
    store.initialize()

    # Handle empty graph (no lessons)
    if not graph.nodes:
        return """
        <p style="color: #999; padding: 20px 0;">
            No lessons registered. Use the form below to add a lesson to the book.
        </p>
        """ + _build_lesson_manager_html(lessons)

    executor = PipelineExecutor(graph, store)
    states = executor.compute_staleness()
    order = graph.topological_order()
    enabled_flags = store.load_enabled_flags()

    stale_count = sum(
        1 for s in states.values()
        if s.status in (NodeStatus.STALE, NodeStatus.PENDING, NodeStatus.ERROR)
    )
    fresh_count = sum(1 for s in states.values() if s.status == NodeStatus.FRESH)
    running_count = sum(1 for s in states.values() if s.status == NodeStatus.RUNNING)
    total = len(states)

    status_colors = {
        "fresh": "#28a745",
        "stale": "#ffc107",
        "pending": "#6c757d",
        "error": "#dc3545",
        "running": "#17a2b8",
    }

    # Build per-lesson summary cards with Run buttons
    lesson_cards = []
    for entry in lessons:
        lid = entry["lesson_id"]
        ltitle = entry.get("title", "Untitled")
        # Count this lesson's node states
        lesson_nodes = {n: s for n, s in states.items() if n.startswith(f"{lid}:")}
        l_fresh = sum(1 for s in lesson_nodes.values() if s.status == NodeStatus.FRESH)
        l_stale = sum(1 for s in lesson_nodes.values()
                      if s.status in (NodeStatus.STALE, NodeStatus.PENDING, NodeStatus.ERROR))
        l_running = sum(1 for s in lesson_nodes.values() if s.status == NodeStatus.RUNNING)
        l_total = len(lesson_nodes)

        if l_running > 0:
            badge = f'<span style="color: #17a2b8; font-weight: bold;">RUNNING ({l_running})</span>'
            run_btn = f'<button class="run-lesson-btn" data-lesson="{lid}" disabled style="opacity:0.5;background:#eee;border:1px solid #ccc;border-radius:4px;padding:4px 12px;font-size:0.85em;">Running...</button>'
        elif l_stale > 0:
            badge = f'<span style="color: #ffc107; font-weight: bold;">{l_stale} stale</span>'
            run_btn = f'<button class="run-lesson-btn" data-lesson="{lid}" style="background:#007bff;color:#fff;border:none;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:0.85em;">Run Pipeline</button>'
        else:
            badge = f'<span style="color: #28a745;">All fresh</span>'
            run_btn = f'<button class="run-lesson-btn" data-lesson="{lid}" style="background:none;border:1px solid #28a745;color:#28a745;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:0.85em;">Re-run</button>'

        lesson_cards.append(f"""
        <div class="card" style="text-align: left; padding: 12px;">
            <div style="font-weight: bold; margin-bottom: 4px;">{ltitle}</div>
            <div style="font-size: 0.8em; color: #999; margin-bottom: 6px; font-family: monospace;">{lid[:12]}...</div>
            <div style="font-size: 0.85em; margin-bottom: 8px;">{l_fresh}/{l_total} fresh &mdash; {badge}</div>
            <div>{run_btn}</div>
        </div>
        """)

    lesson_cards_html = "\n".join(lesson_cards) if lesson_cards else ""

    # ── Group nodes by lesson vs global ──
    # Build a lookup from lesson_id -> title
    lesson_title_map = {e["lesson_id"]: e.get("title", "Untitled") for e in lessons}

    # Classify every node into a lesson group or global
    from collections import OrderedDict
    lesson_groups: OrderedDict[str, list[str]] = OrderedDict()  # lid -> [node_names]
    global_nodes: list[str] = []

    for name in order:
        spec = graph.nodes[name]
        lid = spec.tags.get("lesson_id")
        if lid:
            lesson_groups.setdefault(lid, []).append(name)
        else:
            global_nodes.append(name)

    def _node_row(name: str, strip_prefix: str = "") -> str:
        """Render a single node table row."""
        state = states.get(name)
        if not state:
            return ""
        spec = graph.nodes[name]
        status_val = state.status.value
        color = status_colors.get(status_val, "#333")
        is_transform = spec.kind.value == "transform"

        # Strip lesson_id prefix from display name
        label = name
        if strip_prefix and label.startswith(strip_prefix):
            label = label[len(strip_prefix):]
        display_name = f"<strong>{label}</strong>" if is_transform else f'<span style="color: #888;">{label}</span>'
        duration = f"{state.duration_seconds}s" if state.duration_seconds else "&mdash;"
        status_label = status_val.upper() if status_val != "fresh" else "fresh"

        error_row = ""
        if state.status == NodeStatus.ERROR and state.error_message:
            error_msg = state.error_message[:80]
            error_row = f'<tr><td></td><td colspan="4" style="color: #dc3545; font-size: 0.8em; padding-top: 0;">{error_msg}</td></tr>'

        if state.last_run_at:
            from datetime import timezone, timedelta, datetime
            utc_str = str(state.last_run_at)
            try:
                dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                eastern = timezone(timedelta(hours=-5))
                dt_est = dt.astimezone(eastern)
                last_run = dt_est.strftime("%Y-%m-%d %H:%M")
                sort_time = dt_est.isoformat()[:19]
            except Exception:
                last_run = utc_str[:16].replace("T", " ")
                sort_time = utc_str[:19]
        else:
            last_run = "&mdash;"
            sort_time = ""
        sort_dur = str(state.duration_seconds) if state.duration_seconds else ""
        kind = "transform" if is_transform else "source"
        node_enabled = enabled_flags.get(name, True)
        row_opacity = "1" if node_enabled else "0.4"
        toggle_label = "on" if node_enabled else "off"
        toggle_color = "#28a745" if node_enabled else "#999"

        return f"""
            <tr data-kind="{kind}" data-time="{sort_time}" data-dur="{sort_dur}" data-status="{status_val}" style="opacity: {row_opacity};">
                <td>{display_name}</td>
                <td><span style="color: {color}; font-weight: {'bold' if status_val != 'fresh' else 'normal'};">{status_label}</span></td>
                <td>{last_run}</td>
                <td>{duration}</td>
                <td><button class="toggle-btn" data-node="{name}" style="background: none; border: 1px solid {toggle_color}; color: {toggle_color}; border-radius: 3px; padding: 1px 8px; cursor: pointer; font-size: 0.8em;">{toggle_label}</button></td>
                <td>{"<button class='rebuild-btn' data-node='" + name + "' style='background: none; border: 1px solid #17a2b8; color: #17a2b8; border-radius: 3px; padding: 1px 8px; cursor: pointer; font-size: 0.8em;'>invalidate</button>" if is_transform else ""}</td>
            </tr>
            {error_row}
        """

    # Build grouped rows: lesson groups first (only if they have non-fresh nodes), then global
    rows = []
    for lid, node_names in lesson_groups.items():
        # Skip groups where every node is fresh
        has_non_fresh = any(
            states.get(n) and states[n].status != NodeStatus.FRESH
            for n in node_names
        )
        if not has_non_fresh:
            continue
        title = lesson_title_map.get(lid, lid[:12])
        rows.append(f"""
            <tr class="group-header">
                <td colspan="6" style="background: #f0f0f0; font-weight: bold; padding: 8px 6px 6px; border-top: 2px solid #ccc;">
                    {title} <span style="color: #999; font-weight: normal; font-family: monospace; font-size: 0.8em;">({lid[:12]}...)</span>
                </td>
            </tr>
        """)
        prefix = f"{lid}:"
        for n in node_names:
            rows.append(_node_row(n, strip_prefix=prefix))

    # Global / shared nodes
    if global_nodes:
        rows.append(f"""
            <tr class="group-header">
                <td colspan="6" style="background: #f0f0f0; font-weight: bold; padding: 8px 6px 6px; border-top: 2px solid #ccc;">
                    Shared / Global
                </td>
            </tr>
        """)
        for n in global_nodes:
            rows.append(_node_row(n))

    rows_html = "\n".join(rows)

    return f"""
    <h3>Lessons in Book</h3>
    <div class="cards" style="margin-top: 10px;">
        {lesson_cards_html}
    </div>

    {_build_lesson_manager_html(lessons)}

    <h3 style="margin-top: 24px;">Pipeline Nodes</h3>
    <div class="cards" style="margin-top: 10px;">
        <div class="card">
            <div class="value">{total}</div>
            <div class="label">Total Nodes</div>
        </div>
        <div class="card">
            <div class="value" style="color: #28a745;">{fresh_count}</div>
            <div class="label">Fresh</div>
        </div>
        <div class="card">
            <div class="value" style="color: {('#ffc107' if stale_count else '#333')};">{stale_count}</div>
            <div class="label">Stale / Pending</div>
        </div>
        <div class="card">
            <div class="value" style="color: {('#17a2b8' if running_count else '#333')};">{running_count}</div>
            <div class="label">Running</div>
        </div>
    </div>
    <table class="compact" id="pipeline-table">
        <thead>
            <tr>
                <th class="sortable" data-col="node">Node</th>
                <th class="sortable" data-col="status">Status</th>
                <th class="sortable active desc" data-col="time">Last Run &#x25BC;</th>
                <th class="sortable" data-col="dur">Duration</th>
                <th>Enabled</th>
                <th></th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    <p style="font-size: 0.85em; color: #999; margin-top: 10px;">
        Rebuild stale nodes: <code>.venv/bin/python src/kb/pipeline.py run --lesson LESSON_ID</code>
    </p>
    """


def _build_lesson_manager_html(lessons: list[dict]) -> str:
    """Build the lesson picker / add form for the dashboard."""
    # Build a list of available lessons grouped by source
    sources = []
    available_by_source: dict[int, list[dict]] = {}
    if _db:
        with _db.get_cursor(commit=False) as cur:
            # Load sources
            cur.execute(
                "SELECT id, name, category_name FROM document_source ORDER BY COALESCE(category_name, name)"
            )
            sources = [dict(r) for r in cur.fetchall()]

            # Load available lessons (with transcriptions, not already in book)
            cur.execute(
                """SELECT d.id, d.title, d.source_id FROM document d
                   JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
                   ORDER BY d.title"""
            )
            existing_ids = {e["lesson_id"] for e in lessons}
            for row in cur.fetchall():
                if str(row["id"]) not in existing_ids:
                    sid = row["source_id"]
                    available_by_source.setdefault(sid, []).append(
                        {"id": str(row["id"]), "title": row["title"] or "Untitled"}
                    )

    # Build source filter options (only sources that have available lessons)
    source_options = '<option value="all">All sources</option>\n'
    for src in sources:
        sid = src["id"]
        if sid not in available_by_source:
            continue
        label = src["category_name"] or src["name"]
        count = len(available_by_source[sid])
        source_options += f'<option value="{sid}">{label} ({count})</option>\n'

    # Build lesson options with data-source attribute for filtering
    lesson_options = ""
    for src in sources:
        sid = src["id"]
        for item in available_by_source.get(sid, []):
            lesson_options += f'<option value="{item["id"]}" data-source="{sid}">{item["title"]}</option>\n'

    # Show current lessons with remove buttons
    current_items = ""
    for i, entry in enumerate(lessons):
        current_items += f"""
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
            <span style="font-size:0.85em;">{i+1}. {entry.get('title', 'Untitled')}
                <span style="color:#999;font-family:monospace;font-size:0.8em;">({entry['lesson_id'][:12]}...)</span>
            </span>
            <button class="remove-lesson-btn" data-lesson="{entry['lesson_id']}"
                style="background:none;border:1px solid #dc3545;color:#dc3545;border-radius:3px;padding:1px 6px;cursor:pointer;font-size:0.75em;">remove</button>
        </div>
        """

    return f"""
    <div style="margin-top: 16px; padding: 12px; background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 6px;">
        <div style="font-weight: bold; margin-bottom: 8px;">Manage Book Lessons</div>
        {current_items}
        <div style="display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap;">
            <select id="source-filter" style="padding: 4px 8px; font-size: 0.85em;">
                {source_options}
            </select>
            <select id="add-lesson-select" style="flex: 1; min-width: 200px; padding: 4px 8px; font-size: 0.85em;">
                <option value="">-- Select a lesson --</option>
                {lesson_options}
            </select>
            <button id="add-lesson-btn" style="background: #28a745; color: #fff; border: none; border-radius: 4px; padding: 4px 12px; cursor: pointer; font-size: 0.85em;">Add</button>
        </div>
    </div>
    """

