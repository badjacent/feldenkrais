"""Booklet routes: /booklet page and picker."""

import re

from fastapi.responses import HTMLResponse, RedirectResponse

from llm_config import DEFAULT_LLM, get_llm
from cache_helpers import get_cache_key, load_cached_analysis

from book.state import _db, get_available_lessons, load_lesson, load_lesson_metadata
from book.analysis import get_vl_booklet_intros
from book.booklet_render import build_booklet_html


async def booklet_page(category: str = "", lesson: str = "", fill: str = "", framework: str = ""):
    """Render a lesson booklet from cached analysis."""
    if not category or not lesson:
        return RedirectResponse(url="/", status_code=303)

    # Check if a filled booklet exists in the pipeline cache for this lesson
    # Check if this lesson has a filled booklet in the namespaced pipeline cache
    if _db and framework:
        filled_data = _db.get_cache(f"pipeline:{lesson}:booklet:{framework}")
        if filled_data and filled_data.get("html"):
            return filled_data["html"]

    text = load_lesson(category, lesson).strip()
    if not text:
        return HTMLResponse("<h1>Lesson not found</h1>", status_code=404)

    cache_key = get_cache_key(text)
    cached = load_cached_analysis(cache_key)
    if not cached:
        return HTMLResponse(
            "<h1>No analysis available</h1><p>This lesson hasn't been analyzed yet. "
            '<a href="/analyzer">Go to the analyzer</a> and run it first.</p>',
            status_code=404,
        )

    # Get display name for the lesson
    categories, display_names = get_available_lessons()
    lesson_title = lesson
    cat_lessons = categories.get(category, [])
    for key, display in cat_lessons:
        if key == lesson:
            lesson_title = display
            break

    # Get lesson metadata (title, source_url) from DB
    metadata = load_lesson_metadata(category, lesson)
    source_url = metadata.get("source_url", "")
    original_title = metadata.get("title", "")

    html = build_booklet_html(
        cached,
        lesson_title,
        framework=framework,
        source_url=source_url,
        original_title=original_title,
    )

    # Optionally fill query blocks via QueryAgent
    if fill.lower() in ("true", "1", "yes"):
        import asyncio
        from query_agent import QueryAgent
        agent = QueryAgent(
            llm=get_llm(DEFAULT_LLM),
            max_review_cycles=0,  # skip review for web; use CLI for iteration
        )
        html = await asyncio.to_thread(agent.fill, html)
    else:
        # Replace unfilled query blocks with a placeholder label
        import re
        html = re.sub(
            r'<div class="llm-query"[^>]*>.*?</div>',
            '<div class="llm-query-unfilled">Block unfilled</div>',
            html,
            flags=re.DOTALL,
        )

    return html


def _booklet_picker_html() -> str:
    """Lesson picker for the booklet page."""
    categories, display_names = get_available_lessons()

    # Two queries total instead of N per lesson:
    # 1. All cache keys (just key strings, no data)
    # 2. All document transcription hashes (doc_id -> cache_key)
    cached_keys = _db.get_cache_keys() if _db else set()
    doc_hashes = _db.get_all_transcription_hashes() if _db else {}

    options = []
    for cat_key, lessons in categories.items():
        cat_display = display_names.get(cat_key, cat_key)
        for lesson_key, lesson_display in lessons:
            cache_key = doc_hashes.get(lesson_key)
            if cache_key and cache_key in cached_keys:
                options.append(
                    f'<li><a href="/booklet?category={cat_key}&lesson={lesson_key}">'
                    f'{lesson_display}</a> <span style="color: #999;">({cat_display})</span></li>'
                )

    if not options:
        lesson_list = "<p>No analyzed lessons available. <a href='/analyzer'>Run an analysis first.</a></p>"
    else:
        lesson_list = "<ul>" + "\n".join(options) + "</ul>"

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Lesson Booklets</title>
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
        p {{ color: #555; margin-bottom: 16px; }}
        ul {{ list-style: none; padding: 0; }}
        li {{
            padding: 12px 16px;
            border-bottom: 1px solid #e8e2d9;
        }}
        li:first-child {{ border-top: 1px solid #e8e2d9; }}
        a {{
            color: #4a6741;
            text-decoration: none;
            font-weight: 600;
        }}
        a:hover {{ text-decoration: underline; }}
        .back {{ font-size: 0.85em; color: #999; margin-bottom: 20px; display: block; }}
    </style>
</head>
<body>
    <a href="/analyzer" class="back">&larr; Back to Analyzer</a>
    <h1>Lesson Companion Booklets</h1>
    <p>Select a lesson to view its companion booklet.</p>
    {lesson_list}
</body>
</html>"""

