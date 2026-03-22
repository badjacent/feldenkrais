"""Admin and pipeline API endpoints."""

import asyncio

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from book.state import _db, load_lesson, _pipeline_run_lock, _run_pipeline_for_lesson
from book.pipeline import _build_book_pipeline_section
from book.analysis import _run_analysis


# --- Admin endpoints ---

async def admin_precache(request: Request):
    """Pre-cache analysis for specific lessons (both single and split modes)."""
    body = await request.json()
    lessons = body.get("lessons", [])
    results = []
    for lesson in lessons:
        category = lesson.get("category", "")
        key = lesson.get("key", "")
        text = load_lesson(category, key).strip()
        if not text:
            results.append({"category": category, "key": key, "single": "skipped", "split": "skipped"})
            continue
        entry = {"category": category, "key": key}
        for mode, split in [("single", False), ("split", True)]:
            cache_suffix = ":split" if split else ""
            cache_key = get_cache_key(text + cache_suffix)
            if load_cached_analysis(cache_key):
                entry[mode] = "cached"
            else:
                try:
                    _run_analysis(text, split=split)
                    entry[mode] = "complete"
                except Exception as e:
                    entry[mode] = f"error: {e}"
        results.append(entry)
    return JSONResponse({"results": results})


async def pipeline_status():
    """Return the pipeline status section HTML for polling updates."""
    if not _db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _build_book_pipeline_section()
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


async def toggle_pipeline_node(node_name: str):
    """Toggle the enabled/disabled state of a pipeline node."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        from pipeline import PipelineStateStore
    except ImportError:
        return JSONResponse({"error": "Pipeline not available"}, status_code=503)
    store = PipelineStateStore(_db)
    store.initialize()
    current = store.is_enabled(node_name)
    store.set_enabled(node_name, not current)
    return JSONResponse({"node": node_name, "enabled": not current})


async def rebuild_pipeline_node(node_name: str):
    """Invalidate a pipeline node, marking it and its downstream as stale."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        from pipeline import PipelineStateStore
    except ImportError:
        return JSONResponse({"error": "Pipeline not available"}, status_code=503)

    store = PipelineStateStore(_db)
    store.initialize()
    store.invalidate(node_name)
    return JSONResponse({"status": "invalidated", "node": node_name})


async def run_pipeline_for_lesson(lesson_id: str):
    """Trigger an on-demand pipeline run for a single lesson."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    if _pipeline_run_lock.locked():
        return JSONResponse({"status": "already_running", "message": "A pipeline run is already in progress"})

    async def _run():
        async with _pipeline_run_lock:
            await asyncio.to_thread(_run_pipeline_for_lesson, lesson_id)

    asyncio.create_task(_run())
    return JSONResponse({"status": "started", "lesson_id": lesson_id})


async def add_book_lesson(request: Request):
    """Add a lesson to the book registry."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        from pipeline_graph import get_book_lessons, set_book_lessons
    except ImportError:
        return JSONResponse({"error": "Pipeline not available"}, status_code=503)

    body = await request.json()
    lesson_id = body.get("lesson_id")
    title = body.get("title", "Untitled")
    if not lesson_id:
        return JSONResponse({"error": "lesson_id required"}, status_code=400)

    lessons = get_book_lessons(_db)
    # Don't add duplicates
    if any(e["lesson_id"] == lesson_id for e in lessons):
        return JSONResponse({"status": "already_exists", "lessons": lessons})

    lessons.append({"lesson_id": lesson_id, "title": title})
    set_book_lessons(_db, lessons)
    return JSONResponse({"status": "added", "lessons": lessons})


async def remove_book_lesson(request: Request):
    """Remove a lesson from the book registry."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        from pipeline_graph import get_book_lessons, set_book_lessons
    except ImportError:
        return JSONResponse({"error": "Pipeline not available"}, status_code=503)

    body = await request.json()
    lesson_id = body.get("lesson_id")
    if not lesson_id:
        return JSONResponse({"error": "lesson_id required"}, status_code=400)

    lessons = get_book_lessons(_db)
    lessons = [e for e in lessons if e["lesson_id"] != lesson_id]
    set_book_lessons(_db, lessons)
    return JSONResponse({"status": "removed", "lessons": lessons})


async def list_book_lessons():
    """List all lessons in the book registry."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        from pipeline_graph import get_book_lessons
    except ImportError:
        return JSONResponse({"error": "Pipeline not available"}, status_code=503)

    lessons = get_book_lessons(_db)
    return JSONResponse({"lessons": lessons})



