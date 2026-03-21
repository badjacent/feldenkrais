"""Search routes: search API, lesson page, lesson chat, lesson text/concepts."""

import asyncio
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

import web as _web

router = APIRouter()


@router.post("/api/search")
async def api_search(request: Request, query: str = Form(""), method: str = Form("default")):
    """Semantic search over lesson embeddings. Supports multiple search methods."""
    query = query.strip()
    if not query:
        return JSONResponse({"error": "Empty query"}, status_code=400)
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    if method not in ("default", "concepts", "tags", "combined"):
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


@router.get("/lesson/{document_id}", response_class=HTMLResponse)
async def lesson_detail_page(request: Request, document_id: str):
    """Unified lesson detail page."""
    user = _web._set_source_access(request)
    if _web._db and not _web._db.is_document_accessible(document_id):
        return HTMLResponse("<h1>Not Found</h1>", status_code=404)
    return HTMLResponse(_web.build_lesson_page_html(document_id, user=user, search_mode=_web._is_search_site(request), superuser_active=_web._is_superuser_active(request, user)))


@router.get("/api/lesson/{document_id}/concepts")
async def api_lesson_concepts(document_id: str):
    """Get concept extraction data for a lesson."""
    if not _web._db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    try:
        concepts = _web._db.get_lesson_concepts(document_id)
        text = _web._db.get_document_transcription(document_id)
        extra = {}
        if text:
            from cache_helpers import get_cache_key
            cache_key = get_cache_key(text.strip() + ":concept_tags_v2")
            cached = _web._db.get_cache(cache_key)
            if cached:
                extra["distinctive_summary"] = cached.get("distinctive_summary", "")
                extra["search_terms"] = cached.get("search_terms", [])
        return JSONResponse({"concepts": concepts, **extra})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/lesson/{category}/{filename}")
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

        return JSONResponse({"response": response_text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
