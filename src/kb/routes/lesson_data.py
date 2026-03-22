"""Lesson data API routes: somatic, language, classification, pipeline status, raw I/O."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import web as _web

router = APIRouter()


def _get_lesson_text(document_id: str) -> str | None:
    """Get lesson transcript text for a document, used to compute cache keys."""
    if not _web._db:
        return None
    text = _web._db.get_document_transcription(document_id)
    return text.strip() if text else None


@router.get("/api/lesson/{document_id}/somatic")
async def api_lesson_somatic(document_id: str):
    """Return cached somatic (Pass 0b) analysis for a lesson."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)

    cache_key = _web.get_cache_key(text + ":somatic_v2")
    cached = _web.load_cached_analysis(cache_key)
    if not cached:
        return JSONResponse({"error": "Somatic analysis not yet available. Run the analysis pipeline first."}, status_code=404)

    # Also load pass_a for step context
    pa_key = _web.get_cache_key(text + ":pass_a")
    pass_a = _web.load_cached_analysis(pa_key)

    return JSONResponse({
        "enriched_steps": cached.get("enriched_steps", []),
        "summary": cached.get("summary", {}),
        "pass_a": pass_a or {},
    })


@router.get("/api/lesson/{document_id}/language")
async def api_lesson_language(document_id: str):
    """Return cached language (Pass 0b) analysis for a lesson."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)

    cache_key = _web.get_cache_key(text + ":language_v1")
    cached = _web.load_cached_analysis(cache_key)
    if not cached:
        return JSONResponse({"error": "Language analysis not yet available. Run the analysis pipeline first."}, status_code=404)

    # Also load pass_a for step context
    pa_key = _web.get_cache_key(text + ":pass_a")
    pass_a = _web.load_cached_analysis(pa_key)

    return JSONResponse({
        "enriched_steps": cached.get("enriched_steps", []),
        "summary": cached.get("summary", {}),
        "pass_a": pass_a or {},
    })


@router.get("/api/lesson/{document_id}/condensed")
async def api_lesson_condensed(document_id: str):
    """Return cached condensed analysis for a lesson."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)

    cache_key = _web.get_cache_key(text + ":condense_v4")
    cached = _web.load_cached_analysis(cache_key)
    if not cached:
        return JSONResponse({"error": "Condensed analysis not yet available. Run the analysis pipeline first."}, status_code=404)

    return JSONResponse({
        "segments": cached.get("segments", []),
        "cached": True,
    })


@router.get("/api/lesson/{document_id}/classification")
async def api_lesson_classification(document_id: str):
    """Return cached Pass A (decomposition/classification) analysis for a lesson."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)

    pa_key = _web.get_cache_key(text + ":pass_a")
    pass_a = _web.load_cached_analysis(pa_key)
    if not pass_a:
        return JSONResponse({"error": "Classification analysis not yet available. Run the analysis pipeline first."}, status_code=404)

    return JSONResponse(pass_a)


@router.get("/api/lesson/{document_id}/pipeline-status")
async def api_lesson_pipeline_status(document_id: str):
    """Return pipeline analysis state and document metadata for a lesson."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)

    # Get document metadata
    doc = _web._db.get_document(document_id)
    if not doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)

    # Get all analysis step definitions
    steps = _web._db.get_analysis_steps()

    # Get per-step state for this document
    states = []
    for step in steps:
        state = _web._db.get_document_state(document_id, step["name"])
        states.append({
            "step_name": step["name"],
            "current_version": step.get("current_version"),
            "depends_on": step.get("depends_on") or [],
            "completed_version": state.get("completed_version") if state else None,
            "completed_at": state["completed_at"].isoformat() if state and state.get("completed_at") else None,
            "stale": state.get("stale", False) if state else False,
            "error": state.get("error") if state else None,
            "updated_at": state["updated_at"].isoformat() if state and state.get("updated_at") else None,
        })

    # Document metadata
    doc_meta = {
        "document_id": str(doc["id"]),
        "title": doc.get("title", ""),
        "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
        "source_id": doc.get("source_id"),
        "type_id": doc.get("type_id"),
        "s3_key": doc.get("s3_key"),
        "status": doc.get("status"),
    }

    return JSONResponse({"document": doc_meta, "steps": states})


@router.get("/api/lesson/{document_id}/raw-io")
async def api_lesson_raw_io(document_id: str):
    """Return cached debug/raw I/O logs for analysis steps if available."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)

    # Check known cache suffixes for raw I/O data
    io_steps = [
        {"name": "Pass A (Decompose)", "suffix": ":pass_a"},
        {"name": "Somatic", "suffix": ":somatic_v2"},
        {"name": "Language", "suffix": ":language_v1"},
        {"name": "Condensed", "suffix": ":condense_v4"},
        {"name": "Concepts", "suffix": ":concept_tags_v2"},
        {"name": "Spatial", "suffix": ":spatial_v2"},
    ]

    results = []
    for step in io_steps:
        cache_key = _web.get_cache_key(text + step["suffix"])
        cached = _web.load_cached_analysis(cache_key)
        entry = {
            "name": step["name"],
            "cache_key": cache_key,
            "has_data": cached is not None,
        }
        if cached:
            # Show a preview of the raw data (truncate large values)
            raw_json = json.dumps(cached, indent=2, default=str)
            entry["raw_json"] = raw_json[:50000] if len(raw_json) > 50000 else raw_json
            entry["raw_json_length"] = len(raw_json)
            entry["truncated"] = len(raw_json) > 50000
            # Extract top-level keys for summary
            entry["top_level_keys"] = list(cached.keys()) if isinstance(cached, dict) else []
        results.append(entry)

    return JSONResponse({"steps": results})


@router.get("/api/lesson-info/{document_id}")
async def lesson_info(document_id: str):
    """Return basic lesson metadata (title, source, whisper segments availability)."""
    if not _web._db:
        return JSONResponse({"error": "no database"}, status_code=503)
    doc = _web._db.get_document(document_id)
    if not doc:
        return JSONResponse({"error": "not found"}, status_code=404)
    segments = _web._db.get_document_whisper_segments(document_id)
    return JSONResponse({
        "title": doc.get("title", ""),
        "source_url": doc.get("source_url"),
        "has_whisper_segments": bool(segments),
        "segment_count": len(segments) if segments else 0,
    })
