"""Lesson data API routes: somatic, language, classification, pipeline status, raw I/O."""

import html as html_mod
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

import web as _web
from cache_helpers import load_profile_result

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_lesson_text(document_id: str) -> str | None:
    """Get lesson transcript text for a document, used to compute cache keys."""
    if not _web._db:
        return None
    text = _web._db.get_document_transcription(document_id)
    return text.strip() if text else None


def _resolve_profile(profile_key: str | None) -> dict:
    """Return the requested analysis profile, or the canonical one."""
    return _web._db.resolve_analysis_profile(profile_key)


def _load_cache(profile: dict, text: str, suffix: str) -> tuple[str, dict | None]:
    """Load one profile's result for a step, with no fallback to another profile."""
    return load_profile_result(text, suffix, profile)


def _unknown_profile(error: ValueError) -> JSONResponse:
    return JSONResponse({"error": str(error)}, status_code=400)


# ── Prompt reconstruction (system prompts must match analysis code) ──────

_SYSTEM_PROMPTS = {
    ":decompose_v3": None,  # uses ROUTER_SYSTEM_PROMPT from analysis
    ":somatic_v2": (
        "You are an expert Feldenkrais practitioner enriching lesson steps. "
        "Process every step. Do not skip, merge, or split steps."
    ),
    ":language_v1": (
        "You are an expert in Feldenkrais pedagogy and language analysis. "
        "Analyze the teacher's language choices at each step. "
        "Process every step. Do not skip, merge, or split steps."
    ),
    ":concept_tags_v2": (
        "You are an expert Feldenkrais practitioner tagging a lesson with "
        "searchable concepts. Be specific and distinctive — avoid generic terms "
        "that apply to every lesson."
    ),
    ":spatial_v2": (
        "You are a factual observer describing the physical geometry of body "
        "positions and configurations. Report only what is stated — no "
        "interpretation, no biomechanical analysis, no pedagogical language."
    ),
    ":condense_v4": (
        "You are an expert Feldenkrais practitioner creating condensed lesson cards. "
        "Preserve all five points of view. Generate group cards first, then the setting card."
    ),
}


def _ensure_prompt_io(profile: dict, text: str, io_steps: list) -> None:
    """Reconstruct and cache any missing prompt IO entries for a lesson.

    Uses current prompt templates + cached upstream data to re-assemble prompts.
    No LLM calls — purely local formatting. Only contract-2 profiles: the
    templates on disk are the contract-2 prompts.
    """
    if int(profile.get("contract_version") or 2) != 2:
        return
    try:
        import analysis as _analysis
    except ImportError:
        return

    # Check which steps need prompt reconstruction
    missing = []
    for step in io_steps:
        cache_key, result = _load_cache(profile, text, step["suffix"])
        prompt = _web.load_cached_analysis(cache_key + ":prompt")
        if result and not prompt:
            missing.append(step)

    if not missing:
        return

    missing_suffixes = {s["suffix"] for s in missing}

    try:
        # Load Pass A data (needed by most steps)
        pa_key, pa_data = _load_cache(profile, text, ":decompose_v3")

        # --- Decompose ---
        if ":decompose_v3" in missing_suffixes and pa_data:
            pass_0_result = _analysis.run_pass_0(text)
            segmented_text = _analysis.format_text_with_segments(pass_0_result)
            pa_prompt_template, pa_schema = _analysis.load_pass_a()
            if pa_prompt_template:
                prompt = pa_prompt_template + "\n\n---\n\n## Lesson to Analyze:\n\n" + segmented_text
                _web.save_cached_analysis(pa_key + ":prompt", {
                    "prompt": prompt,
                    "system": _analysis.ROUTER_SYSTEM_PROMPT,
                    "schema": pa_schema,
                })

        if not pa_data:
            return

        # --- Somatic ---
        som_key, som_data = _load_cache(profile, text, ":somatic_v2")
        if ":somatic_v2" in missing_suffixes and som_data:
            steps_text = _analysis.format_steps_for_0b(pa_data)
            tmpl, schema = _analysis.load_pass_0b()
            _web.save_cached_analysis(som_key + ":prompt", {
                "prompt": tmpl + steps_text,
                "system": _SYSTEM_PROMPTS[":somatic_v2"],
                "schema": schema,
            })

        # --- Language ---
        lang_key, lang_data = _load_cache(profile, text, ":language_v1")
        if ":language_v1" in missing_suffixes and lang_data:
            steps_text = _analysis.format_steps_for_0b(pa_data)
            tmpl, schema = _analysis.load_pass_0b_lang()
            _web.save_cached_analysis(lang_key + ":prompt", {
                "prompt": tmpl + steps_text,
                "system": _SYSTEM_PROMPTS[":language_v1"],
                "schema": schema,
            })

        # --- Concepts ---
        ct_key, ct_data = _load_cache(profile, text, ":concept_tags_v2")
        if ":concept_tags_v2" in missing_suffixes and ct_data and som_data:
            somatic_steps = som_data.get("enriched_steps", [])
            concept_input = _analysis.format_input_for_concepts(pa_data, somatic_steps)
            tmpl, schema = _analysis.load_pass_0b_concepts()
            _web.save_cached_analysis(ct_key + ":prompt", {
                "prompt": tmpl + concept_input,
                "system": _SYSTEM_PROMPTS[":concept_tags_v2"],
                "schema": schema,
            })

        # --- Spatial ---
        sp_key, sp_data = _load_cache(profile, text, ":spatial_v2")
        if ":spatial_v2" in missing_suffixes and sp_data and som_data:
            somatic_steps = som_data.get("enriched_steps", [])
            spatial_input = _analysis.format_input_for_spatial(pa_data, somatic_steps)
            tmpl, schema = _analysis.load_pass_0b_spatial()
            _web.save_cached_analysis(sp_key + ":prompt", {
                "prompt": tmpl + spatial_input,
                "system": _SYSTEM_PROMPTS[":spatial_v2"],
                "schema": schema,
            })

        # --- Condensed ---
        cond_key, cond_data = _load_cache(profile, text, ":condense_v4")
        if ":condense_v4" in missing_suffixes and cond_data and som_data and lang_data:
            somatic_enriched = som_data.get("enriched_steps", [])
            somatic_summary = som_data.get("summary", {})
            lang_enriched = lang_data.get("enriched_steps", [])
            lang_summary = lang_data.get("summary", {})
            cond_input = _analysis.format_input_for_0b_condensed(
                pa_data, somatic_enriched, somatic_summary, lang_enriched, lang_summary,
            )
            tmpl, schema = _analysis.load_pass_0b_condensed()
            _web.save_cached_analysis(cond_key + ":prompt", {
                "prompt": tmpl + cond_input,
                "system": _SYSTEM_PROMPTS[":condense_v4"],
                "schema": schema,
            })

    except Exception:
        logger.exception("Failed to reconstruct prompt IO")


@router.get("/api/lesson/{document_id}/combined")
async def api_lesson_combined(document_id: str, profile: str | None = None):
    """Return combined somatic + language analysis in the structured JSON format
    expected by the Step by Step and Transcript tabs."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)
    try:
        analysis_profile = _resolve_profile(profile)
    except ValueError as e:
        return _unknown_profile(e)

    # Load cached results — try pipeline key first, then legacy
    pass_a = None
    for suffix in [":decompose_v3", ":pass_a", ":pass_a_v1"]:
        _, pass_a = _load_cache(analysis_profile, text, suffix)
        if pass_a:
            break
    if not pass_a:
        return JSONResponse({"error": "Decomposition not yet available. Run the analysis pipeline first.",
                             "not_analyzed": True, "profile": analysis_profile["key"]}, status_code=404)

    _, somatic_result = _load_cache(analysis_profile, text, ":somatic_v2")
    _, lang_result = _load_cache(analysis_profile, text, ":language_v1")
    somatic_result = somatic_result or {}
    lang_result = lang_result or {}

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
async def api_lesson_somatic(document_id: str, profile: str | None = None):
    """Return cached somatic (Pass 0b) analysis for a lesson."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)
    try:
        analysis_profile = _resolve_profile(profile)
    except ValueError as e:
        return _unknown_profile(e)

    _, cached = _load_cache(analysis_profile, text, ":somatic_v2")
    if not cached:
        return JSONResponse({"error": "Somatic analysis not yet available. Run the analysis pipeline first."}, status_code=404)

    # Also load pass_a for step context
    _, pass_a = _load_cache(analysis_profile, text, ":decompose_v3")

    return JSONResponse({
        "enriched_steps": cached.get("enriched_steps", []),
        "summary": cached.get("summary", {}),
        "pass_a": pass_a or {},
    })


@router.get("/api/lesson/{document_id}/language")
async def api_lesson_language(document_id: str, profile: str | None = None):
    """Return cached language (Pass 0b) analysis for a lesson."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)
    try:
        analysis_profile = _resolve_profile(profile)
    except ValueError as e:
        return _unknown_profile(e)

    _, cached = _load_cache(analysis_profile, text, ":language_v1")
    if not cached:
        return JSONResponse({"error": "Language analysis not yet available. Run the analysis pipeline first."}, status_code=404)

    # Also load pass_a for step context
    _, pass_a = _load_cache(analysis_profile, text, ":decompose_v3")

    return JSONResponse({
        "enriched_steps": cached.get("enriched_steps", []),
        "summary": cached.get("summary", {}),
        "pass_a": pass_a or {},
    })


@router.get("/api/lesson/{document_id}/condensed")
async def api_lesson_condensed(document_id: str, profile: str | None = None):
    """Return cached condensed analysis for a lesson."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)
    try:
        analysis_profile = _resolve_profile(profile)
    except ValueError as e:
        return _unknown_profile(e)

    _, cached = _load_cache(analysis_profile, text, ":condense_v4")
    if not cached:
        return JSONResponse({"error": "Condensed analysis not yet available. Run the analysis pipeline first."}, status_code=404)

    return JSONResponse({
        "segments": cached.get("segments", []),
        "cached": True,
    })


@router.get("/api/lesson/{document_id}/classification")
async def api_lesson_classification(document_id: str, profile: str | None = None):
    """Return cached Pass A (decomposition/classification) analysis for a lesson."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)
    try:
        analysis_profile = _resolve_profile(profile)
    except ValueError as e:
        return _unknown_profile(e)

    pass_a = None
    for suffix in [":decompose_v3", ":pass_a", ":pass_a_v1"]:
        _, pass_a = _load_cache(analysis_profile, text, suffix)
        if pass_a:
            break
    if not pass_a:
        return JSONResponse({"error": "Classification analysis not yet available. Run the analysis pipeline first."}, status_code=404)

    return JSONResponse(pass_a)


@router.get("/api/lesson/{document_id}/pipeline-status")
async def api_lesson_pipeline_status(document_id: str, profile: str | None = None):
    """Return one profile's pipeline analysis state and document metadata for a lesson."""
    if not _web.PIPELINE_LOCAL_CONTROLS:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    try:
        analysis_profile = _resolve_profile(profile)
    except ValueError as e:
        return _unknown_profile(e)

    # Get document metadata
    doc = _web._db.get_document(document_id)
    if not doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)

    # Get all analysis step definitions, ordered by DAG depth
    steps = _web._db.get_analysis_steps()
    # Topological order (DAG depth), alphabetical tiebreaker
    _DAG_ORDER = [
        "decompose", "extract_steps",
        "language", "map_body_parts", "somatic", "spatial_v3", "print_prep",
        "concept_tags", "condense", "language_patterns", "spatial",
        "embed", "embed_positions", "index_concepts", "index_spatial",
    ]
    _dag_rank = {name: i for i, name in enumerate(_DAG_ORDER)}
    steps.sort(key=lambda s: _dag_rank.get(s["name"], 999))

    # Get per-step state for this document
    states = []
    for step in steps:
        state = _web._db.get_document_state(document_id, step["name"], analysis_profile["key"])
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

    return JSONResponse({"document": doc_meta, "profile": analysis_profile["key"], "steps": states})


@router.get("/api/lesson/{document_id}/raw-io")
async def api_lesson_raw_io(document_id: str, profile: str | None = None):
    """Return cached debug/raw I/O logs for analysis steps if available."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)
    try:
        analysis_profile = _resolve_profile(profile)
    except ValueError as e:
        return _unknown_profile(e)

    # Check known cache suffixes for raw I/O data
    io_steps = [
        {"name": "Pass A (Decompose)", "step_name": "decompose", "suffix": ":decompose_v3"},
        {"name": "Somatic", "step_name": "somatic", "suffix": ":somatic_v2"},
        {"name": "Language", "step_name": "language", "suffix": ":language_v1"},
        {"name": "Condensed", "step_name": "condense", "suffix": ":condense_v4"},
        {"name": "Concepts", "step_name": "concept_tags", "suffix": ":concept_tags_v2"},
        {"name": "Spatial", "step_name": "spatial", "suffix": ":spatial_v2"},
    ]

    # Reconstruct missing prompts on demand from templates + cached upstream data
    _ensure_prompt_io(analysis_profile, text, io_steps)

    results = []
    for step in io_steps:
        cache_key, cached = _load_cache(analysis_profile, text, step["suffix"])
        # Check pipeline state (stale/error/completed)
        step_state = _web._db.get_document_state(
            document_id, step.get("step_name", ""), analysis_profile["key"]
        ) if _web._db else None
        is_stale = step_state.get("stale", False) if step_state else False
        is_error = bool(step_state.get("error")) if step_state else False
        entry = {
            "name": step["name"],
            "step_name": step.get("step_name", step["name"]),
            "cache_key": cache_key,
            "has_data": cached is not None,
            "stale": is_stale,
            "error": step_state.get("error") if is_error else None,
        }
        if cached:
            # Show a preview of the raw data (truncate large values)
            raw_json = json.dumps(cached, indent=2, default=str)
            entry["raw_json"] = raw_json[:50000] if len(raw_json) > 50000 else raw_json
            entry["raw_json_length"] = len(raw_json)
            entry["truncated"] = len(raw_json) > 50000
            # Extract top-level keys for summary
            entry["top_level_keys"] = list(cached.keys()) if isinstance(cached, dict) else []

        # Load prompt IO if available (stored under {cache_key}:prompt)
        prompt_data = _web.load_cached_analysis(cache_key + ":prompt")
        if prompt_data:
            entry["has_prompt"] = True
            entry["prompt"] = prompt_data.get("prompt", "")
            entry["system"] = prompt_data.get("system", "")
            if prompt_data.get("schema"):
                entry["schema"] = json.dumps(prompt_data["schema"], indent=2)
        else:
            entry["has_prompt"] = False

        results.append(entry)

    return JSONResponse({"steps": results})


@router.get("/api/lesson/{document_id}/profiles")
async def api_lesson_profiles(document_id: str):
    """List analysis profiles and how far each has analyzed this lesson.

    Feeds the lesson page's model selector on every deployment. Whether a
    profile is switched on is pipeline state, so it is reported only locally.
    """
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    done_by_profile: dict[str, list[str]] = {}
    for state in _web._db.get_document_states(document_id):
        if state.get("completed_version") is not None and not state.get("error"):
            done_by_profile.setdefault(state["profile_key"], []).append(state["step_name"])
    profiles = [
        {
            "key": p["key"],
            "provider": p["provider"],
            "model": p["model"],
            "contract_version": p["contract_version"],
            "canonical": p["canonical"],
            "completed_steps": sorted(done_by_profile.get(p["key"], [])),
            **({"enabled": p["enabled"]} if _web.PIPELINE_LOCAL_CONTROLS else {}),
        }
        for p in _web._db.get_analysis_profiles()
    ]
    return JSONResponse({"profiles": profiles})


@router.get("/api/lesson/{document_id}/context")
async def api_lesson_context(request: Request, document_id: str, profile: str | None = None,
                             download: bool = False):
    """The lesson and one model's analysis as a Markdown document for an outside LLM.

    Holds the full transcript, so it applies the same source-access check as the
    lesson page itself.
    """
    from datetime import datetime, timezone
    import conversation_import
    import lesson_context

    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    _web._set_source_access(request)
    doc = _web._db.get_document(document_id)
    if not doc or not _web._db.is_document_accessible(document_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    text = _get_lesson_text(document_id)
    if not text:
        return JSONResponse({"error": "No transcript available"}, status_code=404)
    try:
        analysis_profile = _resolve_profile(profile or _web._db.default_profile_for_document(document_id))
    except ValueError as e:
        return _unknown_profile(e)

    results = {name: _load_cache(analysis_profile, text, suffix)[1] for name, suffix in [
        ("pass_a", ":decompose_v3"), ("somatic", ":somatic_v2"), ("language", ":language_v1"),
        ("condensed", ":condense_v4"), ("concepts", ":concept_tags_v2"), ("positions", ":spatial_v3"),
    ]}
    segments = _web._db.get_document_whisper_segments(document_id)
    if results["pass_a"] and segments and hasattr(_web, "align_pass0_with_whisper"):
        _web.align_pass0_with_whisper(results["pass_a"].get("pass_0", {}), segments)

    source_name = ""
    if doc.get("source_id"):
        with _web._db.get_cursor(commit=False) as cur:
            cur.execute("SELECT category_name, name FROM document_source WHERE id = %s", (doc["source_id"],))
            row = cur.fetchone()
            if row:
                source_name = row.get("category_name") or row.get("name", "")

    base_url = _web._auth_mod._base_url(request)
    lesson_url = f"{base_url}/lesson/{document_id}?profile={analysis_profile['key']}"
    saving = None
    if _web._auth_mod.COOKIE_SECRET:
        user = _web._auth_mod.get_current_user(request)
        token, expires = conversation_import.issue_token(
            _web._auth_mod.COOKIE_SECRET, document_id=document_id,
            user_id=user.get("user_id") if user else None, profile_key=analysis_profile["key"])
        saving = {"endpoint": base_url + conversation_import.IMPORT_PATH, "token": token,
                  "expires": expires.strftime("%Y-%m-%d"), "bucket": _web.KB_BUCKET}
    body = lesson_context.build_lesson_context(
        document=doc, source_name=source_name, lesson_url=lesson_url, profile=analysis_profile,
        transcript=text, whisper_segments=segments,
        field_descriptions=lesson_context.schema_field_descriptions(_web.DATA_DIR),
        exported_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        saving=saving,
        **results,
    )
    headers = {}
    if download:
        name = f"{lesson_context.slug(doc.get('title'))}.{analysis_profile['key']}.md"
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return PlainTextResponse(body, media_type="text/markdown; charset=utf-8", headers=headers)


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
    # Get source name via query
    source_name = ""
    if doc.get("source_id"):
        with _web._db.get_cursor(commit=False) as cur:
            cur.execute("SELECT category_name, name FROM document_source WHERE id = %s", (doc["source_id"],))
            row = cur.fetchone()
            if row:
                source_name = row.get("category_name") or row.get("name", "")
    # Use source_url if available; fall back to presigned S3 URL for audio
    source_url = doc.get("source_url")
    if not source_url and doc.get("s3_key") and _web.KB_BUCKET:
        try:
            from storage import Storage
            storage = Storage(_web.KB_BUCKET)
            source_url = storage.generate_presigned_url(doc["s3_key"], expiry_seconds=7200)
        except Exception:
            pass

    # Only flag has_audio when the S3 object is actually playable media.
    # Some text-only lessons have an s3_key pointing at a .txt sidecar; the
    # browser's <audio> control renders "Error" if asked to decode those.
    _s3_key = doc.get("s3_key") or ""
    _ext = _s3_key.rsplit(".", 1)[-1].lower() if "." in _s3_key else ""
    has_audio = _ext in ("mp3", "wav", "m4a", "aac", "ogg", "flac", "opus",
                         "mp4", "mov", "webm", "mkv")

    return JSONResponse({
        "title": doc.get("title", ""),
        "source_url": source_url,
        "source_name": source_name,
        "has_audio": has_audio,
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
    expiry = 7200
    url = storage.generate_presigned_url(doc["s3_key"], expiry_seconds=expiry)
    # The lesson page refreshes the URL once this passes; a seek or reconnect
    # on an expired URL gets a 403 and the player stalls.
    return JSONResponse({"audio_url": url, "expires_at": int(time.time()) + expiry})


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
