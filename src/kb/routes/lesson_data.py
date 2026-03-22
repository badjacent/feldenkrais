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


@router.get("/api/lesson/{document_id}/combined")
async def api_lesson_combined(document_id: str):
    """Return combined somatic + language analysis in the structured JSON format
    expected by the Step by Step and Transcript tabs."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)

    # Load cached results — try pipeline key first, then legacy
    pass_a = None
    for suffix in [":decompose_v3", ":pass_a", ":pass_a_v1"]:
        pa_key = _web.get_cache_key(text + suffix)
        pass_a = _web.load_cached_analysis(pa_key)
        if pass_a:
            break
    if not pass_a:
        return JSONResponse({"error": "Decomposition not yet available. Run the analysis pipeline first."}, status_code=404)

    som_key = _web.get_cache_key(text + ":somatic_v2")
    lang_key = _web.get_cache_key(text + ":language_v1")
    somatic_result = _web.load_cached_analysis(som_key) or {}
    lang_result = _web.load_cached_analysis(lang_key) or {}

    # Align with whisper segments if available
    pass_0 = pass_a.get("pass_0", {})
    whisper_segments = _web._db.get_document_whisper_segments(document_id)
    if whisper_segments and hasattr(_web, "align_pass0_with_whisper"):
        _web.align_pass0_with_whisper(pass_0, whisper_segments)

    groups = pass_a.get("groups", [])
    pa_steps = pass_a.get("steps", [])
    seg_lookup = {s["id"]: s for s in pass_0.get("segments", [])}

    somatic_enriched = somatic_result.get("enriched_steps", [])
    lang_enriched = lang_result.get("enriched_steps", [])
    somatic_lookup = {e["step_number"]: e for e in somatic_enriched}
    lang_lookup = {e["step_number"]: e for e in lang_enriched}

    steps_json = []
    for pa_step in pa_steps:
        sn = pa_step["step_number"]
        seg_ids = pa_step.get("segments", [])
        segments_data = []
        time_starts, time_ends = [], []
        for sid in seg_ids:
            seg = seg_lookup.get(sid, {})
            seg_entry = {"id": sid, "text": seg.get("text", "")}
            if seg.get("time_start") is not None:
                seg_entry["time_start"] = seg["time_start"]
                seg_entry["time_end"] = seg.get("time_end", seg["time_start"])
                time_starts.append(seg_entry["time_start"])
                time_ends.append(seg_entry["time_end"])
            segments_data.append(seg_entry)

        step_obj = {
            "step_number": sn,
            "group_number": pa_step.get("group_number"),
            "instruction": pa_step.get("instruction", ""),
            "tags": pa_step.get("tags", []),
            "starting_position": pa_step.get("starting_position", ""),
            "time_start": min(time_starts) if time_starts else None,
            "time_end": max(time_ends) if time_ends else None,
            "segments": segments_data,
        }

        som = somatic_lookup.get(sn)
        if som:
            step_obj["somatic"] = {
                k: som.get(k, "")
                for k in ["clarified", "intent", "differentiation", "activation",
                          "inhibition", "transmission_route", "ground_push",
                          "mechanical_directive", "attentional_cue"]
            }
            step_obj["relevance"] = som.get("relevance", True)
        else:
            step_obj["relevance"] = True

        lang = lang_lookup.get(sn)
        lang_dict = {}
        if lang:
            lang_dict = {
                k: lang.get(k, "")
                for k in ["explanatory_language", "attention_cues", "effort_language",
                          "image_language", "distinctive_phrasing"]
            }
        if som and som.get("sensory_inquiry"):
            lang_dict["sensory_inquiry"] = som["sensory_inquiry"]
        if lang_dict:
            step_obj["language"] = lang_dict
        steps_json.append(step_obj)

    return JSONResponse({
        "groups": [
            {"group_number": g.get("group_number"), "label": g.get("label", ""),
             "description": g.get("description", ""), "starting_position": g.get("starting_position", ""),
             "steps": g.get("steps", [])}
            for g in groups
        ],
        "steps": steps_json,
        "lang_summary": lang_result.get("summary", {}),
        "somatic_summary": somatic_result.get("summary", {}),
        "cached_somatic": True,
        "cached_lang": True,
    })


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

    pass_a = None
    for suffix in [":decompose_v3", ":pass_a", ":pass_a_v1"]:
        pa_key = _web.get_cache_key(text + suffix)
        pass_a = _web.load_cached_analysis(pa_key)
        if pass_a:
            break
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
        {"name": "Pass A (Decompose)", "suffix": ":decompose_v3"},
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
    """Return lesson metadata including text, audio availability, source info."""
    if not _web._db:
        return JSONResponse({"error": "no database"}, status_code=503)
    doc = _web._db.get_document(document_id)
    if not doc:
        return JSONResponse({"error": "not found"}, status_code=404)
    segments = _web._db.get_document_whisper_segments(document_id)
    text = _web._db.get_document_transcription(document_id)
    # Get source name
    source = _web._db.get_source(doc.get("source_id")) if doc.get("source_id") else None
    source_name = (source.get("category_name") or source.get("name", "")) if source else ""
    return JSONResponse({
        "title": doc.get("title", ""),
        "source_url": doc.get("source_url"),
        "source_name": source_name,
        "has_audio": bool(doc.get("s3_key")),
        "has_whisper_segments": bool(segments),
        "segment_count": len(segments) if segments else 0,
        "text": text or "",
    })


@router.get("/api/audio-url/{document_id}")
async def api_audio_url(document_id: str):
    """Return a presigned S3 URL for the lesson's audio file."""
    if not _web._db or not _web.KB_BUCKET:
        return JSONResponse({"error": "Database or S3 not configured"}, status_code=500)
    doc = _web._db.get_document(document_id)
    if not doc or not doc.get("s3_key"):
        return JSONResponse({"error": "Document or audio not found"}, status_code=404)
    from storage import Storage
    storage = Storage(_web.KB_BUCKET)
    url = storage.generate_presigned_url(doc["s3_key"], expiry_seconds=7200)
    return JSONResponse({"audio_url": url})


@router.get("/api/whisper-segments/{document_id}")
async def api_whisper_segments(document_id: str):
    """Return raw whisper segments for pre-analysis segment sync."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    segments = _web._db.get_document_whisper_segments(document_id)
    if segments is None:
        return JSONResponse({"error": "No whisper segments found"}, status_code=404)
    return JSONResponse({"segments": segments})


@router.get("/api/whisper-words/{document_id}")
async def api_whisper_words(document_id: str):
    """Return word-level timestamps for karaoke-style text highlighting."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    words = _web._db.get_document_whisper_words(document_id)
    if words is None:
        return JSONResponse({"words": None})
    return JSONResponse({"words": words})
