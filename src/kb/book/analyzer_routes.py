"""Analyzer route handlers: /analyzer, /analyze, /api/analyze, /api/status, /api/lesson."""

import asyncio
import uuid

from fastapi import Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from book.state import (
    get_available_lessons, load_lesson, load_whisper_segments,
    align_pass0_with_whisper, render_debug_log,
    save_job, load_job, invoke_lambda_async,
    ANALYSIS_TIMEOUT, IS_LAMBDA, KB_BUCKET,
)
from book.analysis import (
    load_pass_b, load_protocol, get_active_frameworks,
    _run_analysis,
)
from book.analyzer_html import (
    render_router_result, render_multi_vl_analyses, render_analysis,
    build_html, build_result_html,
)


async def home():
    """Show the input form."""
    return build_html()


async def get_lesson_text(category: str, filename: str):
    """Return lesson text for populating the textarea."""
    text = load_lesson(category, filename)
    if text:
        return JSONResponse({"text": text})
    return JSONResponse({"error": "Lesson not found"}, status_code=404)


async def analyze_redirect():
    """Redirect GET requests to home."""
    return RedirectResponse(url="/analyzer", status_code=303)


def _run_analysis(text: str, split: bool = False, pass_a_provider: str | None = None, pass_b_provider: str | None = None, framework_version: str | None = None) -> dict:
    """Run LLM analysis (blocking). Returns dict with pass_a_result, vl_analyses, timings, and debug_log."""
    import time

    fv = framework_version
    # Include split mode, providers, and framework version in cache key
    cache_suffix = _framework_version_suffix(fv)
    if split:
        cache_suffix += ":split"
    if pass_a_provider and pass_a_provider != DEFAULT_LLM:
        cache_suffix += f":a={pass_a_provider}"
    if pass_b_provider and pass_b_provider != DEFAULT_LLM:
        cache_suffix += f":b={pass_b_provider}"
    cache_key = get_cache_key(text + cache_suffix)
    cached = load_cached_analysis(cache_key)

    if cached:
        cached["timings"] = {"cached": True}
        return cached

    timings = {}
    debug_log = []

    t0 = time.time()
    pass_a_result = run_pass_a(text, provider_key=pass_a_provider, debug_log=debug_log, framework_version=fv)
    timings["pass_a"] = round(time.time() - t0, 1)

    active_frameworks = get_active_frameworks(pass_a_result)
    if not active_frameworks:
        primary = pass_a_result.get("primary_framework", "VL-1")
        active_frameworks = [primary]

    vl_analyses = {}
    for framework in active_frameworks:
        t1 = time.time()
        try:
            analysis = run_pass_b(text, pass_a_result, framework, split=split, provider_key=pass_b_provider, debug_log=debug_log, framework_version=fv)
            vl_analyses[framework] = {"status": "complete", "result": analysis}
        except Exception as e:
            vl_analyses[framework] = {"status": "error", "error": str(e)}
        timings[f"pass_b_{framework}"] = round(time.time() - t1, 1)

    timings["total"] = round(time.time() - t0, 1)

    result = {"pass_a_result": pass_a_result, "vl_analyses": vl_analyses}
    save_cached_analysis(cache_key, result)
    result["timings"] = timings
    result["debug_log"] = debug_log
    return result


# JSON API endpoints for AJAX polling
from fastapi.responses import JSONResponse


async def api_analyze(
    lesson_text: str = Form(""),
    selected_category: str = Form(""),
    selected_lesson: str = Form(""),
    split_pass_b: str = Form(""),
    pass_a_provider: str = Form(""),
    pass_b_provider: str = Form(""),
    framework_version: str = Form(""),
):
    """Start analysis and return job_id for polling."""
    # Use textarea content (which may have been populated from a selected lesson)
    text = lesson_text.strip()
    if not text:
        return JSONResponse({"error": "Please enter or select lesson text."}, status_code=400)

    split = bool(split_pass_b)
    pa_provider = pass_a_provider if pass_a_provider else None
    pb_provider = pass_b_provider if pass_b_provider else None
    fv = framework_version if framework_version else None

    # In Lambda with S3 configured, use async pattern
    if IS_LAMBDA and KB_BUCKET:
        job_id = str(uuid.uuid4())

        # Save job to S3
        job_data = {
            "status": "pending",
            "lesson_text": text,
            "selected_category": selected_category,
            "selected_lesson": selected_lesson,
            "split_pass_b": split,
            "pass_a_provider": pa_provider,
            "pass_b_provider": pb_provider,
            "framework_version": fv,
        }
        save_job(job_id, job_data)

        # Invoke Lambda asynchronously to process
        invoke_lambda_async(job_id)

        return JSONResponse({"job_id": job_id, "status": "pending"})

    # Local development: run in thread with timeout
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_analysis, text, split, pa_provider, pb_provider, fv),
            timeout=ANALYSIS_TIMEOUT,
        )
        pass_a_result = result["pass_a_result"]
        vl_analyses = result["vl_analyses"]

        # Align with whisper timestamps (always, even on cache hit)
        whisper_segments = load_whisper_segments(selected_category, selected_lesson)
        if whisper_segments:
            align_pass0_with_whisper(pass_a_result.get("pass_0", {}), whisper_segments)

        # Render
        router_html = render_router_result(pass_a_result)
        _, schema = load_pass_b("VL-1")
        analysis_html = render_multi_vl_analyses(vl_analyses, schema, pass_a_result)
        debug_html = render_debug_log(result.get("debug_log", []))

        return JSONResponse({
            "status": "complete",
            "result_html": router_html + analysis_html + debug_html,
            "timings": result.get("timings"),
        })

    except asyncio.TimeoutError:
        return JSONResponse(
            {"error": "Analysis timed out. Please try again."},
            status_code=504,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_status(job_id: str):
    """Check job status and return result if complete."""
    if not IS_LAMBDA or not KB_BUCKET:
        return JSONResponse({"error": "Not available in local mode"}, status_code=400)

    job = load_job(job_id)
    if job is None:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    status = job.get("status", "unknown")

    if status == "pending":
        return JSONResponse({"status": "pending"})
    elif status == "error":
        return JSONResponse({"status": "error", "error": job.get("error", "Unknown error")})
    else:  # complete
        _, schema = load_protocol()
        router_result = job.get("router_result", {})
        vl_analyses = job.get("vl_analyses", {})

        router_html = render_router_result(router_result)

        if vl_analyses:
            analysis_html = render_multi_vl_analyses(vl_analyses, schema)
        elif job.get("result"):
            analysis_html = render_analysis(job["result"], schema)
        else:
            analysis_html = ""

        return JSONResponse({"status": "complete", "result_html": router_html + analysis_html})


# Legacy form POST (redirects for non-JS browsers)
async def analyze(
    lesson_text: str = Form(""),
    selected_category: str = Form(""),
    selected_lesson: str = Form(""),
    framework_version: str = Form(""),
):
    """Fallback for non-JavaScript form submission."""
    # Use textarea content
    text = lesson_text.strip()
    fv = framework_version if framework_version else None
    if not text:
        return build_html(
            lesson_text=lesson_text,
            selected_category=selected_category,
            result='<div class="error">Please enter or select lesson text.</div>'
        )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_analysis, text, False, None, None, fv),
            timeout=ANALYSIS_TIMEOUT,
        )
        pass_a_result = result["pass_a_result"]
        vl_analyses = result["vl_analyses"]

        # Align with whisper timestamps (always, even on cache hit)
        whisper_segments = load_whisper_segments(selected_category, selected_lesson)
        if whisper_segments:
            align_pass0_with_whisper(pass_a_result.get("pass_0", {}), whisper_segments)

        # Render
        router_html = render_router_result(pass_a_result)
        _, schema = load_pass_b("VL-1")
        analysis_html = render_multi_vl_analyses(vl_analyses, schema, pass_a_result)
        result_html = f'<div class="result">{router_html}{analysis_html}</div>'

    except asyncio.TimeoutError:
        result_html = '<div class="error">Analysis timed out. Please try again.</div>'
    except Exception as e:
        result_html = f'<div class="error">Error: {str(e)}</div>'

    return build_html(
        lesson_text=lesson_text if not selected_lesson else "",
        selected_category=selected_category,
        selected_lesson=selected_lesson,
        result=result_html
    )

