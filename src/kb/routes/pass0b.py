"""Pass0b routes: somatic, language, combined, condensed, player pages and API endpoints."""

import asyncio
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

import web as _web

router = APIRouter()


# --- Pass 0b pages ---

@router.get("/pass0b", response_class=HTMLResponse)
async def pass0b_page(request: Request):
    """Pass 0b experiment page."""
    _web._set_source_access(request)
    return _web.build_pass0b_html(search_mode=_web._is_search_site(request), get_lessons_fn=_web.get_available_lessons)


@router.get("/pass0b-player", response_class=HTMLResponse)
async def pass0b_player_page(request: Request):
    """Pass 0b audio player page."""
    _web._set_source_access(request)
    return _web.build_pass0b_player_html(search_mode=_web._is_search_site(request), get_lessons_fn=_web.get_available_lessons)


@router.get("/pass0b-lang", response_class=HTMLResponse)
async def pass0b_lang_page(request: Request):
    """Pass 0b-lang language analysis page."""
    _web._set_source_access(request)
    return _web.build_pass0b_lang_html(search_mode=_web._is_search_site(request), get_lessons_fn=_web.get_available_lessons)


# --- Pass 0b API endpoints ---

@router.post("/api/pass0b/clear-cache")
async def api_pass0b_clear_cache():
    """Clear Pass A + Pass 0b cache entries."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)

    rows = _web._db.list_cache()
    keys = []
    for r in rows:
        try:
            data = _web._db.get_cache(r["key"])
        except Exception:
            continue
        if not data:
            continue
        if "enriched_segments" in data or "enriched_steps" in data:
            keys.append(r["key"])
        elif "steps" in data and "classifications" in data:
            keys.append(r["key"])
    if keys:
        with _web._db.get_cursor() as cur:
            for k in keys:
                cur.execute("DELETE FROM cache WHERE key = %s", (k,))
    return JSONResponse({"deleted": len(keys)})


@router.post("/api/pass0b")
async def api_pass0b(
    lesson_text: str = Form(""),
    selected_category: str = Form(""),
    selected_lesson: str = Form(""),
    provider: str = Form(""),
):
    """Run Pass A + Pass 0b and return results."""
    text = lesson_text.strip()
    if not text:
        return JSONResponse({"error": "Please enter or select lesson text."}, status_code=400)

    prov = provider if provider else None
    debug_log = []

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_web.run_pass_0b, text, prov, debug_log),
            timeout=_web.ANALYSIS_TIMEOUT,
        )
        whisper_segments = _web.load_whisper_segments(selected_category, selected_lesson)
        if whisper_segments:
            _web.align_pass0_with_whisper(result.get("pass_a", {}).get("pass_0", {}), whisper_segments)
        result_html = _web.render_pass0b_result(result)
        debug_html = _web.render_debug_log(debug_log) if debug_log else ""
        pass_a = result.get("pass_a", {})
        usage = result.get("usage", {})
        return JSONResponse({
            "status": "complete",
            "result_html": result_html,
            "debug_html": debug_html,
            "elapsed": result.get("elapsed"),
            "cached": result.get("cached", False),
            "step_count": len(pass_a.get("steps", [])),
            "group_count": len(pass_a.get("groups", [])),
            "enriched_count": len(result.get("enriched_steps", [])),
            "tokens_in": usage.get("tokens_in"),
            "tokens_out": usage.get("tokens_out"),
        })
    except asyncio.TimeoutError:
        return JSONResponse({"error": "Analysis timed out."}, status_code=504)
    except Exception as e:
        debug_html = _web.render_debug_log(debug_log) if debug_log else ""
        return JSONResponse({
            "error": str(e),
            "debug_html": debug_html,
        }, status_code=500)


@router.post("/api/pass0b-combined")
async def api_pass0b_combined(
    lesson_text: str = Form(""),
    selected_category: str = Form(""),
    selected_lesson: str = Form(""),
    provider: str = Form(""),
):
    """Run Pass A + both Pass 0b (somatic + language) in parallel, return merged results."""
    text = lesson_text.strip()
    if not text:
        return JSONResponse({"error": "Please enter or select lesson text."}, status_code=400)

    prov = provider if provider else None
    debug_log_somatic = []
    debug_log_lang = []

    try:
        somatic_result, lang_result = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(_web.run_pass_0b, text, prov, debug_log_somatic),
                asyncio.to_thread(_web.run_pass_0b_lang, text, prov, debug_log_lang),
            ),
            timeout=_web.ANALYSIS_TIMEOUT,
        )
        whisper_segments = _web.load_whisper_segments(selected_category, selected_lesson)
        if whisper_segments:
            _web.align_pass0_with_whisper(somatic_result.get("pass_a", {}).get("pass_0", {}), whisper_segments)
            _web.align_pass0_with_whisper(lang_result.get("pass_a", {}).get("pass_0", {}), whisper_segments)

        result_html = _web.render_pass0b_combined_result(somatic_result, lang_result)
        debug_html = _web.render_debug_log(debug_log_somatic + debug_log_lang)

        pass_a = somatic_result.get("pass_a", {})
        som_usage = somatic_result.get("usage", {})
        lang_usage = lang_result.get("usage", {})
        tokens_in = None
        tokens_out = None
        if som_usage.get("tokens_in") is not None and lang_usage.get("tokens_in") is not None:
            tokens_in = som_usage["tokens_in"] + lang_usage["tokens_in"]
            tokens_out = (som_usage.get("tokens_out") or 0) + (lang_usage.get("tokens_out") or 0)

        return JSONResponse({
            "status": "complete",
            "result_html": result_html,
            "debug_html": debug_html,
            "elapsed_somatic": somatic_result.get("elapsed"),
            "elapsed_lang": lang_result.get("elapsed"),
            "cached_somatic": somatic_result.get("cached", False),
            "cached_lang": lang_result.get("cached", False),
            "step_count": len(pass_a.get("steps", [])),
            "group_count": len(pass_a.get("groups", [])),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        })
    except asyncio.TimeoutError:
        return JSONResponse({"error": "Analysis timed out."}, status_code=504)
    except Exception as e:
        debug_html = _web.render_debug_log(debug_log_somatic + debug_log_lang)
        return JSONResponse({
            "error": str(e),
            "debug_html": debug_html,
        }, status_code=500)


@router.post("/api/pass0b-word-lang-json")
async def api_pass0b_word_lang_json(
    lesson_text: str = Form(""),
    selected_category: str = Form(""),
    selected_lesson: str = Form(""),
    provider: str = Form(""),
):
    """Run word-level language annotation and return phrase annotations per step."""
    text = lesson_text.strip()
    if not text:
        return JSONResponse({"error": "Please enter or select lesson text."}, status_code=400)

    prov = provider if provider else None

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_web.run_pass_0b_word_lang, text, prov),
            timeout=_web.ANALYSIS_TIMEOUT,
        )
        return JSONResponse({
            "word_annotations": result.get("word_annotations", []),
            "elapsed": result.get("elapsed"),
            "cached": result.get("cached", False),
            "truncation_warning": result.get("truncation_warning"),
        })
    except asyncio.TimeoutError:
        return JSONResponse({"error": "Analysis timed out."}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# --- Pass 0b Player API endpoints ---

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


@router.get("/api/lesson-info/{document_id}")
async def api_lesson_info(document_id: str):
    """Return lesson info for the detail page."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    info = _web._db.get_lesson_info(document_id)
    if not info:
        return JSONResponse({"error": "Lesson not found"}, status_code=404)
    return JSONResponse(info)


@router.post("/api/pass0b-combined-json")
async def api_pass0b_combined_json(
    lesson_text: str = Form(""),
    selected_category: str = Form(""),
    selected_lesson: str = Form(""),
    provider: str = Form(""),
):
    """Run Pass A + both Pass 0b analyses, return structured JSON for the player."""
    text = lesson_text.strip()
    if not text:
        return JSONResponse({"error": "Please enter or select lesson text."}, status_code=400)

    prov = provider if provider else None

    try:
        somatic_result, lang_result = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(_web.run_pass_0b, text, prov),
                asyncio.to_thread(_web.run_pass_0b_lang, text, prov),
            ),
            timeout=_web.ANALYSIS_TIMEOUT,
        )
        whisper_segments = _web.load_whisper_segments(selected_category, selected_lesson)
        if whisper_segments:
            _web.align_pass0_with_whisper(somatic_result.get("pass_a", {}).get("pass_0", {}), whisper_segments)
            _web.align_pass0_with_whisper(lang_result.get("pass_a", {}).get("pass_0", {}), whisper_segments)

        # Build structured JSON
        pass_a = somatic_result.get("pass_a", {})
        groups = pass_a.get("groups", [])
        pa_steps = pass_a.get("steps", [])
        pass_0 = pass_a.get("pass_0", {})

        somatic_enriched = somatic_result.get("enriched_steps", [])
        lang_enriched = lang_result.get("enriched_steps", [])
        lang_summary = lang_result.get("summary", {})
        somatic_summary = somatic_result.get("summary", {})

        somatic_lookup = {e["step_number"]: e for e in somatic_enriched}
        lang_lookup = {e["step_number"]: e for e in lang_enriched}

        seg_lookup = {s["id"]: s for s in pass_0.get("segments", [])}

        steps_json = []
        for pa_step in pa_steps:
            sn = pa_step["step_number"]
            gn = pa_step.get("group_number")
            instruction = pa_step.get("instruction", "")
            tags = pa_step.get("tags", [])
            seg_ids = pa_step.get("segments", [])

            segments_data = []
            time_starts = []
            time_ends = []
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
                "group_number": gn,
                "instruction": instruction,
                "tags": tags,
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
            "lang_summary": lang_summary,
            "somatic_summary": somatic_summary,
            "elapsed_somatic": somatic_result.get("elapsed"),
            "elapsed_lang": lang_result.get("elapsed"),
            "cached_somatic": somatic_result.get("cached", False),
            "cached_lang": lang_result.get("cached", False),
        })
    except asyncio.TimeoutError:
        return JSONResponse({"error": "Analysis timed out."}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/pass0b-condensed")
async def api_pass0b_condensed(
    lesson_text: str = Form(""),
    provider: str = Form(""),
    condensed_provider: str = Form(""),
    skip_cache: str = Form(""),
    hint: str = Form(""),
):
    """Run Pass 0b + Pass 0b-lang (cached) then Pass 0b Condensed."""
    text = lesson_text.strip()
    if not text:
        return JSONResponse({"error": "Please enter or select lesson text."}, status_code=400)

    prov = provider if provider else None
    cond_prov = condensed_provider if condensed_provider else prov
    no_cache = skip_cache == "1"
    user_hint = hint.strip()

    try:
        somatic_result, lang_result = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(_web.run_pass_0b, text, prov),
                asyncio.to_thread(_web.run_pass_0b_lang, text, prov),
            ),
            timeout=_web.ANALYSIS_TIMEOUT,
        )

        condensed_result = await asyncio.wait_for(
            asyncio.to_thread(_web.run_pass_0b_condensed, somatic_result, lang_result, text, cond_prov, skip_cache=no_cache, hint=user_hint),
            timeout=_web.ANALYSIS_TIMEOUT,
        )

        return JSONResponse({
            "segments": condensed_result.get("segments", []),
            "elapsed": condensed_result.get("elapsed"),
            "cached": condensed_result.get("cached", False),
        })
    except asyncio.TimeoutError:
        return JSONResponse({"error": "Analysis timed out."}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/pass0b-lang/clear-cache")
async def api_pass0b_lang_clear_cache():
    """Clear Pass 0b-lang cache entries."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)

    rows = _web._db.list_cache()
    keys = []
    for r in rows:
        k = r["key"]
        try:
            data = _web._db.get_cache(k)
        except Exception:
            continue
        if not data:
            continue
        if "enriched_steps" in data and "summary" in data:
            keys.append(k)
    if keys:
        with _web._db.get_cursor() as cur:
            for k in keys:
                cur.execute("DELETE FROM cache WHERE key = %s", (k,))
    return JSONResponse({"deleted": len(keys)})


@router.post("/api/pass0b-lang")
async def api_pass0b_lang(
    lesson_text: str = Form(""),
    selected_category: str = Form(""),
    selected_lesson: str = Form(""),
    provider: str = Form(""),
):
    """Run Pass A + Pass 0b-lang and return results."""
    text = lesson_text.strip()
    if not text:
        return JSONResponse({"error": "Please enter or select lesson text."}, status_code=400)

    prov = provider if provider else None
    debug_log = []

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_web.run_pass_0b_lang, text, prov, debug_log),
            timeout=_web.ANALYSIS_TIMEOUT,
        )
        whisper_segments = _web.load_whisper_segments(selected_category, selected_lesson)
        if whisper_segments:
            _web.align_pass0_with_whisper(result.get("pass_a", {}).get("pass_0", {}), whisper_segments)
        result_html = _web.render_pass0b_lang_result(result)
        debug_html = _web.render_debug_log(debug_log) if debug_log else ""
        pass_a = result.get("pass_a", {})
        usage = result.get("usage", {})
        return JSONResponse({
            "status": "complete",
            "result_html": result_html,
            "debug_html": debug_html,
            "elapsed": result.get("elapsed"),
            "cached": result.get("cached", False),
            "step_count": len(pass_a.get("steps", [])),
            "group_count": len(pass_a.get("groups", [])),
            "enriched_count": len(result.get("enriched_steps", [])),
            "tokens_in": usage.get("tokens_in"),
            "tokens_out": usage.get("tokens_out"),
        })
    except asyncio.TimeoutError:
        return JSONResponse({"error": "Analysis timed out."}, status_code=504)
    except Exception as e:
        debug_html = _web.render_debug_log(debug_log) if debug_log else ""
        return JSONResponse({
            "error": str(e),
            "debug_html": debug_html,
        }, status_code=500)
