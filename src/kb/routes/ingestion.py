"""Ingestion dashboard routes: page, AJAX refresh, resubmit, retry-all-failed."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import web as _web

router = APIRouter()


@router.get("/ingestion", response_class=HTMLResponse)
async def ingestion_dashboard(request: Request):
    """Ingestion monitoring dashboard with resubmission support."""
    if _web.IS_LAMBDA and _web._auth_mod.get_access_tier(request) != "admin":
        return RedirectResponse("/")
    return _web._build_ingestion_page()


@router.get("/api/pipeline/ingestion")
async def pipeline_ingestion():
    """Return the ingestion tab content HTML for refresh."""
    if not _web._db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _web._build_ingestion_html()
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@router.post("/api/ingestion/resubmit/{document_id}")
async def resubmit_document(document_id: str):
    """Clear all pipeline state for a document so it gets reprocessed from scratch."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        cleared = _web._db.resubmit_document(document_id)
        _web._db.log_event("resubmit", document_id=document_id,
                       detail={"cleared": cleared})
        return JSONResponse({"status": "ok", "cleared": cleared})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/ingestion/retry-all-failed")
async def retry_all_failed():
    """Resubmit all documents in 'failed' pipeline stage."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        documents = _web._db.get_document_pipeline_view(limit=500)
        failed = [d for d in documents if d.get("pipeline_stage") == "failed"]
        results = []
        for doc in failed:
            doc_id = str(doc["id"])
            cleared = _web._db.resubmit_document(doc_id)
            results.append({"document_id": doc_id, "title": doc.get("title"), "cleared": cleared})
        if results:
            _web._db.log_event("retry_all_failed", detail={"count": len(results),
                          "documents": [r["title"] for r in results]})
        return JSONResponse({"status": "ok", "count": len(results), "results": results})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
