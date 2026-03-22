"""Book page (/book) — curated multi-lens lesson viewer."""

import re as _re
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse

from book.state import _db, get_available_lessons
from book.analysis import get_vl_booklet_intros
from book.booklet_data import VL_BOOKLET_INTROS


# --- Book (curated volume) ---
# Lessons are now loaded from the pipeline:book_lessons registry.
# BOOK_LESSONS is kept as a derived dict for backward compatibility with
# code that references it (e.g. booklet_config source node hashing).
def _load_book_lessons_compat() -> dict:
    """Load BOOK_LESSONS dict from the registry (backward-compatible format)."""
    try:
        from pipeline_graph import get_book_lessons
    except ImportError:
        return {}
    if not _db:
        return {}
    lessons = get_book_lessons(_db)
    if not lessons:
        return {}
    # Map every VL to the first lesson for backwards compat
    first = lessons[0]
    vl_ids = sorted(VL_BOOKLET_INTROS.keys())
    return {
        vl_id: {
            "lesson_id": first["lesson_id"],
            "title": first.get("title", "Untitled"),
        }
        for vl_id in vl_ids
    }


BOOK_LESSONS = _load_book_lessons_compat()


def _extract_booklet_sections(booklet_html: str) -> dict[str, str]:
    """Extract named sections from a filled booklet HTML.

    Returns dict with keys like 'person_inside', 'journey', etc.
    Values are inner HTML content (without the <section> wrapper).
    """
    import re
    body_match = re.search(r'<body[^>]*>(.*)</body>', booklet_html, re.DOTALL)
    if not body_match:
        return {}
    body = body_match.group(1)
    sections = re.findall(r'<section\b[^>]*class="([^"]*)"[^>]*>(.*?)</section>', body, re.DOTALL)
    result = {}
    for cls, content in sections:
        if "booklet-cover" in cls:
            continue
        if "booklet-journey" in cls:
            result["journey"] = content
        elif "booklet-concepts" in cls:
            result["concepts"] = content
        else:
            h2 = re.search(r'<h2>(.*?)</h2>', content)
            if h2:
                heading = h2.group(1)
                if "Question" in heading:
                    result["inquiry"] = content
                elif "Changed" in heading:
                    result["washing"] = content
                elif "person_inside" not in result:
                    # First unclassified section is person_inside
                    result["person_inside"] = content
    return result


async def book_page(request: Request):
    """Render the book: lessons analysed through all 5 VL lenses."""
    try:
        from pipeline_graph import get_book_lessons
    except ImportError:
        get_book_lessons = lambda db: []

    vl_ids = sorted(VL_BOOKLET_INTROS.keys())  # VL-1 .. VL-5

    # Load lesson registry and find which have completed booklets
    lessons = get_book_lessons(_db) if _db else []
    completed_lessons = []
    if _db:
        for entry in lessons:
            lid = entry["lesson_id"]
            # A lesson is "completed" if at least one VL booklet exists
            if any(_db.get_cache(f"pipeline:{lid}:booklet:{vl}") for vl in vl_ids):
                completed_lessons.append(entry)

    # Select lesson from query param, or default to first completed
    selected_lid = request.query_params.get("lesson")
    selected_lesson = None
    if selected_lid:
        selected_lesson = next((e for e in completed_lessons if e["lesson_id"] == selected_lid), None)
    if not selected_lesson and completed_lessons:
        selected_lesson = completed_lessons[0]
    first_lid = selected_lesson["lesson_id"] if selected_lesson else None

    # Load all 5 VL booklets from namespaced pipeline cache
    booklets: dict[str, str | None] = {}
    if _db and first_lid:
        cache_prefix = f"pipeline:{first_lid}"
        for vl_id in vl_ids:
            data = _db.get_cache(f"{cache_prefix}:booklet:{vl_id}")
            booklets[vl_id] = data.get("html") if data else None
    else:
        booklets = {vl_id: None for vl_id in vl_ids}

    # Read lesson title from pipeline analysis_a result (namespaced)
    default_tab = "all-voices"
    title = selected_lesson["title"] if selected_lesson else "Untitled Lesson"
    pass_a = None
    if _db and first_lid:
        pass_a = _db.get_cache(f"pipeline:{first_lid}:analysis_a")
        if pass_a and isinstance(pass_a, dict):
            title = pass_a.get("lesson_title", "") or title

    # --- Extract sections from each booklet ---
    all_sections: dict[str, dict[str, str]] = {}  # vl_id -> {section_name: html}
    for vl_id in vl_ids:
        html = booklets.get(vl_id)
        if html:
            all_sections[vl_id] = _extract_booklet_sections(html)

    # --- "All Voices" combined panel ---
    # Part 1: Person inside narratives, labeled by character name
    narrative_parts = []
    for vl_id in vl_ids:
        secs = all_sections.get(vl_id, {})
        person_html = secs.get("person_inside", "")
        if not person_html:
            continue
        info = VL_BOOKLET_INTROS.get(vl_id, {})
        name = info.get("name", vl_id)
        # Strip the h2 (character name) since we add our own label
        import re as _re
        person_html = _re.sub(r'<h2>.*?</h2>', '', person_html, count=1, flags=_re.DOTALL).strip()
        narrative_parts.append(
            f'<div class="voice-entry">'
            f'<h3>{name}</h3>'
            f'{person_html}'
            f'</div>'
        )

    # Part 2: Journey voice — each VL's full story extracted from the journey section.
    # Extract only the llm-filled content (the VL's pedagogical voice),
    # skipping step lists, questions, and blob/shadow content.
    journey_voice_parts = []
    for vl_id in vl_ids:
        secs = all_sections.get(vl_id, {})
        journey_html = secs.get("journey", "")
        if not journey_html:
            continue
        info = VL_BOOKLET_INTROS.get(vl_id, {})
        name = info.get("name", vl_id)

        # Extract part headings and their llm-filled voice content
        # Each journey-group has: h3, step-list, llm-filled-group(s), the-question
        # We want h3 + llm-filled only
        part_voices = []
        groups = _re.findall(
            r'<h3>(.*?)</h3>.*?(?=<h3|$)',
            journey_html, _re.DOTALL
        )
        group_contents = _re.split(r'<h3>.*?</h3>', journey_html)[1:]  # content after each h3

        part_headings = _re.findall(r'<h3>(.*?)</h3>', journey_html)
        for heading, content in zip(part_headings, group_contents):
            # Extract all llm-filled blocks (the VL voice)
            filled = _re.findall(
                r'<div class="llm-filled">(.*?)</div>',
                content, _re.DOTALL
            )
            if filled:
                part_voices.append(
                    f'<h4>{heading}</h4>'
                    + "\n".join(f.strip() for f in filled)
                )

        if part_voices:
            journey_voice_parts.append(
                f'<div class="voice-entry">'
                f'<h3>{name}</h3>'
                + "\n".join(part_voices)
                + '</div>'
            )

    # Assemble "All Voices" (narratives only) content
    if narrative_parts:
        all_voices_content = "\n".join(narrative_parts)
    else:
        all_voices_content = (
            '<p style="color:#999;font-style:italic;padding:40px 0;">'
            'No booklets generated yet. Run the pipeline first.</p>'
        )

    # Assemble "Inner Viewpoints" content (pedagogical gloss from journey)
    if journey_voice_parts:
        inner_viewpoints_content = "\n".join(journey_voice_parts)
    else:
        inner_viewpoints_content = (
            '<p style="color:#999;font-style:italic;padding:40px 0;">'
            'No journey data available yet. Run the pipeline first.</p>'
        )

    # --- "Journey Voices" panel ---
    # Extract character first-person voice text from the journey section.
    # Pattern: <p><strong>The Riser:</strong> <em>I feel the floor...</em></p>
    journey_character_parts = []
    for vl_id in vl_ids:
        secs = all_sections.get(vl_id, {})
        journey_html = secs.get("journey", "")
        if not journey_html:
            continue
        info = VL_BOOKLET_INTROS.get(vl_id, {})
        char_name = info.get("name", vl_id)

        # Find all instances of this character's voice in the journey
        # Pattern: <strong>The Riser:</strong> followed by <em>...</em>
        pattern = (
            r'<strong>\s*' + _re.escape(char_name) + r'\s*:\s*</strong>\s*'
            r'<em>(.*?)</em>'
        )
        voices = _re.findall(pattern, journey_html, _re.DOTALL)
        if voices:
            voice_paragraphs = [f"<p><em>{v.strip()}</em></p>" for v in voices]
            journey_character_parts.append(
                f'<div class="voice-entry">'
                f'<h3>{char_name}</h3>'
                + "\n".join(voice_paragraphs)
                + '</div>'
            )

    if journey_character_parts:
        journey_voices_content = "\n".join(journey_character_parts)
    else:
        journey_voices_content = (
            '<p style="color:#999;font-style:italic;padding:40px 0;">'
            'No character voice data available. Run the pipeline first.</p>'
        )

    # --- "The Blob" panel ---
    # Extract shadow_voice entries from analysis data (raw first-person Blob observations)
    blob_name = VL_BOOKLET_INTROS.get("VL-1", {}).get("habit_label_derived", "The Blob")
    blob_parts = []
    if _db and first_lid:
        # Try namespaced cache key first, fall back to legacy
        analysis_data = _db.get_cache(f"pipeline:{first_lid}:analysis_merge") or _db.get_cache("pipeline:analysis_merge") or _db.get_cache("pipeline:vl_synthesis")
        if analysis_data:
            for vl_id in vl_ids:
                vl = analysis_data.get("vl_analyses", {}).get(vl_id, {})
                result = vl.get("result", {})
                rows = result.get("analytical_table", {}).get("rows", [])
                shadow_voices = []
                for row in rows:
                    sv = row.get("shadow_voice", "") or row.get("striver_shadow", "")
                    if sv:
                        shadow_voices.append(f"<p>{sv}</p>")
                if shadow_voices:
                    info = VL_BOOKLET_INTROS.get(vl_id, {})
                    name = info.get("name", vl_id)
                    blob_parts.append(
                        f'<div class="voice-entry">'
                        f'<h3>Through the eyes of {name}</h3>'
                        + "\n".join(shadow_voices)
                        + '</div>'
                    )
    if blob_parts:
        blob_content = "\n".join(blob_parts)
    else:
        blob_content = (
            '<p style="color:#999;font-style:italic;padding:40px 0;">'
            'No shadow voice data available. Run the pipeline first.</p>'
        )

    # --- "Lesson Text" panel ---
    # Show the raw lesson transcription broken by its own section headings
    import re as _re
    lesson_text_content = ""
    lesson_id = first_lid or ""
    if _db and lesson_id:
        transcription = _db.get_document_transcription(lesson_id)
        if transcription:
            # Build source attribution link
            doc = _db.get_document(lesson_id)
            source_link = ""
            if doc:
                source_url = doc.get("source_url", "") or ""
                doc_title = doc.get("title", "") or title
                # Get source name
                source_name = ""
                if doc.get("source_id"):
                    with _db.get_cursor(commit=False) as cur:
                        cur.execute(
                            "SELECT category_name, name FROM document_source WHERE id = %s",
                            (doc["source_id"],),
                        )
                        row = cur.fetchone()
                        if row:
                            source_name = row["category_name"] or row["name"]
                if source_url:
                    source_link = (
                        f'<p class="lesson-source">'
                        f'<a href="{source_url}">Lesson &ldquo;{title}&rdquo; from {source_name}</a>'
                        f'</p>'
                    )

            # Split transcription by **Bold Headings** (markdown-style sections)
            sections = _re.split(r'\n\*\*([^*]+)\*\*\n', transcription)
            parts = []
            # sections[0] is text before first heading (preamble)
            if sections[0].strip():
                parts.append(f'<p>{sections[0].strip()}</p>')
            # Remaining pairs: heading, content
            for i in range(1, len(sections), 2):
                heading = sections[i].strip()
                content = sections[i + 1].strip() if i + 1 < len(sections) else ""
                # Convert paragraphs (double newlines)
                paragraphs = [f"<p>{p.strip()}</p>" for p in content.split("\n\n") if p.strip()]
                parts.append(
                    f'<div class="lesson-section">'
                    f'<h3>{heading}</h3>'
                    + "\n".join(paragraphs)
                    + '</div>'
                )

            lesson_text_content = source_link + "\n".join(parts)
    if not lesson_text_content:
        lesson_text_content = (
            '<p style="color:#999;font-style:italic;padding:40px 0;">'
            'Lesson text not available.</p>'
        )

    # Build tab buttons: Lesson Text, All Voices, The Blob, then VL-1..5
    tab_buttons = []
    tab_buttons.append(
        '<button onclick="showBookTab(\'lesson-text\')" id="book-tab-lesson-text" '
        'class="book-tab">Lesson Text</button>'
    )
    tab_buttons.append(
        '<button onclick="showBookTab(\'all-voices\')" id="book-tab-all-voices" '
        'class="book-tab">Narratives</button>'
    )
    tab_buttons.append(
        '<button onclick="showBookTab(\'journey-voices\')" id="book-tab-journey-voices" '
        'class="book-tab">Journey Voices</button>'
    )
    tab_buttons.append(
        '<button onclick="showBookTab(\'inner-viewpoints\')" id="book-tab-inner-viewpoints" '
        'class="book-tab">Inner Viewpoints</button>'
    )
    tab_buttons.append(
        f'<button onclick="showBookTab(\'blob\')" id="book-tab-blob" '
        f'class="book-tab">{blob_name}</button>'
    )
    for vl_id in vl_ids:
        info = VL_BOOKLET_INTROS.get(vl_id, {})
        label = f'{vl_id}: {info.get("name", "")}'
        tab_buttons.append(
            f'<button onclick="showBookTab(\'{vl_id}\')" id="book-tab-{vl_id}" '
            f'class="book-tab">{label}</button>'
        )

    # Build tab content panels
    tab_panels = []
    tab_panels.append(f'<div id="book-content-lesson-text" class="book-panel">{lesson_text_content}</div>')
    tab_panels.append(f'<div id="book-content-all-voices" class="book-panel">{all_voices_content}</div>')
    tab_panels.append(f'<div id="book-content-journey-voices" class="book-panel">{journey_voices_content}</div>')
    tab_panels.append(f'<div id="book-content-inner-viewpoints" class="book-panel">{inner_viewpoints_content}</div>')
    tab_panels.append(f'<div id="book-content-blob" class="book-panel">{blob_content}</div>')
    for vl_id in vl_ids:
        html = booklets.get(vl_id)
        if html:
            content = html
        else:
            content = (
                '<p style="color:#999;font-style:italic;padding:40px 0;">'
                'Booklet not yet generated for this lens. Run the pipeline to generate it.</p>'
            )
        tab_panels.append(
            f'<div id="book-content-{vl_id}" class="book-panel">{content}</div>'
        )

    # Build lesson selector (only if multiple completed lessons)
    lesson_selector = ""
    if len(completed_lessons) > 1:
        opts = []
        for entry in completed_lessons:
            lid = entry["lesson_id"]
            ltitle = entry.get("title", "Untitled")
            sel = " selected" if lid == first_lid else ""
            opts.append(f'<option value="{lid}"{sel}>{ltitle}</option>')
        lesson_selector = (
            '<select id="lesson-select" onchange="window.location=\'/?lesson=\'+this.value"'
            ' style="font-family:Inter,sans-serif;font-size:0.85em;padding:4px 8px;'
            'border:1px solid #c4bfb5;border-radius:4px;background:#fff;color:#555;">'
            + "".join(opts) + '</select>'
        )
    elif len(completed_lessons) == 1:
        lesson_selector = ""  # no selector needed for single lesson

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>{title} &mdash; Five-Lens Analysis</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400;1,600&family=Inter:wght@400;500;600&display=swap');
        body {{
            font-family: 'Lora', Georgia, serif;
            max-width: 680px;
            margin: 0 auto;
            padding: 40px 24px;
            line-height: 1.7;
            color: #2c2c2c;
            background: #faf9f7;
        }}
        h1 {{ font-size: 1.8em; margin-bottom: 8px; }}
        h2 {{ font-size: 1.3em; margin: 32px 0 12px; color: #4a6741; }}
        p {{ color: #555; margin-bottom: 16px; }}
        .back {{ font-size: 0.85em; color: #999; margin-bottom: 20px; display: block; }}
        .book-tabs {{
            display: flex;
            gap: 4px;
            margin-bottom: 0;
            flex-wrap: wrap;
        }}
        .book-tab {{
            padding: 10px 16px;
            border: 1px solid #d4cfc7;
            border-bottom: none;
            cursor: pointer;
            background: #eee8df;
            color: #666;
            border-radius: 6px 6px 0 0;
            font-family: 'Inter', sans-serif;
            font-size: 0.85em;
            font-weight: 500;
            transition: background 0.15s, color 0.15s;
        }}
        .book-tab:hover {{
            background: #e2dacf;
        }}
        .book-tab.active {{
            background: #fff;
            color: #4a6741;
            font-weight: 600;
            border-color: #c4bfb5;
        }}
        .book-panel {{
            display: none;
            border: 1px solid #c4bfb5;
            border-radius: 0 0 6px 6px;
            padding: 24px;
            background: #fff;
        }}
        .book-panel.active {{
            display: block;
        }}
        .voice-entry {{
            margin-bottom: 32px;
            padding-bottom: 24px;
            border-bottom: 1px solid #e8e2d9;
        }}
        .voice-entry:last-child {{
            border-bottom: none;
            margin-bottom: 0;
            padding-bottom: 0;
        }}
        .voice-entry h3 {{
            font-family: 'Inter', sans-serif;
            font-size: 1.05em;
            font-weight: 600;
            color: #4a6741;
            margin-bottom: 12px;
        }}
        .voice-entry h4 {{
            font-family: 'Inter', sans-serif;
            font-size: 0.9em;
            font-weight: 600;
            color: #4a6741;
            margin-bottom: 8px;
            margin-top: 0;
        }}
        .lesson-source {{
            margin-bottom: 24px;
        }}
        .lesson-source a {{
            color: #4a6741;
            text-decoration: none;
            font-style: italic;
        }}
        .lesson-source a:hover {{
            text-decoration: underline;
        }}
        .lesson-section {{
            margin-bottom: 28px;
        }}
        .lesson-section h3 {{
            font-family: 'Inter', sans-serif;
            font-size: 1.05em;
            font-weight: 600;
            color: #333;
            margin-bottom: 12px;
        }}
    </style>
</head>
<body>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <a href="/pipeline" class="back" style="margin:0;">Pipeline Dashboard &rarr;</a>
        {lesson_selector}
    </div>
    <h1>{title}</h1>
    <div class="book-tabs">
        {"".join(tab_buttons)}
    </div>
    {"".join(tab_panels)}
    <footer style="margin-top:48px;padding-top:12px;border-top:1px solid #e8e2d9;font-size:0.75em;color:#bbb;">
        Generated {datetime.now(timezone.utc).strftime("%b %d, %Y at %I:%M %p")} UTC
    </footer>
    <script>
    function showBookTab(vlId) {{
        document.querySelectorAll('.book-tab').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.book-panel').forEach(p => p.classList.remove('active'));
        document.getElementById('book-tab-' + vlId).classList.add('active');
        document.getElementById('book-content-' + vlId).classList.add('active');
    }}
    showBookTab('{default_tab}');
    </script>
</body>
</html>"""

