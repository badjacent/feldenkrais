"""Analysis dashboard routes: dashboard page, profile switches, gates, status, error clearing.

The analysis pipeline is visible only on local deployments (this laptop and the
Mac mini). Under Lambda every route here answers 404, so the production site
neither shows nor exposes it.

Analysis itself runs in the dedicated worker (``pipeline.analysis run``); these
routes only change what the worker may claim.
"""

import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import web as _web


def _local_deployment_only():
    """Hide the pipeline from the production site."""
    if not _web.PIPELINE_LOCAL_CONTROLS:
        raise HTTPException(status_code=404)


router = APIRouter(dependencies=[Depends(_local_deployment_only)])


def _profile_key(value: str | None) -> str:
    """Resolve a requested profile key (canonical when omitted); raises ValueError if unknown."""
    return _web._db.resolve_analysis_profile(value)["key"]


@router.get("/analysis", response_class=HTMLResponse)
async def analysis_dashboard(request: Request):
    """Analysis pipeline dashboard: profile switches and gate management."""
    group_id = request.query_params.get("group")
    group_id = int(group_id) if group_id else None
    try:
        profile_key = _profile_key(request.query_params.get("profile")) if _web._db else None
    except ValueError as e:
        return HTMLResponse(f"<p>{e}</p>", status_code=400)
    return _web._build_analysis_page(group_id, profile_key)


@router.get("/pipeline", response_class=HTMLResponse)
async def pipeline_dashboard(request: Request):
    """Pipeline dashboard -- book generation. 404s when the book pipeline is dormant."""
    if not _web._book_available:
        return HTMLResponse("Not Found", status_code=404)
    return _web.build_pipeline_html()


@router.get("/api/analysis/dashboard")
async def analysis_dashboard_data(profile: str | None = None):
    """Return group-level analysis dashboard HTML for AJAX refresh."""
    if not _web._db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _web._build_analysis_html(_profile_key(profile))
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@router.get("/api/analysis/group/{group_id}")
async def analysis_group_data(group_id: int, profile: str | None = None):
    """Return document drill-down HTML for a group under one profile."""
    if not _web._db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _web._build_analysis_group_html(group_id, _profile_key(profile))
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@router.get("/api/analysis/status")
async def analysis_status_data(profile: str | None = None):
    """Return pipeline progress + errors HTML for AJAX refresh."""
    if not _web._db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _web._build_pipeline_status_html(_profile_key(profile))
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@router.post("/api/analysis/profile")
async def update_analysis_profile(request: Request):
    """Turn a profile's global switch on or off.

    Switching off stops the worker claiming new steps for that profile; steps
    already running finish.
    """
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    body = await request.json()
    key = body.get("key")
    enabled = body.get("enabled")
    if not key or not isinstance(enabled, bool):
        return JSONResponse({"error": "Missing key or enabled"}, status_code=400)
    if not _web._db.set_profile_enabled(key, enabled):
        return JSONResponse({"error": f"Unknown analysis profile: {key}"}, status_code=404)
    return JSONResponse({"status": "ok", "key": key, "enabled": enabled})


@router.post("/api/analysis/gate")
async def update_analysis_gate(request: Request):
    """Toggle the permitted flag for a (group, profile, step) triple."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    body = await request.json()
    group_id = body.get("group_id")
    step_name = body.get("step_name")
    permitted = body.get("permitted")
    if not group_id or not step_name or permitted is None:
        return JSONResponse({"error": "Missing group_id, step_name, or permitted"}, status_code=400)
    try:
        profile_key = _profile_key(body.get("profile_key"))
        _web._db.set_group_gate(int(group_id), step_name, permitted, profile_key=profile_key)
        return JSONResponse({"status": "ok"})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
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


@router.post("/api/analysis/run-step")
async def run_single_step(request: Request):
    """Re-run one step for one document under one profile, plus its dependents.

    Invalidates the step and everything downstream of it for that profile. The
    dedicated worker picks the work up on its next pass, provided a gate
    permits it and the profile is switched on.
    """
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)

    body = await request.json()
    document_id = body.get("document_id")
    step_name = body.get("step_name")
    if not document_id or not step_name:
        return JSONResponse({"error": "Missing document_id or step_name"}, status_code=400)

    step = _web._db.get_analysis_step(step_name)
    if not step:
        return JSONResponse({"error": f"Unknown step: {step_name}"}, status_code=400)

    try:
        profile_key = _profile_key(body.get("profile_key"))
        _web._db.invalidate_step(document_id, step_name, profile_key=profile_key)
        return JSONResponse({"status": "queued", "document_id": document_id,
                             "step_name": step_name, "profile_key": profile_key})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/analysis/capabilities")
async def analysis_capabilities():
    """Return which pipeline controls are available in this environment."""
    return JSONResponse({
        "local_controls": _web.PIPELINE_LOCAL_CONTROLS,
    })


@router.post("/api/analysis/publish-atlas")
async def publish_atlas_projection(request: Request):
    """Manually trigger the Atlas projection publish (same fail-closed path
    as the automatic post-sweep hook). Runs in a background thread."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)

    def _run():
        try:
            from atlas_projection import publish
            publish(_web._db)
        except Exception as e:
            print(f"[atlas-projection] manual publish failed: {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse({"status": "started",
                         "note": "result appears in the Atlas projection panel"})


@router.post("/api/analysis/clear-error")
async def clear_analysis_error(request: Request):
    """Clear the error on one profile's state entry so it can be retried."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    body = await request.json()
    document_id = body.get("document_id")
    step_name = body.get("step_name")
    if not document_id or not step_name:
        return JSONResponse({"error": "Missing document_id or step_name"}, status_code=400)
    try:
        profile_key = _profile_key(body.get("profile_key"))
        _web._db.clear_step_error(document_id, step_name, profile_key=profile_key)
        return JSONResponse({"status": "ok"})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/analysis/clear-all-errors")
async def clear_all_analysis_errors(request: Request):
    """Clear errors so failed steps become runnable again, for one profile or all."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    body = await request.json() if await request.body() else {}
    try:
        count = _web._db.clear_all_errors(body.get("profile_key"))
        return JSONResponse({"status": "ok", "cleared": count})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
