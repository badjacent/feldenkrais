"""Analysis dashboard routes: dashboard page, gate management, pipeline run, status, error clearing."""

import threading

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import web as _web

router = APIRouter()


@router.get("/analysis", response_class=HTMLResponse)
async def analysis_dashboard(request: Request):
    """Analysis pipeline dashboard with gate management."""
    if _web.IS_LAMBDA and _web._auth_mod.get_access_tier(request) != "admin":
        return RedirectResponse("/")
    group_id = request.query_params.get("group")
    group_id = int(group_id) if group_id else None
    return _web._build_analysis_page(group_id)


@router.get("/pipeline", response_class=HTMLResponse)
async def pipeline_dashboard(request: Request):
    """Pipeline dashboard -- book generation. 404s when the book pipeline is dormant."""
    if not _web._book_available:
        return HTMLResponse("Not Found", status_code=404)
    if _web.IS_LAMBDA and _web._auth_mod.get_access_tier(request) != "admin":
        return RedirectResponse("/")
    return _web.build_pipeline_html()


@router.get("/api/analysis/dashboard")
async def analysis_dashboard_data():
    """Return group-level analysis dashboard HTML for AJAX refresh."""
    if not _web._db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _web._build_analysis_html()
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@router.get("/api/analysis/group/{group_id}")
async def analysis_group_data(group_id: int):
    """Return document drill-down HTML for a group."""
    if not _web._db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _web._build_analysis_group_html(group_id)
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@router.get("/api/analysis/status")
async def analysis_status_data():
    """Return pipeline progress + errors HTML for AJAX refresh."""
    if not _web._db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _web._build_pipeline_status_html()
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@router.post("/api/analysis/gate")
async def update_analysis_gate(request: Request):
    """Toggle permitted flag for a (group, step) pair."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    body = await request.json()
    group_id = body.get("group_id")
    step_name = body.get("step_name")
    permitted = body.get("permitted")
    if not group_id or not step_name or permitted is None:
        return JSONResponse({"error": "Missing group_id, step_name, or permitted"}, status_code=400)
    try:
        _web._db.set_group_gate(int(group_id), step_name, permitted)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/analysis/groups")
async def list_analysis_groups():
    """List all lesson groups as JSON."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        groups = _web._db.get_lesson_groups()
        return JSONResponse([{
            "id": g["id"], "name": g["name"],
            "doc_count": g["doc_count"],
            "auto_source_id": g.get("auto_source_id"),
        } for g in groups])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/analysis/groups")
async def create_analysis_group(request: Request):
    """Create a custom lesson group."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    body = await request.json()
    name = body.get("name")
    document_ids = body.get("document_ids", [])
    if not name:
        return JSONResponse({"error": "Missing name"}, status_code=400)
    try:
        group_id = _web._db.create_lesson_group(name, body.get("description"))
        if document_ids:
            _web._db.add_group_members(group_id, document_ids)
        return JSONResponse({"status": "ok", "group_id": group_id})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/analysis/run")
async def run_analysis_pipeline(request: Request):
    """Toggle the pipeline on/off. Non-blocking -- runs in background thread."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)

    if _web._pipeline_is_running():
        # Stop -- set DB flag, worker will notice and exit
        _web._pipeline_set_running(False)
        return JSONResponse({"status": "stopping"})
    else:
        _web._ensure_worker_running()
        return JSONResponse({"status": "started"})


@router.get("/api/analysis/pipeline-status")
async def pipeline_status():
    """Return whether the pipeline is currently running."""
    return JSONResponse({"running": _web._pipeline_is_running()})


@router.post("/api/analysis/run-step")
async def run_single_step(request: Request):
    """Run a single analysis step for one document plus all downstream dependents.

    Invalidates the step and all downstream dependents first, then queues the
    lesson for priority processing by the pipeline worker. The worker will run
    the invalidated step, then pick up any downstream steps that become runnable.

    Only available when PIPELINE_LOCAL_CONTROLS is True.
    """
    if not _web.PIPELINE_LOCAL_CONTROLS:
        return JSONResponse({"error": "Step-level controls not available in this environment"}, status_code=403)
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)

    body = await request.json()
    document_id = body.get("document_id")
    step_name = body.get("step_name")
    if not document_id or not step_name:
        return JSONResponse({"error": "Missing document_id or step_name"}, status_code=400)

    # Validate step exists
    step = _web._db.get_analysis_step(step_name)
    if not step:
        return JSONResponse({"error": f"Unknown step: {step_name}"}, status_code=400)

    try:
        # Invalidate this step + all downstream dependents
        _web._db.invalidate_step(document_id, step_name)

        # Queue this lesson for priority processing. The worker will run the
        # invalidated step first, then any downstream steps as they become
        # runnable. If the worker is already processing bulk work, this lesson
        # jumps to the front without pre-empting in-flight steps.
        _web.queue_lesson(document_id)

        return JSONResponse({"status": "running", "document_id": document_id, "step_name": step_name})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/analysis/capabilities")
async def analysis_capabilities():
    """Return which pipeline controls are available in this environment."""
    return JSONResponse({
        "local_controls": _web.PIPELINE_LOCAL_CONTROLS,
    })


@router.post("/api/analysis/clear-error")
async def clear_analysis_error(request: Request):
    """Clear the error on a document analysis state entry so it can be retried."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    body = await request.json()
    document_id = body.get("document_id")
    step_name = body.get("step_name")
    if not document_id or not step_name:
        return JSONResponse({"error": "Missing document_id or step_name"}, status_code=400)
    try:
        _web._db.clear_step_error(document_id, step_name)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/analysis/clear-all-errors")
async def clear_all_analysis_errors(request: Request):
    """Clear all errors so failed steps become runnable again."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        count = _web._db.clear_all_errors()
        return JSONResponse({"status": "ok", "cleared": count})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
