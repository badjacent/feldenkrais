"""Search routes: search API, lesson page, lesson chat, lesson text/concepts."""

import asyncio
import html
import json
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

import web as _web
from cache_helpers import load_profile_result

router = APIRouter()


@router.post("/api/search")
async def api_search(request: Request, query: str = Form(""), method: str = Form("default")):
    """Semantic search over lesson embeddings. Supports multiple search methods."""
    query = query.strip()
    if not query:
        return JSONResponse({"error": "Empty query"}, status_code=400)
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    if method not in ("default", "concepts", "positions", "tags", "combined"):
        return JSONResponse({"error": f"Unknown method: {method}"}, status_code=400)
    _web._set_source_access(request)
    try:
        if method == "tags":
            terms = [t.strip() for t in query.split() if t.strip()]
            results = _web._db.search_by_concepts(terms, limit=10)
            for r in results:
                r["similarity"] = r.pop("match_count", 0)
                matched = r.pop("matched_concepts", [])
                r["snippet"] = ", ".join(matched[:8]) if matched else ""
                src_id = r.pop("source_id", None)
                cat_name = r.pop("category_name", None)
                if src_id and cat_name:
                    r["category"] = f"db:{src_id}:{cat_name}"
                elif src_id:
                    r["category"] = f"db:{src_id}"
            return JSONResponse({"results": results, "method": "tags"})

        from llm import get_embeddings
        embeddings, _ = get_embeddings([query], task_type="RETRIEVAL_QUERY")

        cascade_mode = None
        if method == "combined":
            results, band, gap, cascade_mode = _web._combined_search(query, embeddings[0])
        else:
            results, band, gap = _web._single_method_search(embeddings[0], method)

        for i, r in enumerate(results):
            r["similarity"] = round(r["similarity"], 4)
            if i < 10 and r.get("summary_text"):
                r["snippet"] = r["summary_text"][:400]
            elif i >= 10:
                r.pop("snippet", None)
            r.pop("summary_text", None)
            src_id = r.pop("source_id", None)
            cat_name = r.pop("category_name", None)
            if src_id and cat_name:
                r["category"] = f"db:{src_id}:{cat_name}"
            elif src_id:
                r["category"] = f"db:{src_id}"
        # Log query for usage analysis (fire-and-forget)
        try:
            top5 = [
                {"doc_id": r.get("document_id"), "title": r.get("title"), "similarity": r.get("similarity")}
                for r in results[:5]
            ]
            _user = _web._auth_mod.get_current_user(request)
            _web._db.log_search_query(
                query=query,
                result_count=len(results),
                specificity_band=band,
                specificity_gap=gap,
                top_results=top5,
                user_id=_user["user_id"] if _user else None,
            )
        except Exception:
            pass  # never break search for logging
        resp = {"results": results, "specificity": {"gap": gap, "band": band}, "method": method}
        if cascade_mode:
            resp["cascade_mode"] = cascade_mode
        return JSONResponse(resp)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/lesson-chat-history/{document_id}")
async def api_lesson_chat_history(request: Request, document_id: str):
    """Per-lesson chat history for the current user (superuser sees all users)."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    user = _web._auth_mod.get_current_user(request) if _web._auth_mod else None
    is_super = _web._is_superuser_active(request, user)

    if is_super:
        turns = _web._db.get_lesson_chat_history_all(document_id)
    else:
        user_id = user.get("user_id") if user else None
        turns = _web._db.get_lesson_chat_history(document_id, user_id)

    return JSONResponse({
        "document_id": document_id,
        "is_superuser_view": is_super,
        "turns": [
            {
                "id": t["id"],
                "user_id": t.get("user_id"),
                "query": t["query"],
                "response": t["response"],
                "origin": t.get("origin") or "app",
                "assistant": t.get("assistant"),
                "created_at": t["created_at"].isoformat() if t.get("created_at") else None,
            }
            for t in turns
        ],
    })


@router.get("/api/conversations")
async def api_conversations(request: Request):
    """Cross-lesson conversation index for the current user (superuser sees everyone)."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    user = _web._auth_mod.get_current_user(request) if _web._auth_mod else None
    is_super = _web._is_superuser_active(request, user)

    rows = _web._db.get_chat_lessons_summary(
        user_id=user.get("user_id") if user else None,
        all_users=is_super,
    )

    return JSONResponse({
        "is_superuser_view": is_super,
        "lessons": [
            {
                "document_id": r["document_id"],
                "title": r.get("title") or "(untitled)",
                "user_id": r.get("user_id"),
                "user_email": r.get("user_email"),
                "last_chat_at": r["last_chat_at"].isoformat() if r.get("last_chat_at") else None,
                "turn_count": r["turn_count"],
            }
            for r in rows
        ],
    })


@router.get("/conversations", response_class=HTMLResponse)
async def conversations_page(request: Request):
    """Central page listing the current user's chat history across all lessons."""
    user = _web._set_source_access(request)
    if not user and not _web._is_trusted_local(request):
        return HTMLResponse("<h1>Sign in to view your conversations.</h1>", status_code=401)
    return HTMLResponse(_web.build_conversations_page_html(
        user=user,
        superuser_active=_web._is_superuser_active(request, user),
    ))


PRINT_NEEDS = ["decompose", "spatial_v3", "print_prep"]


def _print_model(document_id: str, profile: str | None) -> dict:
    """One profile's cached print model and what the print views need around it.

    Read-only: never starts an analysis. Raises ValueError for an unknown profile.
    """
    doc = _web._db.get_document(document_id) or {}
    text = (_web._db.get_document_transcription(document_id) or "").strip()
    if profile:
        candidates = [_web._db.resolve_analysis_profile(profile)]
    else:
        # Without ?profile=, print what the lesson page opens on, or else the
        # first profile that has prepared the lesson.
        opens_on = _web._db.resolve_analysis_profile(_web._db.default_profile_for_document(document_id))
        candidates = [opens_on] + [p for p in _web._db.get_analysis_profiles() if p["key"] != opens_on["key"]]
    analysis_profile, model = candidates[0], None
    for candidate in candidates if text else []:
        model = load_profile_result(text, ":print_prep_v1", candidate)[1]
        if model:
            analysis_profile = candidate
            break
    state = _web._db.get_document_state(document_id, "print_prep", analysis_profile["key"]) if model else None
    # A requested profile without a model: name the ones that have one.
    others = [] if model or not text else [
        p["key"] for p in _web._db.get_analysis_profiles()
        if p["key"] != analysis_profile["key"] and load_profile_result(text, ":print_prep_v1", p)[1]]
    meta = [str(doc[field]) for field in ("presenter", "lesson_date") if doc.get(field)]
    return {"doc": doc, "profile": analysis_profile, "text": text, "model": model,
            "stale": bool(state and state.get("stale")), "meta": meta, "others": others}


@router.get("/lesson/{document_id}/print", response_class=HTMLResponse)
async def lesson_print_page(request: Request, document_id: str):
    """Lesson Step Table (print_prep) for one profile, printable offline."""
    _web._set_source_access(request)
    if not _web._db:
        return HTMLResponse("<h1>No database connection</h1>", status_code=500)
    if not _web._db.is_document_accessible(document_id):
        return HTMLResponse("<h1>Not Found</h1>", status_code=404)
    try:
        prepared = _print_model(document_id, request.query_params.get("profile"))
    except ValueError as e:
        return HTMLResponse(f"<p>{html.escape(str(e))}</p>", status_code=400)
    return HTMLResponse(_web.build_lesson_print_html(
        document_id, title=prepared["doc"].get("title"), model=prepared["model"],
        profile=prepared["profile"]["key"], meta=prepared["meta"], stale=prepared["stale"],
        missing=PRINT_NEEDS, other_profiles=prepared["others"],
        analysis_url=f"/analysis?profile={prepared['profile']['key']}" if _web.PIPELINE_LOCAL_CONTROLS else None))


@router.get("/lesson/{document_id}/print.docx")
async def lesson_print_docx(request: Request, document_id: str):
    """Annotated Step Transcript (DOCX) for one profile, built on request."""
    _web._set_source_access(request)
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    if not _web._db.is_document_accessible(document_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        prepared = _print_model(document_id, request.query_params.get("profile"))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    profile_key = prepared["profile"]["key"]
    if not prepared["model"]:
        return JSONResponse({"error": f"No print model for this lesson under {profile_key}",
                             "needs": PRINT_NEEDS}, status_code=404)
    pass_a = load_profile_result(prepared["text"], ":decompose_v3", prepared["profile"])[1]
    if not pass_a:
        return JSONResponse({"error": f"No decomposition for this lesson under {profile_key}",
                             "needs": ["decompose"]}, status_code=404)
    try:
        # Imported lazily so deployments without the web extra can still load other routes.
        from print_docx import IncompleteTranscriptError, build_annotated_transcript
    except ImportError:
        return JSONResponse({"error": "Word export is not available on this deployment"},
                            status_code=503)
    title = prepared["doc"].get("title") or "Lesson"
    try:
        data = build_annotated_transcript(prepared["model"], pass_a, title,
                                          source_label=" · ".join(prepared["meta"]) or None)
    except IncompleteTranscriptError as e:
        return JSONResponse({"error": f"The decomposition does not cover the transcript: {e}"},
                            status_code=409)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "lesson"
    return Response(content=data, headers={
        "Content-Disposition": f'attachment; filename="{slug}-annotated-step-transcript.docx"'},
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.get("/lesson/{document_id}", response_class=HTMLResponse)
async def lesson_detail_page(request: Request, document_id: str):
    """Unified lesson detail page."""
    user = _web._set_source_access(request)
    if _web._db and not _web._db.is_document_accessible(document_id):
        return HTMLResponse("<h1>Not Found</h1>", status_code=404)
    if _web._db:
        try:
            _web._db.log_lesson_view(document_id, user.get("user_id") if user else None)
        except Exception:
            pass
    default_profile = None
    if _web._db:
        try:
            default_profile = _web._db.default_profile_for_document(document_id)
        except Exception:
            default_profile = None  # the page falls back to the canonical profile
    return HTMLResponse(_web.build_lesson_page_html(
        document_id, user=user, search_mode=_web._is_search_site(request),
        superuser_active=_web._is_superuser_active(request, user),
        pipeline_visible=_web.PIPELINE_LOCAL_CONTROLS,
        default_profile=default_profile,
    ))


@router.get("/api/lesson/{document_id}/concepts")
async def api_lesson_concepts(document_id: str, profile: str | None = None):
    """Get one profile's concept extraction data for a lesson (canonical by default)."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    try:
        analysis_profile = _web._db.resolve_analysis_profile(profile)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        concepts = _web._db.get_lesson_concepts(document_id, analysis_profile["key"])
        text = _web._db.get_document_transcription(document_id)
        extra = {}
        if text:
            _, cached = load_profile_result(text.strip(), ":concept_tags_v2", analysis_profile)
            if cached:
                extra["distinctive_summary"] = cached.get("distinctive_summary", "")
                extra["search_terms"] = cached.get("search_terms", [])
        return JSONResponse({"concepts": concepts, **extra})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _convert_v2_to_v3(v2: dict, pa: dict | None) -> tuple[list, list]:
    """Convert spatial v2 (group-level) to v3-like (step-level) format.

    Uses v2 structured data + pass_a steps to produce a best-effort
    step-level position sequence. Less detailed than native v3 but
    ensures all lessons with v2 data show positions.
    """
    groups_v2 = v2.get("groups", [])
    v2_by_group = {g.get("group_number"): g for g in groups_v2}

    steps_out = []
    seen_positions = []
    seen_set = set()

    # Build step-level data from pass_a if available
    if pa:
        pa_groups = pa.get("groups", [])
        pa_steps = {s["step_number"]: s for s in pa.get("steps", [])}

        # Map step → group number
        step_to_group = {}
        for g in pa_groups:
            for sn in g.get("steps", []):
                step_to_group[sn] = g.get("group_number", 0)

        for g in pa_groups:
            gnum = g.get("group_number", 0)
            v2g = v2_by_group.get(gnum, {})
            group_pos = _v2_group_to_position(v2g, g)

            for sn in g.get("steps", []):
                s = pa_steps.get(sn, {})
                # Use step's own starting_position if available, else group position
                step_pos = s.get("starting_position") or group_pos
                is_home = s.get("is_home", False)
                tags = s.get("tags", [])

                steps_out.append({
                    "step_number": sn,
                    "group_number": gnum,
                    "position": step_pos,
                    "action": s.get("instruction", ""),
                    "is_transition": False,
                    "is_rest": is_home or "P" in tags,
                })

                if step_pos and step_pos not in seen_set:
                    seen_set.add(step_pos)
                    seen_positions.append({"position": step_pos, "alternates": []})
    else:
        # No pass_a — just produce group-level entries
        for g in groups_v2:
            gnum = g.get("group_number", 0)
            pos = _v2_group_to_position(g)
            movements = g.get("primary_movements", [])
            action = "; ".join(movements) if movements else ""

            steps_out.append({
                "step_number": gnum,
                "group_number": gnum,
                "position": pos,
                "action": action,
                "is_transition": False,
                "is_rest": False,
            })

            if pos and pos not in seen_set:
                seen_set.add(pos)
                seen_positions.append({"position": pos, "alternates": []})

    return steps_out, seen_positions


def _v2_group_to_position(v2g: dict, pa_group: dict | None = None) -> str:
    """Convert a v2 spatial group to a plain-language position string."""
    parts = []

    # Base position
    pos = v2g.get("position", "")
    pos_map = {
        "supine": "Lying on back",
        "prone": "Lying on stomach",
        "side_lying_left": "Lying on left side",
        "side_lying_right": "Lying on right side",
        "sitting": "Sitting",
        "standing": "Standing",
        "kneeling": "Kneeling",
        "all_fours": "On hands and knees",
    }
    parts.append(pos_map.get(pos, pos or "Unknown position"))

    # Holds
    for h in v2g.get("holds", []):
        a, b = h.get("part_a", ""), h.get("part_b", "")
        if a and b:
            parts.append(f"{a} on {b}")

    # Key contacts (skip generic floor contacts)
    for c in v2g.get("contacts", []):
        qualifier = c.get("qualifier", "")
        if qualifier:
            parts.append(f"{c.get('part', '')} {qualifier}")

    # Constraints
    for c in v2g.get("constraints", []):
        parts.append(f"{c.get('part', '')}: {c.get('rule', '')}")

    # If we have a pa_group starting_position, prefer it for the base
    if pa_group and pa_group.get("starting_position"):
        base = pa_group["starting_position"]
        # Append any structured details not already in the base
        extras = parts[1:]  # skip the position we're replacing
        if extras:
            return f"{base}, {', '.join(extras)}"
        return base

    return ", ".join(parts)


@router.get("/api/lesson/{document_id}/print-model")
async def api_lesson_print_model(request: Request, document_id: str, profile: str | None = None):
    """Get one profile's print model (print_prep) for a lesson, read-only.

    Never starts an analysis: a lesson without a prepared model reports what is
    missing.
    """
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    _web._set_source_access(request)
    if not _web._db.is_document_accessible(document_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        analysis_profile = _web._db.resolve_analysis_profile(profile)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    text = _web._db.get_document_transcription(document_id)
    if not text:
        return JSONResponse({"error": "No transcript"}, status_code=404)
    _, model = load_profile_result(text.strip(), ":print_prep_v1", analysis_profile)
    if not model:
        return JSONResponse({"available": False, "profile": analysis_profile["key"],
                             "needs": ["decompose", "spatial_v3", "print_prep"]})
    state = _web._db.get_document_state(document_id, "print_prep", analysis_profile["key"])
    return JSONResponse({"available": True, "profile": analysis_profile["key"],
                         "stale": bool(state and state.get("stale")), "model": model})


@router.get("/api/lesson/{document_id}/positions")
async def api_lesson_positions(document_id: str, profile: str | None = None):
    """Get one profile's step-level position sequence (spatial v3) for a lesson.

    Falls back to converting that profile's spatial v2 data if v3 isn't available.
    """
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    try:
        analysis_profile = _web._db.resolve_analysis_profile(profile)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        text = _web._db.get_document_transcription(document_id)
        if not text:
            return JSONResponse({"error": "No transcript"}, status_code=404)
        # Try v3 first
        _, cached = load_profile_result(text.strip(), ":spatial_v3", analysis_profile)
        if cached:
            return JSONResponse({
                "steps": cached.get("steps", []),
                "position_summary": cached.get("position_summary", []),
                "available": True,
                "version": "v3",
            })

        # Fall back to v2 → convert to v3-like format
        _, v2 = load_profile_result(text.strip(), ":spatial_v2", analysis_profile)
        if not v2:
            return JSONResponse({"steps": [], "position_summary": [], "available": False})

        # Also load pass_a for step-level data
        _, pa = load_profile_result(text.strip(), ":decompose_v3", analysis_profile)

        steps, summary = _convert_v2_to_v3(v2, pa)
        return JSONResponse({
            "steps": steps,
            "position_summary": summary,
            "available": True,
            "version": "v2-converted",
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/lesson-text/{category}/{filename}")
async def api_lesson_text(category: str, filename: str):
    """Return lesson text for a given category and lesson key."""
    text = _web.load_lesson(category, filename)
    if text:
        return JSONResponse({"text": text})
    return JSONResponse({"error": "Lesson not found"}, status_code=404)


@router.post("/api/lesson-chat")
async def api_lesson_chat(request: Request):
    """Lesson chat endpoint -- converse with the lesson using analysis context."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "No messages provided."}, status_code=400)

    provider_key = body.get("provider") or None
    context_config = body.get("context_config", {})
    lesson_text = body.get("lesson_text", "").strip()

    if not lesson_text:
        return JSONResponse({"error": "No lesson text provided."}, status_code=400)

    # Load cached analysis data from lesson text hash
    somatic_result = None
    lang_result = None
    condensed_result = None

    som_suffix = ":somatic_v2"
    if provider_key and provider_key != _web.DEFAULT_LLM:
        som_suffix += f":{provider_key}"
    som_cached = _web.load_cached_analysis(_web.get_cache_key(lesson_text + som_suffix))
    if som_cached:
        pass_a_cached = _web.load_cached_analysis(_web.get_cache_key(lesson_text + ":pass_a"))
        somatic_result = {
            "pass_a": pass_a_cached or {},
            "enriched_steps": som_cached.get("enriched_steps", []),
            "summary": som_cached.get("summary", {}),
        }

    lang_suffix = ":language_v1"
    if provider_key and provider_key != _web.DEFAULT_LLM:
        lang_suffix += f":{provider_key}"
    lang_cached = _web.load_cached_analysis(_web.get_cache_key(lesson_text + lang_suffix))
    if lang_cached:
        lang_result = {
            "enriched_steps": lang_cached.get("enriched_steps", []),
            "summary": lang_cached.get("summary", {}),
        }

    cond_suffix = ":condense_v4"
    if provider_key and provider_key != _web.DEFAULT_LLM:
        cond_suffix += f":{provider_key}"
    cond_cached = _web.load_cached_analysis(_web.get_cache_key(lesson_text + cond_suffix))
    if cond_cached:
        condensed_result = {"segments": cond_cached.get("segments", [])}

    system_prompt = _web.assemble_chat_context(
        lesson_text, context_config,
        somatic_result=somatic_result,
        lang_result=lang_result,
        condensed_result=condensed_result,
    )

    try:
        llm = _web.get_llm(provider_key)
        chat_messages = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant")]

        response_text = await asyncio.to_thread(
            llm.chat, chat_messages, system=system_prompt,
        )

        if _web._db:
            try:
                user = _web._auth_mod.get_current_user(request) if _web._auth_mod else None
                user_query = messages[-1].get("content", "") if messages else ""
                doc_id = body.get("document_id", "") or ""

                # Fallback: derive doc_id from the Referer URL when the client
                # didn't send it (old cached JS from before f0476dc, etc.).
                if not doc_id:
                    referer = request.headers.get("referer", "")
                    import re
                    m = re.search(r"/lesson/([0-9a-fA-F-]{36})", referer)
                    if m:
                        doc_id = m.group(1)

                _web._db.log_lesson_chat(doc_id, user.get("user_id") if user else None,
                                         user_query, response_text)
            except Exception as log_err:
                import traceback
                print(f"[lesson-chat] log_lesson_chat FAILED: {type(log_err).__name__}: {log_err}")
                traceback.print_exc()

        return JSONResponse({"response": response_text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
