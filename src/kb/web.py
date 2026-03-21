"""
Simple web interface for Feldenkrais lesson analysis.

Async architecture to handle long LLM calls:
1. POST /analyze -> Create job in S3, invoke Lambda async, redirect to /result/{job_id}
2. GET /result/{job_id} -> Check S3 for result, auto-refresh while pending
3. Async Lambda invocation processes job and writes result to S3

Usage (local):
    cd src/kb
    uvicorn web:app --reload
"""

import asyncio
from datetime import datetime, timezone
import html as html_mod
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from llm import LLM
from storage import Storage

# ── Subdomain detection ───────────────────────────────────────────────
SEARCH_DOMAIN = os.environ.get("SEARCH_DOMAIN", "")
FINDER_DOMAIN = os.environ.get("FINDER_DOMAIN", "")
INTERIORITY_DOMAIN = os.environ.get("INTERIORITY_DOMAIN", "")


def _is_search_site(request: Request) -> bool:
    """True when served from the search subdomain."""
    if not SEARCH_DOMAIN:
        return False
    host = request.headers.get("host", "")
    return host == SEARCH_DOMAIN or host.startswith(SEARCH_DOMAIN + ":")


def _is_finder_site(request: Request) -> bool:
    """True when served from the finder subdomain."""
    if not FINDER_DOMAIN:
        return False
    host = request.headers.get("host", "")
    return host == FINDER_DOMAIN or host.startswith(FINDER_DOMAIN + ":")


def _is_interiority_site(request: Request) -> bool:
    """True when served from the interiority subdomain."""
    if not INTERIORITY_DOMAIN:
        return False
    host = request.headers.get("host", "")
    return host == INTERIORITY_DOMAIN or host.startswith(INTERIORITY_DOMAIN + ":")


# ── Source access helpers ─────────────────────────────────────────────

SUPERUSER_COOKIE = "fk_superuser"


def _is_superuser_active(request: Request, user: dict | None) -> bool:
    """True if the user is superuser-eligible AND has the superuser cookie set."""
    if not user or not user.get("superuser_eligible"):
        return False
    return request.cookies.get(SUPERUSER_COOKIE) == "1"


def _set_source_access(request: Request) -> dict | None:
    """Compute allowed_source_ids on _db based on the current user/request.

    Returns the user dict (or None for anonymous).
    """
    if not _db:
        return None
    user = _auth_mod.get_current_user(request)
    if user and user.get("tier") == "admin":
        _db.allowed_source_ids = None
    elif user and _is_superuser_active(request, user):
        _db.allowed_source_ids = None
    elif user:
        _db.allowed_source_ids = _db.get_user_allowed_sources(user["user_id"])
    else:
        _db.allowed_source_ids = _db.get_user_allowed_sources(None)
    return user


# ── Pipeline lock (DB-based, 5-minute expiry) ───────────────────────────

PIPELINE_LOCK_KEY = "pipeline:global_lock"
PIPELINE_LOCK_TTL_SECONDS = 300  # 5 minutes


def acquire_pipeline_lock(reason: str = "") -> bool:
    """Acquire the global pipeline lock. Returns True if acquired."""
    import time
    if not _db:
        return False
    now = time.time()
    existing = _db.get_cache(PIPELINE_LOCK_KEY)
    if existing and (now - existing.get("acquired_at", 0)) < PIPELINE_LOCK_TTL_SECONDS:
        return False
    _db.set_cache(PIPELINE_LOCK_KEY, {"acquired_at": now, "reason": reason})
    print(f"[pipeline] lock acquired: {reason}", flush=True)
    return True


def release_pipeline_lock():
    """Release the global pipeline lock."""
    if not _db:
        return
    with _db.get_cursor() as cursor:
        cursor.execute("DELETE FROM cache WHERE key = %s", (PIPELINE_LOCK_KEY,))
    print("[pipeline] lock released", flush=True)


def _is_pipeline_locked() -> bool:
    """Check if the pipeline lock is held (and not expired)."""
    import time
    if not _db:
        return False
    existing = _db.get_cache(PIPELINE_LOCK_KEY)
    if not existing:
        return False
    age = time.time() - existing.get("acquired_at", 0)
    if age >= PIPELINE_LOCK_TTL_SECONDS:
        print(f"[pipeline] lock expired ({age:.0f}s), releasing", flush=True)
        release_pipeline_lock()
        return False
    return True


# ── Pipeline runner (on-demand only, no background loop) ─────────────────

_pipeline_run_lock = asyncio.Lock()


def _run_pipeline_for_lesson(lesson_id: str):
    """Run all stale nodes for a single lesson (synchronous, called from thread)."""
    if _is_pipeline_locked():
        print(f"[pipeline] skipping run for {lesson_id[:8]}... — global lock held")
        return

    from pipeline import PipelineStateStore, PipelineExecutor
    from pipeline_graph import build_lesson_pipeline, get_book_lessons

    # Find title from registry
    lessons = get_book_lessons(_db)
    title = "Unknown Lesson"
    for entry in lessons:
        if entry["lesson_id"] == lesson_id:
            title = entry["title"]
            break

    graph = build_lesson_pipeline(_db, lesson_id, title)
    store = PipelineStateStore(_db)
    store.initialize()
    executor = PipelineExecutor(graph, store)
    results = executor.run_stale(total_timeout_seconds=900)
    if isinstance(results, list) and results:
        fresh = sum(1 for r in results if r.status.value == "fresh")
        errors = sum(1 for r in results if r.status.value == "error")
        print(f"[pipeline] lesson {lesson_id[:8]}... complete: {fresh} succeeded, {errors} errors out of {len(results)} nodes")


async def _periodic_group_sync():
    """Periodically sync auto-groups so newly transcribed lessons enter the analysis pipeline."""
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        try:
            _db.sync_auto_groups()
        except Exception as e:
            print(f"[sync] auto-group sync error: {e}")


@asynccontextmanager
async def _lifespan(app):
    if _db and not IS_LAMBDA:
        # Clear zombie RUNNING states from previous server run
        with _db.get_cursor() as cur:
            cur.execute(
                "UPDATE pipeline_node_state SET status = 'stale' WHERE status = 'running'"
            )
        print("[pipeline] on-demand mode (no background runner)")
    sync_task = None
    if _db and not IS_LAMBDA:
        sync_task = asyncio.create_task(_periodic_group_sync())
        print("[sync] auto-group sync running every 5 minutes")
    try:
        yield
    finally:
        if sync_task:
            sync_task.cancel()


app = FastAPI(title="Feldenkrais Lesson Analyzer", lifespan=_lifespan)


def _login_page_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
    <title>Sign In — Feldenkrais Lesson Search</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex; justify-content: center; align-items: center;
            min-height: 100vh; margin: 0; background: #fafafa; color: #333;
        }
        .login-card {
            text-align: center; padding: 48px 40px;
            background: #fff; border: 1px solid #e0e0e0; border-radius: 12px;
            max-width: 380px; width: 100%;
        }
        h1 { font-size: 1.4em; margin: 0 0 8px; }
        .subtitle { color: #666; font-size: 0.9em; margin-bottom: 32px; }
        .btn {
            display: block; width: 100%; padding: 12px; margin-bottom: 12px;
            font-size: 15px; font-weight: 600; border-radius: 6px;
            text-decoration: none; text-align: center; cursor: pointer;
        }
        .btn-github {
            background: #24292e; color: #fff; border: none;
        }
        .btn-github:hover { background: #1a1e22; }
        .btn-google {
            background: #fff; color: #333; border: 1px solid #dadce0;
        }
        .btn-google:hover { background: #f8f9fa; }
    </style>
</head>
<body>
    <div class="login-card">
        <h1>Feldenkrais Lesson Search</h1>
        <p class="subtitle">Sign in to continue</p>
        <a class="btn btn-github" href="/auth/github/login">Sign in with GitHub</a>
        <a class="btn btn-google" href="/auth/google/login">Sign in with Google</a>
    </div>
</body>
</html>"""


@app.middleware("http")
async def search_site_guard(request: Request, call_next):
    """Auth gate + search subdomain path restrictions."""
    path = request.url.path

    # Auth routes are always accessible
    if path.startswith("/auth/"):
        return await call_next(request)

    # Finder subdomain is public — no auth required
    if _is_finder_site(request):
        allowed = (
            path == "/"
            or path.startswith("/api/finder")
        )
        if not allowed:
            return RedirectResponse("/")
        return await call_next(request)

    # Interiority subdomain is public — serves research booklet
    if _is_interiority_site(request):
        if path != "/":
            return RedirectResponse("/")
        return await call_next(request)

    # On Lambda, require login for all pages
    if IS_LAMBDA and not _auth_mod.get_current_user(request):
        return HTMLResponse(_login_page_html())

    # On search subdomain, restrict to allowed paths
    if _is_search_site(request):
        allowed = (
            path == "/"
            or path.startswith("/lesson/")
            or path.startswith("/api/")
            or path.startswith("/docs/")
        )
        if not allowed:
            return RedirectResponse("/")

    return await call_next(request)


# ── Auth routes ──────────────────────────────────────────────────────

@app.get("/auth/github/login")
async def auth_github_login(request: Request):
    return _auth_mod.github_login_redirect(request)


@app.get("/auth/github/callback")
async def auth_github_callback(request: Request):
    return _auth_mod.github_oauth_callback(request, _db)


@app.get("/auth/google/login")
async def auth_google_login(request: Request):
    return _auth_mod.google_login_redirect(request)


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    return _auth_mod.google_oauth_callback(request, _db)


@app.get("/auth/logout")
async def auth_logout(request: Request):
    return _auth_mod.logout(request)


@app.get("/auth/superuser/on")
async def superuser_on(request: Request):
    """Enable superuser mode (for eligible users only)."""
    user = _auth_mod.get_current_user(request)
    if not user or not user.get("superuser_eligible"):
        return RedirectResponse("/")
    response = RedirectResponse("/")
    response.set_cookie(
        SUPERUSER_COOKIE, "1",
        max_age=30 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=_auth_mod._is_secure(request),
    )
    return response


@app.get("/auth/superuser/off")
async def superuser_off(request: Request):
    """Disable superuser mode."""
    response = RedirectResponse("/")
    response.delete_cookie(SUPERUSER_COOKIE)
    return response


# Paths - handle both local development and Lambda environment
IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
if IS_LAMBDA:
    # In Lambda, data files are packaged alongside the code
    DATA_DIR = Path(__file__).parent / "data"
else:
    # Local development
    DATA_DIR = Path(__file__).parent.parent.parent / "data"
LESSONS_DIR = DATA_DIR / "lessons"


# ── Extracted modules ──────────────────────────────────────────────────────
# Analysis engine, LLM config, and cache helpers are extracted into separate
# modules for independent importability. They are initialized after _db setup
# below. Placeholder globals until init:
PROMPTS_DIR = None
FRAMEWORK_VERSION = None



# Database connection (optional - for loading lessons from DB)
_db = None
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    from database import Database
    _db = Database(DATABASE_URL)
    _db.initialize()

# Pipeline runner state — uses DB cache key for cross-process coordination
import subprocess
import threading

_PIPELINE_KEY = "pipeline:analysis_running"
_PIPELINE_ACTIVE_KEY = "pipeline:active_steps"
_PIPELINE_PYTHON = str(Path(__file__).parent.parent.parent / ".venv" / "bin" / "python")
_PIPELINE_MODULE = "pipeline.analysis"
_PIPELINE_CWD = str(Path(__file__).parent)

def _pipeline_is_running() -> bool:
    """Check if pipeline is marked as running in DB."""
    if not _db:
        return False
    row = _db.get_cache(_PIPELINE_KEY)
    return bool(row and row.get("running"))

def _pipeline_set_running(running: bool):
    """Set pipeline running state in DB."""
    if _db:
        _db.set_cache(_PIPELINE_KEY, {"running": running})

def _pipeline_update_active(active: list[dict]):
    """Update the active steps list in DB. Preserves start times for ongoing steps."""
    import datetime
    # Merge with existing to preserve start times
    existing = (_db.get_cache(_PIPELINE_ACTIVE_KEY) or {}).get("steps", [])
    existing_lookup = {(s["step"], s["title"]): s.get("started") for s in existing}
    now = datetime.datetime.utcnow().isoformat()
    for s in active:
        s["started"] = existing_lookup.get((s["step"], s["title"]), now)
    _db.set_cache(_PIPELINE_ACTIVE_KEY, {"steps": active})

def _run_step_subprocess(doc_id: str, step_name: str, title: str, timeout: int = 300) -> dict:
    """Run a single step in a subprocess. Returns result dict."""
    try:
        result = subprocess.run(
            [_PIPELINE_PYTHON, "-m", _PIPELINE_MODULE, "run-one", doc_id, step_name],
            capture_output=True, text=True, timeout=timeout,
            cwd=_PIPELINE_CWD,
        )
        if result.returncode == 0:
            return {"ok": True, "doc_id": doc_id, "step": step_name, "title": title}
        else:
            return {"ok": False, "doc_id": doc_id, "step": step_name, "title": title,
                    "error": result.stderr[:200] if result.stderr else "exit code " + str(result.returncode)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "doc_id": doc_id, "step": step_name, "title": title,
                "error": f"Timed out after {timeout}s"}

def _pipeline_worker():
    """Background pipeline worker using subprocesses.

    Each step runs in its own process with a hard timeout.
    Up to 4 steps run concurrently via a thread pool that manages subprocesses.
    Each pass gets a fresh pool so timed-out threads don't block future work.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    max_workers = 4
    step_timeout = 300  # 5 minutes per step

    try:
        while _pipeline_is_running():
            runnable = _db.get_runnable_steps()
            if not runnable:
                break

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                pending = {}  # future -> info dict
                idx = 0

                # Seed initial batch
                while idx < len(runnable) and len(pending) < max_workers:
                    if not _pipeline_is_running():
                        break
                    r = runnable[idx]
                    idx += 1
                    doc_id = str(r["document_id"])
                    step = r["step_name"]
                    title = r["document_title"][:50]
                    f = pool.submit(_run_step_subprocess, doc_id, step, title, step_timeout)
                    pending[f] = {"step": step, "title": title, "doc_id": doc_id}

                _pipeline_update_active(list(pending.values()))

                # Process completions
                while pending:
                    if not _pipeline_is_running():
                        break
                    try:
                        done = next(as_completed(pending, timeout=step_timeout + 30))
                    except (StopIteration, TimeoutError):
                        # Pool-level timeout — mark remaining as errors
                        for f, info in list(pending.items()):
                            if not f.done():
                                _db.mark_step_error(info["doc_id"], info["step"],
                                                    f"Timed out after {step_timeout}s")
                        break
                    info = pending.pop(done)
                    try:
                        result = done.result()
                        if not result["ok"] and "Timed out" in result.get("error", ""):
                            _db.mark_step_error(result["doc_id"], result["step"], result["error"])
                    except Exception:
                        pass

                    # Feed next item
                    if idx < len(runnable) and _pipeline_is_running():
                        r = runnable[idx]
                        idx += 1
                        doc_id = str(r["document_id"])
                        step = r["step_name"]
                        title = r["document_title"][:50]
                        f = pool.submit(_run_step_subprocess, doc_id, step, title, step_timeout)
                        pending[f] = {"step": step, "title": title, "doc_id": doc_id}

                    _pipeline_update_active(list(pending.values()))
            # Pool closes here — any hung threads get abandoned, subprocesses get killed
    finally:
        _pipeline_update_active([])
        _pipeline_set_running(False)

# On startup, if pipeline was running, restart the worker thread
if _db:
    _pipeline_update_active([])
    if _pipeline_is_running():
        _t = threading.Thread(target=_pipeline_worker, daemon=True)
        _t.start()

# ── Initialize extracted modules ──────────────────────────────────────────
import auth as _auth_mod
import cache_helpers
import llm_config
import analysis as _analysis_mod

cache_helpers.init(_db)
llm_config.init(_db)
_analysis_mod.init(_db, DATA_DIR, llm_config.get_llm)

# Re-export names so existing web.py code continues to work unchanged
from cache_helpers import get_cache_key, load_cached_analysis, save_cached_analysis, build_summary_from_condensed  # noqa: E402
from llm_config import LLM_CONFIGS, DEFAULT_LLM, get_llm  # noqa: E402
from analysis import (  # noqa: E402
    run_pass_0, format_text_with_segments,
    load_pass_a, run_pass_a, _pass_a_cache_key,
    run_pass_0b, run_pass_0b_lang, run_pass_0b_word_lang,
    run_pass_0b_condensed, run_segmentation_query,
    load_pass_0b, load_pass_0b_lang, load_pass_0b_word_lang,
    load_pass_0b_condensed, load_pass_0b_segmentation,
    format_steps_for_0b, format_steps_for_word_lang,
    format_input_for_segmentation, format_input_for_0b_condensed,
    _run_condensed_for_groups,
    load_frameworks, get_active_frameworks_config, _derive_key,
    build_classification_table, build_pass_a_schema,
    _resolve_prompts_dir, _framework_version_suffix,
    ROUTER_SYSTEM_PROMPT,
)

# Set framework version globals from the initialized analysis module
FRAMEWORK_VERSION = _analysis_mod.FRAMEWORK_VERSION
PROMPTS_DIR = _analysis_mod.PROMPTS_DIR

# ── Page builders (extracted to web_pages.py) ─────────────────────────────
from web_pages import (  # noqa: E402
    _AUTH_BAR_CSS, _auth_bar_html,
    build_pass0b_html, build_search_home_html,
    build_lesson_page_html, build_pass0b_player_html,
    build_pass0b_lang_html,
)


# S3 configuration for async jobs
KB_BUCKET = os.environ.get("KB_BUCKET")
LAMBDA_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME_SELF") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")


# ── Pass 0/A/0b functions extracted to analysis.py ────────────────────────
# run_pass_0, format_text_with_segments, load_pass_a, run_pass_a,
# run_pass_0b, run_pass_0b_lang, run_pass_0b_word_lang,
# run_pass_0b_condensed, run_segmentation_query, etc.
# are imported above from the analysis module.
# ──────────────────────────────────────────────────────────────────────────


def format_timestamp(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    if seconds is None:
        return ""
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def align_pass0_with_whisper(pass_0_result: dict, whisper_segments: list) -> None:
    """Align Pass 0 segments with Whisper timestamps.

    Mutates pass_0_result in place, adding time_start and time_end
    (in seconds) to each segment that can be matched.

    Algorithm:
    1. Concatenate all whisper segment texts, tracking which character
       positions belong to which whisper segment.
    2. For each Pass 0 segment, search for its text in the whisper
       concatenation using a prefix-based approach.
    3. Map matched positions to whisper segment indices and read timestamps.
    """
    import re

    if not whisper_segments or not pass_0_result.get("segments"):
        return

    # Build whisper concatenation and character-to-segment-index map
    whisper_text = ""
    char_to_seg_idx = []

    for i, ws in enumerate(whisper_segments):
        text = ws.get("text", "")
        char_to_seg_idx.extend([i] * len(text))
        whisper_text += text

    if not whisper_text or not char_to_seg_idx:
        return

    whisper_lower = whisper_text.lower()
    search_from = 0

    for seg in pass_0_result["segments"]:
        seg_text = seg.get("text", "").strip()
        if not seg_text or len(seg_text) < 5:
            continue

        # Search for start of segment text in whisper (case-insensitive)
        key_len = min(60, len(seg_text))
        key = seg_text[:key_len].lower()
        # Allow some backtrack to handle slight ordering differences
        pos = whisper_lower.find(key, max(0, search_from - 200))

        if pos < 0:
            # Try shorter key
            key = seg_text[:30].lower()
            pos = whisper_lower.find(key, max(0, search_from - 500))

        if pos >= 0:
            # Found start - now find end position
            if len(seg_text) > key_len:
                end_key = seg_text[-key_len:].lower()
                end_pos = whisper_lower.find(end_key, pos)
                if end_pos >= 0:
                    end_pos += len(end_key)
                else:
                    end_pos = min(pos + len(seg_text), len(whisper_text))
            else:
                end_pos = pos + len(key)

            # Clamp to valid range
            pos = min(pos, len(char_to_seg_idx) - 1)
            end_pos = min(max(end_pos - 1, pos), len(char_to_seg_idx) - 1)

            # Get whisper segment timestamps
            start_ws_idx = char_to_seg_idx[pos]
            end_ws_idx = char_to_seg_idx[end_pos]

            seg["time_start"] = whisper_segments[start_ws_idx]["start"]
            seg["time_end"] = whisper_segments[end_ws_idx]["end"]

            search_from = end_pos



def get_available_lessons() -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    """Get categories and their display names.

    DB documents with category_name set are grouped under that name (override).
    DB documents without category_name are grouped under their source's category.

    Uses _db.allowed_source_ids (set by _set_source_access) to filter sources.

    Returns (categories_dict, display_names_dict).
    - categories: category_key -> [(lesson_key, display_name)]
    - display_names: category_key -> display_name
    All DB keys start with 'db:'.
    """
    categories = {}
    display_names = {}

    # 1. Database sources (if available)
    if _db:
        try:
            for src in _db.get_lesson_categories():
                source_display = (
                    src.get("source_category_name")
                    or src["source_name"].replace("_", " ").title()
                )
                # Group documents: those with category_name override go to their own bucket
                override_buckets = {}  # category_name -> list of (id, title)
                source_bucket = []     # docs without override

                for doc in _db.get_source_lessons(src["source_id"]):
                    # Only show ATM lessons for FELDENKRAIS_MANHATTAN
                    lesson_type = doc.get("lesson_type")
                    if src["source_id"] == 3 and lesson_type and lesson_type != "ATM":
                        continue
                    title = doc["title"]
                    instructor = doc.get("instructor")
                    display = f"{title} ({instructor})" if instructor else title
                    source_url = doc.get("source_url") or ""
                    doc_cat = doc.get("category_name")
                    if doc_cat:
                        override_buckets.setdefault(doc_cat, []).append(
                            (str(doc["id"]), display, source_url)
                        )
                    else:
                        source_bucket.append((str(doc["id"]), display, source_url))

                # Add override buckets as separate categories
                for cat_name, lessons in override_buckets.items():
                    # Key encodes source + override name for uniqueness
                    cat_key = f"db:{src['source_id']}:{cat_name}"
                    categories[cat_key] = lessons
                    display_names[cat_key] = cat_name

                # Add remaining docs under the source category
                if source_bucket:
                    cat_key = f"db:{src['source_id']}"
                    categories[cat_key] = source_bucket
                    display_names[cat_key] = source_display
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("get_available_lessons DB error: %s", e)

    # Skip empty sources (e.g. OTHER with no documents)
    categories = {k: v for k, v in categories.items() if v}

    # Sort lessons within each category alphabetically by display name
    for key in categories:
        categories[key].sort(key=lambda x: x[1].lower())

    # Sort categories alphabetically by display name
    sorted_keys = sorted(categories.keys(), key=lambda k: display_names.get(k, k).lower())
    categories = {k: categories[k] for k in sorted_keys}

    return categories, display_names


def load_lesson(category: str, lesson_key: str) -> str:
    """Load lesson text from DB or filesystem."""
    # DB source (handles both 'db:4' and 'db:4:Category Name')
    if category.startswith("db:") and _db:
        text = _db.get_document_transcription(lesson_key)
        return text or ""

    # Filesystem source
    if category and category != "uncategorized":
        path = LESSONS_DIR / category / lesson_key
    else:
        path = LESSONS_DIR / lesson_key
    if path.exists() and path.is_file():
        return path.read_text()
    return ""


def load_lesson_metadata(category: str, lesson_key: str) -> dict:
    """Load lesson metadata (title, source_url) from DB."""
    if category.startswith("db:") and _db:
        doc = _db.get_document(lesson_key)
        if doc:
            return {
                "title": doc.get("title"),
                "source_url": doc.get("source_url"),
            }
    return {}


def load_whisper_segments(category: str, lesson_key: str) -> list | None:
    """Load Whisper timestamp segments from DB or sidecar JSON file.

    Returns the list of whisper segments or None if not available.
    """
    if not lesson_key:
        return None

    # DB source: segments are in the transcription analysis
    if category.startswith("db:") and _db:
        return _db.get_document_whisper_segments(lesson_key)

    # Filesystem source: look for .whisper.json sidecar
    whisper_name = lesson_key.replace(".txt", ".whisper.json")
    if category and category != "uncategorized":
        path = LESSONS_DIR / category / whisper_name
    else:
        path = LESSONS_DIR / whisper_name
    if path.exists():
        return json.loads(path.read_text())
    return None


# S3 helper functions for async job management
def get_s3_client():
    """Get S3 client (only available in Lambda)."""
    import boto3
    return boto3.client("s3")


def save_job(job_id: str, data: dict) -> None:
    """Save job data to S3."""
    s3 = get_s3_client()
    s3.put_object(
        Bucket=KB_BUCKET,
        Key=f"web-jobs/{job_id}.json",
        Body=json.dumps(data),
        ContentType="application/json"
    )


def load_job(job_id: str) -> dict | None:
    """Load job data from S3."""
    s3 = get_s3_client()
    try:
        response = s3.get_object(Bucket=KB_BUCKET, Key=f"web-jobs/{job_id}.json")
        return json.loads(response["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return None
    except Exception:
        return None


def invoke_lambda_async(job_id: str) -> None:
    """Invoke this Lambda asynchronously to process a job."""
    import boto3
    client = boto3.client("lambda")
    client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType="Event",  # Async invocation
        Payload=json.dumps({"job_id": job_id, "action": "process"})
    )


def render_debug_log(debug_log: list) -> str:
    """Render raw LLM input/output debug log as a flat tabbed UI.

    Self-contained: embeds its own JS and uses unique IDs so it works on any
    page and can be rendered multiple times without collisions.
    """
    if not debug_log:
        return ""

    import html as html_module
    import random
    import string

    # Filter out cached-only entries that have no useful content
    entries = [
        e for e in debug_log
        if e.get("prompt") or e.get("output") or e.get("error")
    ]
    if not entries:
        return ""

    # Unique prefix to avoid ID collisions
    uid = "dbg_" + "".join(random.choices(string.ascii_lowercase, k=6))

    pre_style = (
        "background: {bg}; padding: 12px; border-radius: 6px; "
        "white-space: pre-wrap; word-wrap: break-word; "
        "max-height: 600px; overflow-y: auto; font-size: 0.8em; margin: 0;"
    )

    parts = [
        f'<div id="{uid}" style="margin-top: 30px; border-top: 2px solid #ddd; padding-top: 15px;">',
        f'<h2 style="cursor:pointer;" '
        f"""onclick="var c=document.getElementById('{uid}_body');c.style.display=c.style.display==='none'?'block':'none';">"""
        'Raw I/O <span style="font-size:0.7em;color:#999;">(click to toggle)</span></h2>',
        f'<div id="{uid}_body" style="display:none;">',
    ]

    # --- Tab bar ---
    parts.append('<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:12px;">')
    for i, entry in enumerate(entries):
        name = entry.get("name", f"Query {i+1}")
        provider = entry.get("provider", "")
        has_error = "error" in entry
        label = f'{name} [{provider}]' if provider else name
        if has_error:
            label += " (err)"
        active_bg = "#007bff" if i == 0 else "#e9ecef"
        active_fg = "white" if i == 0 else ("#dc3545" if has_error else "#6c757d")
        parts.append(
            f'<button id="{uid}_tab{i}" onclick="{uid}_show({i})" '
            f'style="padding:6px 12px;border:1px solid #ccc;cursor:pointer;'
            f'background:{active_bg};color:{active_fg};border-radius:4px;font-size:0.85em;">'
            f'{label}</button>'
        )
    parts.append('</div>')

    # --- Tab panels ---
    for i, entry in enumerate(entries):
        display = "block" if i == 0 else "none"
        parts.append(f'<div id="{uid}_panel{i}" style="display:{display};">')

        # Input section
        prompt = entry.get("prompt", "")
        system = entry.get("system", "")
        schema = entry.get("schema")
        if prompt or system:
            parts.append('<div style="margin-bottom:12px;">')
            parts.append('<strong style="color:#555;">Input</strong>')
            if system:
                parts.append(f'<div style="margin-top:6px;font-size:0.8em;color:#888;">System ({len(system):,} chars)</div>')
                parts.append(f'<pre style="{pre_style.format(bg="#f0f8ff")}">{html_module.escape(system)}</pre>')
            if prompt:
                parts.append(f'<div style="margin-top:6px;font-size:0.8em;color:#888;">Prompt ({len(prompt):,} chars)</div>')
                parts.append(f'<pre style="{pre_style.format(bg="#f8f9fa")}">{html_module.escape(prompt)}</pre>')
            if schema:
                schema_str = json.dumps(schema, indent=2)
                parts.append(f'<div style="margin-top:6px;font-size:0.8em;color:#888;">Schema ({len(schema_str):,} chars)</div>')
                parts.append(f'<pre style="{pre_style.format(bg="#fff8f0")}">{html_module.escape(schema_str)}</pre>')
            parts.append('</div>')

        # Output section
        if "error" in entry:
            error_str = entry["error"]
            parts.append('<strong style="color:#dc3545;">Output (ERROR)</strong>')
            parts.append(f'<pre style="{pre_style.format(bg="#fff5f5")}border:1px solid #f5c6cb;">{html_module.escape(error_str)}</pre>')
        elif "output" in entry:
            output_str = json.dumps(entry["output"], indent=2)
            parts.append(f'<strong style="color:#555;">Output ({len(output_str):,} chars)</strong>')
            parts.append(f'<pre style="{pre_style.format(bg="#f0fff0")}">{html_module.escape(output_str)}</pre>')

        parts.append('</div>')

    # --- Inline JS for tab switching ---
    parts.append(f'''<script>
function {uid}_show(idx) {{
  for (var i = 0; i < {len(entries)}; i++) {{
    var p = document.getElementById("{uid}_panel" + i);
    var t = document.getElementById("{uid}_tab" + i);
    if (p) p.style.display = i === idx ? "block" : "none";
    if (t) {{ t.style.background = i === idx ? "#007bff" : "#e9ecef"; t.style.color = i === idx ? "white" : "#6c757d"; }}
  }}
}}
</script>''')

    parts.append('</div></div>')
    return "\n".join(parts)


ANALYSIS_TIMEOUT = 900  # 15 minutes



def _build_ingestion_html() -> str:
    """Build the ingestion tab content HTML (cards + events + documents tables)."""
    summary = _db.get_pipeline_summary()
    events = _db.get_pipeline_log(limit=50)
    documents = _db.get_document_pipeline_view(limit=50)

    # Check for permanent failures
    perm_failures = _db.get_pipeline_log(limit=20, event="permanent_failure")

    # Summary cards
    doc_total = summary.get("documents", {}).get("total", 0)
    transcribed = summary.get("transcribed", 0)
    pending_docs = summary.get("documents", {}).get("pending", 0)
    jobs = summary.get("transcription_jobs", {})
    pending_jobs = jobs.get("pending", 0)
    failed_jobs = jobs.get("failed", 0)

    failed_count = sum(1 for d in documents if d.get("pipeline_stage") == "failed")

    # Events table rows
    event_rows = []
    for ev in events:
        created = str(ev.get("created_at", ""))[:19]
        event_type = ev.get("event", "")
        title = ev.get("title", "") or ""
        doc_id = str(ev.get("document_id", "") or "")[:8]
        detail = ev.get("detail", {}) or {}
        detail_str = json.dumps(detail) if detail else ""
        if len(detail_str) > 120:
            detail_str = detail_str[:120] + "..."

        # Color code event types
        color_map = {
            "loaded": "#28a745",
            "staged": "#17a2b8",
            "batch_created": "#007bff",
            "transcription_complete": "#28a745",
            "transcription_failed": "#dc3545",
            "duplicate_skipped": "#6c757d",
            "load_error": "#dc3545",
            "analysis_complete": "#28a745",
            "analysis_error": "#dc3545",
            "embedding_complete": "#17a2b8",
            "permanent_failure": "#dc3545",
            "load_retry": "#ffc107",
            "transcription_retry": "#ffc107",
            "resubmit": "#6610f2",
            "temp_cleanup": "#6c757d",
        }
        color = color_map.get(event_type, "#333")

        event_rows.append(f"""
            <tr>
                <td style="white-space: nowrap;">{created}</td>
                <td><span style="color: {color}; font-weight: bold;">{event_type}</span></td>
                <td>{title}</td>
                <td style="font-family: monospace; font-size: 0.85em;">{doc_id}</td>
                <td style="font-size: 0.85em; color: #666;">{detail_str}</td>
            </tr>
        """)

    # Documents table rows
    doc_rows = []
    for doc in documents:
        created = str(doc.get("created_at", ""))[:10]
        title = doc.get("title", "")
        if len(title) > 60:
            title = title[:57] + "..."
        category = doc.get("display_category", "")
        has_tx = doc.get("has_transcription", False)
        latest_job = doc.get("latest_job_status", "")
        latest_error = doc.get("latest_error", "")

        stage = doc.get("pipeline_stage", "")
        stage_styles = {
            "transcribed": ("Transcribed", "#d4edda", "#155724"),
            "transcribing": ("Transcribing", "#fff3cd", "#856404"),
            "failed": ("Failed", "#f8d7da", "#721c24"),
            "awaiting_transcription": ("New", "#e2e3e5", "#383d41"),
        }
        label, bg, fg = stage_styles.get(stage, ("Unknown", "#e2e3e5", "#383d41"))
        title_attr = f' title="{latest_error}"' if latest_error and stage == "failed" else ""
        status_badge = f'<span style="padding: 2px 8px; border-radius: 10px; font-size: 0.8em; background: {bg}; color: {fg};"{title_attr}>{label}</span>'

        doc_id = str(doc.get("id", ""))
        can_resubmit = stage in ("failed", "transcribed")
        resubmit_btn = (
            f'<button class="resubmit-btn" data-docid="{doc_id}" '
            f'style="padding: 2px 8px; font-size: 0.8em; border: 1px solid #dc3545; '
            f'background: white; color: #dc3545; border-radius: 4px; cursor: pointer;">'
            f'Resubmit</button>'
        ) if can_resubmit else ""

        doc_rows.append(f"""
            <tr>
                <td>{created}</td>
                <td>{title}</td>
                <td style="font-size: 0.85em;">{category}</td>
                <td>{status_badge}</td>
                <td style="text-align: center;">{doc.get("job_count", 0)}</td>
                <td style="text-align: center;">{resubmit_btn}</td>
            </tr>
        """)

    events_html = "\n".join(event_rows) if event_rows else '<tr><td colspan="5" style="text-align: center; color: #999;">No events yet</td></tr>'
    docs_html = "\n".join(doc_rows) if doc_rows else '<tr><td colspan="6" style="text-align: center; color: #999;">No documents yet</td></tr>'

    # Permanent failure banner
    pf_banner = ""
    if perm_failures:
        pf_items = []
        seen = set()
        for pf in perm_failures:
            title = pf.get("title", "Unknown")
            if title in seen:
                continue
            seen.add(title)
            stage = (pf.get("detail") or {}).get("stage", "unknown")
            created = str(pf.get("created_at", ""))[:19]
            pf_items.append(f"<li><strong>{title}</strong> — {stage} (failed {created})</li>")
        pf_banner = f"""
        <div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 6px; padding: 12px 16px; margin-bottom: 15px; color: #721c24;">
            <strong>Permanent Failures ({len(seen)})</strong> — these documents exceeded retry limits:
            <ul style="margin: 8px 0 0 0; padding-left: 20px;">{"".join(pf_items)}</ul>
        </div>
        """

    return f"""
        {pf_banner}
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 15px;">
            <button id="refresh-ingestion" style="padding: 4px 12px; border: 1px solid #007bff; background: white; color: #007bff; border-radius: 4px; cursor: pointer; font-size: 0.85em;">Refresh</button>
            {f'<button id="retry-all-failed" style="padding: 4px 12px; border: 1px solid #dc3545; background: white; color: #dc3545; border-radius: 4px; cursor: pointer; font-size: 0.85em;">Retry all failed ({failed_count})</button>' if failed_count else ''}
            <span id="refresh-status" style="font-size: 0.8em; color: #999;"></span>
        </div>
        <div class="cards">
            <div class="card">
                <div class="value">{doc_total}</div>
                <div class="label">Total Documents</div>
            </div>
            <div class="card">
                <div class="value">{transcribed}</div>
                <div class="label">Transcribed</div>
            </div>
            <div class="card">
                <div class="value">{pending_jobs}</div>
                <div class="label">Pending Jobs</div>
            </div>
            <div class="card">
                <div class="value" style="color: {('#dc3545' if failed_jobs else '#333')};">{failed_jobs}</div>
                <div class="label">Failed Jobs</div>
            </div>
        </div>

        <h2>Recent Events</h2>
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Event</th>
                    <th>Title</th>
                    <th>Doc ID</th>
                    <th>Detail</th>
                </tr>
            </thead>
            <tbody>
                {events_html}
            </tbody>
        </table>

        <h2>Documents</h2>
        <table>
            <thead>
                <tr>
                    <th>Created</th>
                    <th>Title</th>
                    <th>Category</th>
                    <th>Status</th>
                    <th>Jobs</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                {docs_html}
            </tbody>
        </table>
    """


def _build_ingestion_page() -> str:
    """Build the standalone ingestion dashboard HTML page."""
    if not _db:
        return """
<!DOCTYPE html><html><head><title>Ingestion Dashboard</title>
<style>body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }</style>
</head><body><h1>Ingestion Dashboard</h1><p>Database not configured. Set DATABASE_URL to enable.</p>
<p><a href="/">&larr; Home</a></p></body></html>"""

    try:
        ingestion_html = _build_ingestion_html()
    except Exception as e:
        ingestion_html = f'<div style="color: red;">Error loading ingestion data: {e}</div>'

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Ingestion Dashboard - Feldenkrais</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; }}
        h2 {{ color: #555; border-bottom: 1px solid #ddd; padding-bottom: 5px; margin-top: 30px; }}
        .nav {{ margin-bottom: 20px; }}
        .nav a {{ color: #007bff; text-decoration: none; }}
        .nav a:hover {{ text-decoration: underline; }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}
        .card {{
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
        }}
        .card .value {{
            font-size: 2em;
            font-weight: bold;
            color: #333;
        }}
        .card .label {{
            font-size: 0.85em;
            color: #666;
            margin-top: 5px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        th, td {{
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        th {{
            background: #f0f0f0;
            font-weight: bold;
            font-size: 0.9em;
        }}
        tr:hover {{ background: #f8f9fa; }}
    </style>
</head>
<body>
    <div class="nav"><a href="/">&larr; Home</a> <a href="/analysis">Analysis</a> <a href="/pipeline">Book Pipeline</a></div>
    <h1>Ingestion Dashboard</h1>

    <div id="ingestion-content">
        {ingestion_html}
    </div>

    <script>
        document.addEventListener('click', function(e) {{
            if (e.target && e.target.id === 'refresh-ingestion') {{
                var btn = e.target;
                var statusEl = document.getElementById('refresh-status');
                btn.disabled = true;
                btn.textContent = 'Refreshing...';
                if (statusEl) statusEl.textContent = '';
                fetch('/api/pipeline/ingestion')
                    .then(function(r) {{ return r.text(); }})
                    .then(function(html) {{
                        document.getElementById('ingestion-content').innerHTML = html;
                    }})
                    .catch(function(err) {{
                        if (statusEl) statusEl.textContent = 'Error: ' + err;
                        btn.disabled = false;
                        btn.textContent = 'Refresh';
                    }});
            }}
            if (e.target && e.target.id === 'retry-all-failed') {{
                var btn = e.target;
                if (!confirm('Retry all failed documents? Their transcription state will be cleared for reprocessing.')) return;
                btn.disabled = true;
                btn.textContent = 'Retrying...';
                fetch('/api/ingestion/retry-all-failed', {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        if (data.status === 'ok') {{
                            btn.textContent = 'Retried ' + data.count;
                            btn.style.color = '#28a745';
                            btn.style.borderColor = '#28a745';
                            setTimeout(function() {{
                                fetch('/api/pipeline/ingestion')
                                    .then(function(r) {{ return r.text(); }})
                                    .then(function(html) {{
                                        document.getElementById('ingestion-content').innerHTML = html;
                                    }});
                            }}, 500);
                        }} else {{
                            btn.textContent = data.error || 'Error';
                            btn.disabled = false;
                        }}
                    }})
                    .catch(function(err) {{
                        btn.textContent = 'Error';
                        btn.disabled = false;
                    }});
            }}
            if (e.target && e.target.classList.contains('resubmit-btn')) {{
                var btn = e.target;
                var docId = btn.dataset.docid;
                if (!confirm('Resubmit this document? All existing transcription, analysis, and embeddings will be cleared.')) return;
                btn.disabled = true;
                btn.textContent = 'Clearing...';
                fetch('/api/ingestion/resubmit/' + encodeURIComponent(docId), {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        if (data.status === 'ok') {{
                            btn.textContent = 'Done';
                            btn.style.color = '#28a745';
                            btn.style.borderColor = '#28a745';
                            // Refresh the page content
                            setTimeout(function() {{
                                fetch('/api/pipeline/ingestion')
                                    .then(function(r) {{ return r.text(); }})
                                    .then(function(html) {{
                                        document.getElementById('ingestion-content').innerHTML = html;
                                    }});
                            }}, 500);
                        }} else {{
                            btn.textContent = data.error || 'Error';
                            btn.disabled = false;
                        }}
                    }})
                    .catch(function(err) {{
                        btn.textContent = 'Error';
                        btn.disabled = false;
                    }});
            }}
        }});
    </script>
</body>
</html>
"""


def _analysis_step_names():
    """Return step names in display order, filtered to those in DB."""
    steps = _db.get_analysis_steps()
    step_lookup = {s["name"]: s for s in steps}
    ordered = [
        "decompose", "somatic", "language", "condense", "embed",
        "concept_tags", "index_concepts", "spatial", "index_spatial",
        "language_patterns", "extract_steps", "map_body_parts",
    ]
    return [sn for sn in ordered if sn in step_lookup], step_lookup


_STEP_DISPLAY_NAMES = {
    "decompose": "decompose", "somatic": "somatic", "language": "language",
    "language_patterns": "lang-pat", "concept_tags": "concepts", "spatial": "spatial",
    "condense": "condense", "embed": "embed", "index_concepts": "idx-conc",
    "index_spatial": "idx-spat", "extract_steps": "extract", "map_body_parts": "map-body",
}


def _build_analysis_html() -> str:
    """Build the group-level analysis dashboard content HTML."""
    groups = _db.get_group_dashboard()
    step_names, step_lookup = _analysis_step_names()

    def step_tooltip(sn):
        s = step_lookup[sn]
        deps = ", ".join(s.get("depends_on") or []) or "none"
        desc = s.get("description") or ""
        return f"{sn} (v{s['current_version']}): {desc}. Depends on: {deps}"

    header_cells = "".join(
        f'<th style="text-align:center;padding:4px 6px;font-size:0.8em;cursor:help;" title="{step_tooltip(sn)}">'
        f'{_STEP_DISPLAY_NAMES.get(sn, sn)}</th>'
        for sn in step_names
    )

    rows = []
    for group in groups:
        group_id = group["id"]
        name = html_mod.escape(group.get("name", ""))
        doc_count = group.get("doc_count", 0)
        gate_cells = ""
        for sn in step_names:
            permitted = group["gates"].get(sn, False)
            sv = step_lookup[sn]["current_version"]
            if permitted:
                label, bg, fg = f"v{sv}", "#d4edda", "#155724"
                title = f"Permitted (v{sv}) — click to disable"
            else:
                label, bg, fg = "off", "#e2e3e5", "#6c757d"
                title = f"Click to permit v{sv}"
            onclick = f"toggleGroupGate({group_id}, '{sn}', {str(not permitted).lower()})"
            gate_cells += (
                f'<td style="text-align:center;padding:4px 6px;cursor:pointer;" title="{title}" onclick="{onclick}">'
                f'<span style="padding:2px 6px;border-radius:8px;font-size:0.75em;'
                f'background:{bg};color:{fg};">{label}</span></td>'
            )

        rows.append(
            f'<tr>'
            f'<td style="font-size:0.85em;"><a href="/analysis?group={group_id}" style="color:#007bff;text-decoration:none;">{name}</a></td>'
            f'<td style="text-align:center;font-size:0.85em;color:#666;">{doc_count}</td>'
            f'{gate_cells}'
            f'</tr>'
        )

    rows_html = "\n".join(rows) if rows else (
        f'<tr><td colspan="{2 + len(step_names)}" style="text-align:center;color:#999;">No groups found. Run sync to create groups from sources.</td></tr>'
    )

    runnable_count = _db.count_runnable_steps()

    return f"""
    <div style="display:flex;gap:16px;margin-bottom:16px;align-items:center;">
        <div style="background:#f0f7ff;padding:8px 16px;border-radius:8px;">
            <strong>{len(groups)}</strong> groups
        </div>
        <div style="background:{'#fff3cd' if runnable_count > 0 else '#d4edda'};padding:8px 16px;border-radius:8px;">
            <strong>{runnable_count}</strong> runnable steps
        </div>
        <button id="pipeline-toggle" onclick="togglePipeline()"
                style="padding:6px 16px;background:{'#dc3545' if _pipeline_is_running() else '#007bff'};color:white;border:none;border-radius:4px;cursor:pointer;"
                {'disabled' if runnable_count == 0 and not _pipeline_is_running() else ''}>
            {'Stop Pipeline' if _pipeline_is_running() else 'Start Pipeline'}
        </button>
        <button onclick="refreshDashboard()" style="padding:6px 16px;background:#6c757d;color:white;border:none;border-radius:4px;cursor:pointer;">
            Refresh
        </button>
    </div>

    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;">
        <thead>
            <tr style="border-bottom:2px solid #dee2e6;">
                <th style="text-align:left;padding:4px 6px;">Group</th>
                <th style="text-align:center;padding:4px 6px;font-size:0.8em;">docs</th>
                {header_cells}
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    </div>

    <div style="margin-top:16px;font-size:0.8em;color:#999;">
        Click a cell to toggle permission for the group. Click a group name to see document details.
    </div>
    """


def _build_analysis_group_html(group_id: int) -> str:
    """Build the document drill-down view for a specific group."""
    group = _db.get_lesson_group(group_id)
    if not group:
        return "<p>Group not found.</p>"

    docs = _db.get_document_dashboard(group_id)
    step_names, step_lookup = _analysis_step_names()

    def step_tooltip(sn):
        s = step_lookup[sn]
        deps = ", ".join(s.get("depends_on") or []) or "none"
        desc = s.get("description") or ""
        return f"{sn} (v{s['current_version']}): {desc}. Depends on: {deps}"

    header_cells = "".join(
        f'<th style="text-align:center;padding:4px 6px;font-size:0.8em;cursor:help;" title="{step_tooltip(sn)}">'
        f'{_STEP_DISPLAY_NAMES.get(sn, sn)}</th>'
        for sn in step_names
    )

    rows = []
    for doc in docs:
        doc_id = str(doc["document_id"])
        title = doc.get("title", "")
        if len(title) > 50:
            title = title[:47] + "..."

        gate_cells = ""
        for sn in step_names:
            gate = doc["gates"].get(sn, {"state": "inactive"})
            state = gate["state"]
            cv = gate.get("completed_version")
            sv = gate.get("current_version")
            err = gate.get("error")

            if state == "current":
                label = f"v{cv}"
                bg, fg = "#d4edda", "#155724"
                title_attr = f"Current (v{cv})"
            elif state == "stale":
                label = f"v{cv}" if cv else "new"
                bg, fg = "#fff3cd", "#856404"
                title_attr = f"Stale — will run (current: v{sv})"
            elif state == "error":
                label = "err"
                bg, fg = "#f8d7da", "#721c24"
                err_safe = (err or 'unknown').replace('"', '&quot;').replace("'", "\\'")[:200]
                title_attr = f"Error: {err_safe} (click to clear)"
            else:
                label = "—"
                bg, fg = "#f8f9fa", "#adb5bd"
                title_attr = "Not started"

            if state == "error":
                onclick = f" onclick=\"clearError('{doc_id}', '{sn}')\" style=\"cursor:pointer;\""
            else:
                onclick = ""

            gate_cells += (
                f'<td style="text-align:center;padding:4px 6px;{"cursor:pointer;" if state == "error" else ""}" '
                f'title="{title_attr}"{onclick}>'
                f'<span style="padding:2px 6px;border-radius:8px;font-size:0.75em;'
                f'background:{bg};color:{fg};">{label}</span></td>'
            )

        rows.append(
            f'<tr>'
            f'<td style="font-size:0.85em;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{html_mod.escape(doc.get("title", ""))}">{html_mod.escape(title)}</td>'
            f'{gate_cells}'
            f'</tr>'
        )

    rows_html = "\n".join(rows) if rows else (
        f'<tr><td colspan="{1 + len(step_names)}" style="text-align:center;color:#999;">No documents in this group</td></tr>'
    )

    group_name = html_mod.escape(group.get("name", ""))

    return f"""
    <div style="margin-bottom:16px;">
        <a href="/analysis" style="color:#007bff;text-decoration:none;">&larr; Back to groups</a>
        <h2 style="margin:8px 0;">{group_name}</h2>
        <span style="color:#666;font-size:0.9em;">{len(docs)} documents</span>
    </div>

    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;">
        <thead>
            <tr style="border-bottom:2px solid #dee2e6;">
                <th style="text-align:left;padding:4px 6px;">Document</th>
                {header_cells}
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    </div>

    <div style="margin-top:16px;font-size:0.8em;color:#999;">
        Colors:
        <span style="background:#d4edda;padding:1px 6px;border-radius:4px;">current</span>
        <span style="background:#fff3cd;padding:1px 6px;border-radius:4px;">stale (will run)</span>
        <span style="background:#f8d7da;padding:1px 6px;border-radius:4px;">error (click to clear)</span>
        <span style="background:#f8f9fa;padding:1px 6px;border-radius:4px;color:#adb5bd;">not started</span>
    </div>
    """


def _build_analysis_page(group_id: int | None = None) -> str:
    """Build the full analysis dashboard HTML page."""
    if not _db:
        return """
<!DOCTYPE html><html><head><title>Analysis Pipeline</title>
<style>body { font-family: -apple-system, sans-serif; max-width: 1400px; margin: 0 auto; padding: 20px; }</style>
</head><body><h1>Analysis Pipeline</h1><p>Database not configured.</p></body></html>"""

    if group_id is not None:
        content = _build_analysis_group_html(group_id)
        status_panels = ""
    else:
        content = _build_analysis_html()
        status_panels = _build_pipeline_status_html()

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Analysis Pipeline - Feldenkrais</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; }}
        .nav {{ margin-bottom: 20px; }}
        .nav a {{ color: #007bff; text-decoration: none; margin-right: 16px; }}
        .nav a:hover {{ text-decoration: underline; }}
        table {{ border-collapse: collapse; }}
        th, td {{ border-bottom: 1px solid #eee; }}
        th {{ position: sticky; top: 0; background: white; z-index: 1; }}
        tr:hover {{ background: #f8f9fa; }}
    </style>
</head>
<body>
    <div class="nav">
        <a href="/">&larr; Home</a>
        <a href="/ingestion">Ingestion</a>
        <a href="/analysis">Analysis</a>
        <a href="/pipeline">Book Pipeline</a>
    </div>
    <h1>Analysis Pipeline</h1>
    <p style="color:#666;font-size:0.9em;margin-bottom:16px;">
        Toggle steps at the group level. Click a group name to see per-document completion states.
        Only permitted + pending steps will run when you click "Run Pipeline".
    </p>
    <details style="margin-bottom:16px;font-size:0.85em;">
        <summary style="cursor:pointer;color:#007bff;">Show step dependency graph</summary>
        <pre style="background:#f8f9fa;padding:12px;border-radius:6px;overflow-x:auto;font-size:0.8em;line-height:1.4;">
decompose ──┬──► somatic ──┬──► condense ──► embed
            │              │
            ├──► language ──┘
            │
            ├──► concept_tags ──► index_concepts
            │    (needs decompose + somatic)
            │
            └──► spatial ──► index_spatial
                 (needs decompose + somatic)

language_patterns (depends on decompose + language)
extract_steps ──► map_body_parts (independent)</pre>
    </details>

    <div id="analysis-content">
        {content}
    </div>

    <div id="analysis-status">
        {status_panels}
    </div>

    <script>
    function clearAllErrors() {{
        if (!confirm('Clear all errors? These steps will become runnable again.')) return;
        fetch('/api/analysis/clear-all-errors', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: '{{}}'
        }})
        .then(r => r.json())
        .then(data => {{
            if (data.status === 'ok') {{
                refreshDashboard();
            }} else alert(data.error || 'Error');
        }})
        .catch(err => alert('Error: ' + err));
    }}

    function toggleGroupGate(groupId, stepName, newPermitted) {{
        fetch('/api/analysis/gate', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{group_id: groupId, step_name: stepName, permitted: newPermitted}})
        }})
        .then(r => r.json())
        .then(data => {{
            if (data.status === 'ok') refreshDashboard();
            else alert(data.error || 'Error');
        }})
        .catch(err => alert('Error: ' + err));
    }}

    function clearError(docId, stepName) {{
        fetch('/api/analysis/clear-error', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{document_id: docId, step_name: stepName}})
        }})
        .then(r => r.json())
        .then(data => {{
            if (data.status === 'ok') refreshDashboard();
            else alert(data.error || 'Error');
        }})
        .catch(err => alert('Error: ' + err));
    }}

    function refreshDashboard() {{
        const url = new URL(window.location);
        const groupId = url.searchParams.get('group');
        const endpoint = groupId
            ? '/api/analysis/group/' + groupId
            : '/api/analysis/dashboard';
        fetch(endpoint)
            .then(r => r.text())
            .then(html => {{
                document.getElementById('analysis-content').innerHTML = html;
            }});
        // Refresh status panels (only on group view)
        const statusEl = document.getElementById('analysis-status');
        if (statusEl && !groupId) {{
            fetch('/api/analysis/status')
                .then(r => r.text())
                .then(html => {{ statusEl.innerHTML = html; }});
        }}
        // Update pipeline button state
        fetch('/api/analysis/pipeline-status')
            .then(r => r.json())
            .then(data => {{
                const btn = document.getElementById('pipeline-toggle');
                if (btn) {{
                    btn.style.background = data.running ? '#dc3545' : '#007bff';
                    btn.textContent = data.running ? 'Stop Pipeline' : 'Start Pipeline';
                    btn.disabled = false;
                }}
            }});
    }}

    // Auto-refresh every 10s when pipeline is running
    setInterval(() => {{
        fetch('/api/analysis/pipeline-status')
            .then(r => r.json())
            .then(data => {{
                if (data.running) refreshDashboard();
            }});
    }}, 10000);

    function togglePipeline() {{
        fetch('/api/analysis/run', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: '{{}}'
        }})
        .then(r => r.json())
        .then(data => {{
            refreshDashboard();
        }})
        .catch(err => alert('Error: ' + err));
    }}
    </script>
</body>
</html>
"""


def _build_pipeline_status_html() -> str:
    """Build the pipeline progress + errors panels."""
    progress = _db.get_pipeline_progress()
    errors = _db.get_pipeline_errors()

    # Progress table: group × step with completion bars
    if progress:
        # Collect unique groups and steps in order
        groups_seen = {}
        steps_seen = []
        for row in progress:
            if row["group_id"] not in groups_seen:
                groups_seen[row["group_id"]] = row["group_name"]
            if row["step_name"] not in steps_seen:
                steps_seen.append(row["step_name"])

        # Build a lookup
        lookup = {}
        for row in progress:
            lookup[(row["group_id"], row["step_name"])] = row

        header = "".join(
            f'<th style="text-align:center;padding:4px 8px;font-size:0.8em;">'
            f'{_STEP_DISPLAY_NAMES.get(sn, sn)}</th>'
            for sn in steps_seen
        )

        rows = []
        for gid, gname in groups_seen.items():
            cells = ""
            for sn in steps_seen:
                r = lookup.get((gid, sn))
                if not r:
                    cells += '<td style="text-align:center;color:#ccc;">—</td>'
                    continue
                total = r["total"]
                done = r["completed"]
                stale = r["stale"]
                errs = r["errors"]
                pending = total - done - stale - errs
                pct = int(100 * done / total) if total > 0 else 0

                if errs > 0:
                    color = "#dc3545"
                    label = f"{done}/{total} ({errs} err)"
                elif done == total:
                    color = "#28a745"
                    label = f"{done}/{total}"
                elif done > 0 or stale > 0:
                    color = "#ffc107"
                    label = f"{done}/{total}"
                else:
                    color = "#6c757d"
                    label = f"0/{total}"

                cells += (
                    f'<td style="text-align:center;padding:4px 8px;font-size:0.8em;">'
                    f'<div style="background:#e9ecef;border-radius:4px;overflow:hidden;height:18px;position:relative;">'
                    f'<div style="background:{color};height:100%;width:{pct}%;"></div>'
                    f'<span style="position:absolute;top:0;left:0;right:0;text-align:center;font-size:0.75em;line-height:18px;">{label}</span>'
                    f'</div></td>'
                )

            rows.append(
                f'<tr><td style="font-size:0.85em;padding:4px 8px;">{html_mod.escape(gname)}</td>{cells}</tr>'
            )

        progress_html = f"""
        <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="border-bottom:2px solid #dee2e6;">
                <th style="text-align:left;padding:4px 8px;">Group</th>{header}
            </tr></thead>
            <tbody>{"".join(rows)}</tbody>
        </table>
        """
    else:
        progress_html = '<p style="color:#999;">No steps are permitted yet. Toggle gates above to enable analysis.</p>'

    # Errors list
    if errors:
        error_rows = []
        for e in errors:
            doc_id = str(e["document_id"])
            title = html_mod.escape(e.get("title", "")[:50])
            step = e["step_name"]
            err_text = html_mod.escape((e.get("error") or "")[:120])
            updated = str(e.get("updated_at") or "")[:16]
            error_rows.append(
                f'<tr>'
                f'<td style="font-size:0.85em;padding:4px 8px;">{title}</td>'
                f'<td style="font-size:0.8em;padding:4px 8px;">{step}</td>'
                f'<td style="font-size:0.8em;padding:4px 8px;color:#721c24;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{err_text}">{err_text}</td>'
                f'<td style="font-size:0.75em;padding:4px 8px;color:#999;">{updated}</td>'
                f'<td style="padding:4px 8px;"><button onclick="clearError(\'{doc_id}\', \'{step}\')" '
                f'style="font-size:0.75em;padding:2px 8px;border:1px solid #ccc;border-radius:4px;cursor:pointer;background:white;">clear</button></td>'
                f'</tr>'
            )
        errors_html = f"""
        <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="border-bottom:2px solid #dee2e6;">
                <th style="text-align:left;padding:4px 8px;">Document</th>
                <th style="text-align:left;padding:4px 8px;">Step</th>
                <th style="text-align:left;padding:4px 8px;">Error</th>
                <th style="text-align:left;padding:4px 8px;">When</th>
                <th></th>
            </tr></thead>
            <tbody>{"".join(error_rows)}</tbody>
        </table>
        """
    else:
        errors_html = '<p style="color:#999;">No errors.</p>'

    # Overall counts
    total_steps = sum(r["total"] for r in progress)
    completed_steps = sum(r["completed"] for r in progress)
    error_steps = sum(r["errors"] for r in progress)
    remaining = total_steps - completed_steps - error_steps

    if progress:
        rate = _db.get_recent_completion_rate(5)
        if rate > 0 and remaining > 0:
            eta_minutes = remaining / rate
            if eta_minutes >= 60:
                eta_str = f" — ~{int(eta_minutes // 60)}h{int(eta_minutes % 60)}m"
            else:
                eta_str = f" — ~{int(eta_minutes)}m"
        else:
            eta_str = ""
        remaining_badge = (
            f'<span style="font-size:0.75em;font-weight:normal;margin-left:12px;'
            f'padding:2px 8px;border-radius:8px;'
            f'background:{"#fff3cd" if remaining > 0 else "#d4edda"};">'
            f'{remaining} remaining{eta_str}</span>'
        )
    else:
        remaining_badge = ""

    # Active steps
    import datetime
    active_data = _db.get_cache(_PIPELINE_ACTIVE_KEY) if _db else None
    active_steps = (active_data or {}).get("steps", [])
    now = datetime.datetime.utcnow()
    if active_steps and _pipeline_is_running():
        active_items = ""
        for s in active_steps:
            started = s.get("started")
            if started:
                try:
                    elapsed = (now - datetime.datetime.fromisoformat(started)).total_seconds()
                    if elapsed >= 60:
                        elapsed_str = f"{int(elapsed // 60)}m{int(elapsed % 60)}s"
                    else:
                        elapsed_str = f"{int(elapsed)}s"
                except (ValueError, TypeError):
                    elapsed_str = ""
            else:
                elapsed_str = ""
            time_span = f' <span style="color:#999;font-size:0.8em;">({elapsed_str})</span>' if elapsed_str else ""
            active_items += f'<li style="font-size:0.85em;"><strong>{html_mod.escape(s["step"])}</strong> — {html_mod.escape(s["title"])}{time_span}</li>'
        active_html = f'<ul style="margin:0;padding-left:20px;">{active_items}</ul>'
    else:
        active_html = ""

    return f"""
    <div style="margin-top:32px;">
        <h2 style="font-size:1.1em;margin-bottom:12px;">Pipeline Progress{remaining_badge}</h2>
        {progress_html}
    </div>

    {f'<div style="margin-top:32px;"><h2 style="font-size:1.1em;margin-bottom:8px;">Currently Running</h2>{active_html}</div>' if active_html else ''}

    <div style="margin-top:32px;">
        <h2 style="font-size:1.1em;margin-bottom:12px;">
            Errors
            <span style="font-size:0.8em;color:#dc3545;font-weight:normal;">
                {f'({len(errors)})' if errors else ''}
            </span>
            {f'<button onclick="clearAllErrors()" style="font-size:0.7em;padding:2px 10px;margin-left:8px;border:1px solid #ccc;border-radius:4px;cursor:pointer;background:white;vertical-align:middle;">Clear all &amp; retry</button>' if errors else ''}
        </h2>
        {errors_html}
    </div>
    """


def build_pipeline_html() -> str:
    """Build the pipeline dashboard HTML page (book pipeline only)."""
    if not _db:
        return """
<!DOCTYPE html><html><head><title>Pipeline Dashboard</title>
<style>body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }</style>
</head><body><h1>Pipeline Dashboard</h1><p>Database not configured. Set DATABASE_URL to enable.</p>
<p><a href="/">&larr; Home</a></p></body></html>"""

    # Book pipeline section (injected by web_book router when available)
    book_pipeline_fn = getattr(app.state, 'build_book_pipeline_section', None)
    if book_pipeline_fn:
        try:
            book_pipeline_html = book_pipeline_fn()
        except Exception as e:
            book_pipeline_html = f'<p style="color: #999;">Book pipeline error: {e}</p>'
    else:
        book_pipeline_html = '<p style="color: #999;">Book pipeline not available.</p>'

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Pipeline Dashboard - Feldenkrais</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; }}
        h2 {{ color: #555; border-bottom: 1px solid #ddd; padding-bottom: 5px; margin-top: 30px; }}
        .nav {{ margin-bottom: 20px; }}
        .nav a {{ color: #007bff; text-decoration: none; }}
        .nav a:hover {{ text-decoration: underline; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        th, td {{
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        th {{
            background: #f0f0f0;
            font-weight: bold;
            font-size: 0.9em;
        }}
        tr:hover {{ background: #f8f9fa; }}
        table.compact th, table.compact td {{
            padding: 3px 10px;
            font-size: 0.88em;
            line-height: 1.3;
        }}
        th.sortable {{
            cursor: pointer;
            user-select: none;
        }}
        th.sortable:hover {{
            background: #e0e0e0;
        }}
    </style>
</head>
<body>
    <div class="nav"><a href="/">&larr; Home</a></div>
    <h1>Pipeline Dashboard</h1>

    <div id="tab-analysis">
        {book_pipeline_html}
    </div>

    <script>
        // Event delegation for toggle, rebuild, run, add-lesson, and remove-lesson buttons
        var analysisTab = document.getElementById('tab-analysis');
        if (analysisTab) analysisTab.addEventListener('click', function(e) {{
            var btn = e.target;
            if (btn.classList.contains('toggle-btn')) {{
                var node = btn.dataset.node;
                btn.disabled = true;
                btn.textContent = '...';
                fetch('/api/pipeline/toggle/' + encodeURIComponent(node), {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        var row = btn.closest('tr');
                        if (data.enabled) {{
                            btn.textContent = 'on';
                            btn.style.color = '#28a745';
                            btn.style.borderColor = '#28a745';
                            row.style.opacity = '1';
                        }} else {{
                            btn.textContent = 'off';
                            btn.style.color = '#999';
                            btn.style.borderColor = '#999';
                            row.style.opacity = '0.4';
                        }}
                        btn.disabled = false;
                    }})
                    .catch(function() {{
                        btn.textContent = 'err';
                        btn.disabled = false;
                    }});
            }}
            if (btn.classList.contains('rebuild-btn')) {{
                var node = btn.dataset.node;
                btn.disabled = true;
                btn.textContent = '...';
                fetch('/api/pipeline/rebuild/' + encodeURIComponent(node), {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        if (data.status === 'invalidated') {{
                            var row = btn.closest('tr');
                            var statusCell = row.querySelectorAll('td')[1];
                            if (statusCell) {{
                                statusCell.innerHTML = '<span style="color: #ffc107; font-weight: bold;">STALE</span>';
                            }}
                        }}
                        btn.textContent = 'invalidate';
                        btn.disabled = false;
                    }})
                    .catch(function() {{
                        btn.textContent = 'err';
                        btn.style.color = '#dc3545';
                        btn.disabled = false;
                    }});
            }}
            // Run pipeline for a lesson
            if (btn.classList.contains('run-lesson-btn')) {{
                var lessonId = btn.dataset.lesson;
                btn.disabled = true;
                var orig = btn.textContent;
                btn.textContent = 'Starting...';
                fetch('/api/pipeline/run/' + encodeURIComponent(lessonId), {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        btn.textContent = data.status === 'started' ? 'Running...' : (data.status || 'done');
                    }})
                    .catch(function() {{
                        btn.textContent = 'Error';
                        btn.disabled = false;
                    }});
            }}
            // Remove lesson from book
            if (btn.classList.contains('remove-lesson-btn')) {{
                var lessonId = btn.dataset.lesson;
                if (!confirm('Remove this lesson from the book?')) return;
                btn.disabled = true;
                btn.textContent = '...';
                fetch('/api/pipeline/book-lessons/remove', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{lesson_id: lessonId}})
                }})
                    .then(function() {{ location.reload(); }})
                    .catch(function() {{ btn.textContent = 'err'; btn.disabled = false; }});
            }}
            // Add lesson to book
            if (btn.id === 'add-lesson-btn') {{
                var sel = document.getElementById('add-lesson-select');
                var val = sel.value;
                if (!val) return;
                btn.disabled = true;
                btn.textContent = '...';
                fetch('/api/pipeline/book-lessons', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{lesson_id: val, title: sel.options[sel.selectedIndex].text}})
                }})
                    .then(function() {{ location.reload(); }})
                    .catch(function() {{ btn.textContent = 'err'; btn.disabled = false; }});
            }}
        }});

        // Source filter: rebuild lesson select based on selected source
        (function() {{
            var sel = document.getElementById('add-lesson-select');
            var filter = document.getElementById('source-filter');
            if (!sel || !filter) return;
            var allOpts = [];
            for (var i = 1; i < sel.options.length; i++) {{
                var o = sel.options[i];
                allOpts.push({{value: o.value, text: o.text, source: o.dataset.source}});
            }}
            filter.addEventListener('change', function() {{
                var sourceId = filter.value;
                while (sel.options.length > 1) sel.remove(1);
                allOpts.forEach(function(item) {{
                    if (sourceId === 'all' || item.source === sourceId) {{
                        var opt = document.createElement('option');
                        opt.value = item.value;
                        opt.textContent = item.text;
                        opt.dataset.source = item.source;
                        sel.appendChild(opt);
                    }}
                }});
            }});
        }})();

        // Auto-refresh: poll pipeline status every 5 seconds
        (function() {{
            var container = document.getElementById('tab-analysis');
            if (!container) return;
            var refreshInterval = 5000;
            var paused = false;

            function refresh() {{
                if (paused) return;
                fetch('/api/pipeline/status')
                    .then(function(r) {{ return r.text(); }})
                    .then(function(html) {{
                        container.innerHTML = html;
                    }})
                    .catch(function(e) {{
                        console.error('Pipeline refresh failed:', e);
                    }});
            }}

            setInterval(refresh, refreshInterval);

            container.addEventListener('mousedown', function() {{ paused = true; }});
            document.addEventListener('mouseup', function() {{
                setTimeout(function() {{ paused = false; }}, 1000);
            }});
        }})();
    </script>
</body>
</html>
"""


@app.get("/ingestion", response_class=HTMLResponse)
async def ingestion_dashboard(request: Request):
    """Ingestion monitoring dashboard with resubmission support."""
    if IS_LAMBDA and _auth_mod.get_access_tier(request) != "admin":
        return RedirectResponse("/")
    return _build_ingestion_page()


@app.get("/pipeline", response_class=HTMLResponse)
async def pipeline_dashboard(request: Request):
    """Pipeline dashboard — book generation."""
    if IS_LAMBDA and _auth_mod.get_access_tier(request) != "admin":
        return RedirectResponse("/")
    return build_pipeline_html()


# --- Docs ---

DOCS_DIR = Path(__file__).parent.parent.parent / "docs" if not IS_LAMBDA else Path(__file__).parent / "docs"

@app.get("/docs/{slug}", response_class=HTMLResponse)
async def docs_page(request: Request, slug: str):
    """Render a markdown doc file."""
    # Sanitize slug — alphanumeric, hyphens, underscores only
    if not all(c.isalnum() or c in "-_" for c in slug):
        return HTMLResponse("Not found", status_code=404)
    md_path = DOCS_DIR / f"{slug}.md"
    if not md_path.exists():
        return HTMLResponse("Not found", status_code=404)
    md_content = md_path.read_text()
    user = _auth_mod.get_current_user(request) if _auth_mod else None
    if user:
        login = html_mod.escape(user.get("login", ""))
        su_parts = []
        if user.get("superuser_eligible"):
            if _is_superuser_active(request, user):
                su_parts.append('<a href="/auth/superuser/off" style="color:#d32f2f; font-weight:600;">Exit Superuser</a>')
            else:
                su_parts.append('<a href="/auth/superuser/on">Superuser</a>')
        su_html = (' &middot; ' + ' &middot; '.join(su_parts)) if su_parts else ''
        auth_bar = f'<div class="auth-bar">{login}{su_html} &middot; <a href="/auth/logout">Log out</a> &middot; <a href="/">Search</a></div>'
    else:
        auth_bar = ''
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Docs</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }}
    {_AUTH_BAR_CSS}
    h1 {{ border-bottom: 2px solid #eee; padding-bottom: 8px; }}
    h2 {{ border-bottom: 1px solid #eee; padding-bottom: 4px; margin-top: 2em; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
    th {{ background: #f5f5f5; }}
    code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
    pre {{ background: #f5f5f5; padding: 12px; border-radius: 4px; overflow-x: auto; }}
    pre code {{ background: none; padding: 0; }}
    a {{ color: #007bff; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
</style>
</head><body>
{auth_bar}
<div id="content"></div>
<script>
    const md = {json.dumps(md_content)};
    document.getElementById('content').innerHTML = marked.parse(md);
    // Set page title from first h1
    const h1 = document.querySelector('#content h1');
    if (h1) document.title = h1.textContent;
</script>
</body></html>""")


# --- Search ---

def _single_method_search(embedding, method: str, limit: int = 1000):
    """Run embedding search for a single method. Returns (results, band, gap)."""
    results = _db.search_lessons(embedding, limit=limit, method=method)
    sims = [r["similarity"] for r in results]
    gap = round(sims[0] - sims[4], 4) if len(sims) >= 5 else 0
    if gap < 0.0167:
        band = "low"
    elif gap < 0.0215:
        band = "mid"
    elif gap < 0.0297:
        band = "mid-high"
    else:
        band = "high"
    return results, band, gap


def _combined_search(query: str, embedding, limit: int = 1000):
    """Cascade search: trust distinctive when confident, RRF fallback otherwise.

    Optimized via grid search (optimize_combined.py, 2026-03-03):
        gate_threshold=0.07, fallback=rrf, concepts_weight=3.0, tag_boost=0.0
        P@1=44.2%, MRR=0.590 vs always-distinctive P@1=41.9%, MRR=0.565

    Logic:
        1. Run concepts (distinctive) search, compute similarity gap
        2. If gap >= GATE_THRESHOLD: distinctive is confident, use it directly
        3. Otherwise: RRF merge of default + concepts (3x weight)
    """
    GATE_THRESHOLD = 0.07
    RRF_K = 60
    CONCEPTS_WEIGHT = 3.0

    # Always run both searches (need concepts for gate check, both for RRF)
    default_hits = _db.search_lessons(embedding, limit=1000, method="default")
    concepts_hits = _db.search_lessons(embedding, limit=1000, method="concepts")

    # Compute concepts gap for confidence gate
    concepts_sims = [h["similarity"] for h in concepts_hits]
    concepts_gap = concepts_sims[0] - concepts_sims[4] if len(concepts_sims) >= 5 else 0

    if concepts_gap >= GATE_THRESHOLD:
        # Confident — use concepts results directly
        cascade_mode = "confident"
        results = concepts_hits[:limit]
        for r in results:
            r.setdefault("summary_text", "")
    else:
        # Ambiguous — RRF merge of default + concepts (no tag boost)
        cascade_mode = "blended"
        scores = {}
        result_data = {}

        for rank, hit in enumerate(default_hits, 1):
            doc_id = hit["document_id"]
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (RRF_K + rank)
            if doc_id not in result_data:
                result_data[doc_id] = hit

        for rank, hit in enumerate(concepts_hits, 1):
            doc_id = hit["document_id"]
            scores[doc_id] = scores.get(doc_id, 0) + CONCEPTS_WEIGHT / (RRF_K + rank)
            if doc_id not in result_data:
                result_data[doc_id] = hit

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        max_score = ranked[0][1] if ranked else 1.0
        results = []
        for doc_id, score in ranked:
            r = dict(result_data[doc_id])
            r["similarity"] = round(score / max_score, 4)
            r.setdefault("summary_text", "")
            results.append(r)

    # Band from concepts gap
    gap = round(concepts_gap, 4)
    if gap < 0.0167:
        band = "low"
    elif gap < 0.0215:
        band = "mid"
    elif gap < 0.0297:
        band = "mid-high"
    else:
        band = "high"

    return results, band, gap, cascade_mode


@app.post("/api/search")
async def api_search(request: Request, query: str = Form(""), method: str = Form("default")):
    """Semantic search over lesson embeddings. Supports multiple search methods."""
    query = query.strip()
    if not query:
        return JSONResponse({"error": "Empty query"}, status_code=400)
    if not _db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    if method not in ("default", "concepts", "tags", "combined"):
        return JSONResponse({"error": f"Unknown method: {method}"}, status_code=400)
    _set_source_access(request)
    try:
        if method == "tags":
            # Tag-based search: match query words against concept values
            terms = [t.strip() for t in query.split() if t.strip()]
            results = _db.search_by_concepts(terms, limit=10)
            # Normalize results to match embedding search format
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

        # Embedding-based search
        from llm import get_embeddings
        embeddings, _ = get_embeddings([query], task_type="RETRIEVAL_QUERY")

        cascade_mode = None
        if method == "combined":
            results, band, gap, cascade_mode = _combined_search(query, embeddings[0])
        else:
            results, band, gap = _single_method_search(embeddings[0], method)

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
            _user = _auth_mod.get_current_user(request)
            _db.log_search_query(
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


@app.get("/pass0b", response_class=HTMLResponse)
async def pass0b_page(request: Request):
    """Pass 0b experiment page."""
    _set_source_access(request)
    return build_pass0b_html(search_mode=_is_search_site(request), get_lessons_fn=get_available_lessons)


@app.post("/api/pass0b/clear-cache")
async def api_pass0b_clear_cache():
    """Clear Pass A + Pass 0b cache entries."""
    if not _db:
        return JSONResponse({"error": "No database connection"}, status_code=500)

    rows = _db.list_cache()
    keys = []
    for r in rows:
        try:
            data = _db.get_cache(r["key"])
        except Exception:
            continue
        if not data:
            continue
        # Pass 0b entries
        if "enriched_segments" in data or "enriched_steps" in data:
            keys.append(r["key"])
        # Pass A entries (have "steps" and "classifications" but not enriched_steps)
        elif "steps" in data and "classifications" in data:
            keys.append(r["key"])
    if keys:
        with _db.get_cursor() as cur:
            for k in keys:
                cur.execute("DELETE FROM cache WHERE key = %s", (k,))
    return JSONResponse({"deleted": len(keys)})


@app.post("/api/pass0b")
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
            asyncio.to_thread(run_pass_0b, text, prov, debug_log),
            timeout=ANALYSIS_TIMEOUT,
        )
        # Align with whisper timestamps for step-level timing
        whisper_segments = load_whisper_segments(selected_category, selected_lesson)
        if whisper_segments:
            align_pass0_with_whisper(result.get("pass_a", {}).get("pass_0", {}), whisper_segments)
        result_html = render_pass0b_result(result)
        debug_html = render_debug_log(debug_log) if debug_log else ""
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
        debug_html = render_debug_log(debug_log) if debug_log else ""
        return JSONResponse({
            "error": str(e),
            "debug_html": debug_html,
        }, status_code=500)


@app.post("/api/pass0b-combined")
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
                asyncio.to_thread(run_pass_0b, text, prov, debug_log_somatic),
                asyncio.to_thread(run_pass_0b_lang, text, prov, debug_log_lang),
            ),
            timeout=ANALYSIS_TIMEOUT,
        )
        # Align whisper timestamps (somatic result has pass_a)
        whisper_segments = load_whisper_segments(selected_category, selected_lesson)
        if whisper_segments:
            align_pass0_with_whisper(somatic_result.get("pass_a", {}).get("pass_0", {}), whisper_segments)
            # Lang result shares the same pass_a, align it too
            align_pass0_with_whisper(lang_result.get("pass_a", {}).get("pass_0", {}), whisper_segments)

        result_html = render_pass0b_combined_result(somatic_result, lang_result)
        debug_html = render_debug_log(debug_log_somatic + debug_log_lang)

        pass_a = somatic_result.get("pass_a", {})
        som_usage = somatic_result.get("usage", {})
        lang_usage = lang_result.get("usage", {})
        # Sum token counts
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
        debug_html = render_debug_log(debug_log_somatic + debug_log_lang)
        return JSONResponse({
            "error": str(e),
            "debug_html": debug_html,
        }, status_code=500)


@app.post("/api/pass0b-word-lang-json")
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
            asyncio.to_thread(run_pass_0b_word_lang, text, prov),
            timeout=ANALYSIS_TIMEOUT,
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


# --- Pass 0b Player: Audio-synced card view ---

@app.get("/api/audio-url/{document_id}")
async def api_audio_url(document_id: str):
    """Return a presigned S3 URL for the lesson's audio file."""
    if not _db or not KB_BUCKET:
        return JSONResponse({"error": "Database or S3 not configured"}, status_code=500)
    doc = _db.get_document(document_id)
    if not doc or not doc.get("s3_key"):
        return JSONResponse({"error": "Document or audio not found"}, status_code=404)
    storage = Storage(KB_BUCKET)
    url = storage.generate_presigned_url(doc["s3_key"], expiry_seconds=7200)
    return JSONResponse({"audio_url": url})


@app.get("/api/whisper-segments/{document_id}")
async def api_whisper_segments(document_id: str):
    """Return raw whisper segments for pre-analysis segment sync."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    segments = _db.get_document_whisper_segments(document_id)
    if segments is None:
        return JSONResponse({"error": "No whisper segments found"}, status_code=404)
    return JSONResponse({"segments": segments})


@app.get("/api/whisper-words/{document_id}")
async def api_whisper_words(document_id: str):
    """Return word-level timestamps for karaoke-style text highlighting."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    words = _db.get_document_whisper_words(document_id)
    if words is None:
        return JSONResponse({"words": None})
    return JSONResponse({"words": words})


@app.get("/api/lesson-info/{document_id}")
async def api_lesson_info(document_id: str):
    """Return lesson info for the detail page."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    info = _db.get_lesson_info(document_id)
    if not info:
        return JSONResponse({"error": "Lesson not found"}, status_code=404)
    return JSONResponse(info)


@app.get("/api/lesson/{document_id}/concepts")
async def api_lesson_concepts(document_id: str):
    """Get concept extraction data for a lesson."""
    if not _db:
        return JSONResponse({"error": "No database connection"}, status_code=500)
    try:
        concepts = _db.get_lesson_concepts(document_id)
        # Also get the cached concept extraction for summary/search terms
        text = _db.get_document_transcription(document_id)
        extra = {}
        if text:
            from cache_helpers import get_cache_key
            cache_key = get_cache_key(text.strip() + ":concept_tags_v2")
            cached = _db.get_cache(cache_key)
            if cached:
                extra["distinctive_summary"] = cached.get("distinctive_summary", "")
                extra["search_terms"] = cached.get("search_terms", [])
        return JSONResponse({"concepts": concepts, **extra})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/lesson/{category}/{filename}")
async def api_lesson_text(category: str, filename: str):
    """Return lesson text for a given category and lesson key."""
    text = load_lesson(category, filename)
    if text:
        return JSONResponse({"text": text})
    return JSONResponse({"error": "Lesson not found"}, status_code=404)


@app.post("/api/pass0b-combined-json")
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
                asyncio.to_thread(run_pass_0b, text, prov),
                asyncio.to_thread(run_pass_0b_lang, text, prov),
            ),
            timeout=ANALYSIS_TIMEOUT,
        )
        # Align whisper timestamps
        whisper_segments = load_whisper_segments(selected_category, selected_lesson)
        if whisper_segments:
            align_pass0_with_whisper(somatic_result.get("pass_a", {}).get("pass_0", {}), whisper_segments)
            align_pass0_with_whisper(lang_result.get("pass_a", {}).get("pass_0", {}), whisper_segments)

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

        # Segment lookups
        seg_lookup = {s["id"]: s for s in pass_0.get("segments", [])}

        # Build step objects
        steps_json = []
        for pa_step in pa_steps:
            sn = pa_step["step_number"]
            gn = pa_step.get("group_number")
            instruction = pa_step.get("instruction", "")
            tags = pa_step.get("tags", [])
            seg_ids = pa_step.get("segments", [])

            # Compute time range from segments
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

            # Somatic analysis
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

            # Language analysis
            lang = lang_lookup.get(sn)
            lang_dict = {}
            if lang:
                lang_dict = {
                    k: lang.get(k, "")
                    for k in ["explanatory_language", "attention_cues", "effort_language",
                              "image_language", "distinctive_phrasing"]
                }
            # sensory_inquiry comes from somatic pass but belongs in language display
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


@app.post("/api/pass0b-condensed")
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
        # Run somatic + language analyses (will hit cache if already run)
        somatic_result, lang_result = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(run_pass_0b, text, prov),
                asyncio.to_thread(run_pass_0b_lang, text, prov),
            ),
            timeout=ANALYSIS_TIMEOUT,
        )

        # Run condensed synthesis (optionally with a different provider)
        condensed_result = await asyncio.wait_for(
            asyncio.to_thread(run_pass_0b_condensed, somatic_result, lang_result, text, cond_prov, skip_cache=no_cache, hint=user_hint),
            timeout=ANALYSIS_TIMEOUT,
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


def assemble_chat_context(
    lesson_text: str,
    context_config: dict,
    somatic_result: dict | None = None,
    lang_result: dict | None = None,
    condensed_result: dict | None = None,
) -> str:
    """Assemble system prompt for lesson chat from selected context layers."""
    parts = [
        "You are a knowledgeable Feldenkrais practitioner having a conversation about this lesson. "
        "Answer questions using the analysis context provided below. Be specific and grounded "
        "in what the analysis reveals about this particular lesson. "
        "Keep responses concise — aim for 2-4 short paragraphs unless the user asks for more detail."
    ]

    if context_config.get("lesson_text") and lesson_text:
        parts.append("\n\n## Original Lesson Text\n" + lesson_text)

    if somatic_result:
        pass_a = somatic_result.get("pass_a", {})
        groups = pass_a.get("groups", [])

        if context_config.get("pass_a") and groups:
            # Build step lookup — pass_a["steps"] has full step dicts
            step_lookup = {}
            for s in pass_a.get("steps", []):
                if isinstance(s, dict):
                    step_lookup[s.get("step_number")] = s

            lines = ["\n\n## Lesson Structure (Pass A)"]
            for g in groups:
                lines.append(f"\n### Group {g.get('group_number', '?')}: {g.get('label', '')}")
                lines.append(g.get("description", ""))
                for step_ref in g.get("steps", []):
                    # steps in groups are just step numbers (ints)
                    sn = step_ref if isinstance(step_ref, int) else step_ref.get("step_number", "?") if isinstance(step_ref, dict) else "?"
                    step_data = step_lookup.get(sn, {})
                    instruction = step_data.get("instruction", "")
                    lines.append(f"- Step {sn}: {instruction}")
            parts.append("\n".join(lines))

        somatic_steps = somatic_result.get("enriched_steps", [])
        if context_config.get("somatic") and somatic_steps:
            lines = ["\n\n## Somatic Enrichment"]
            for step in somatic_steps:
                lines.append(f"\n**Step {step.get('step_number', '?')}**")
                for field in ["differentiation", "activation", "inhibition", "transmission_route", "ground_push"]:
                    val = step.get(field, "")
                    if val and str(val).lower() != "none":
                        lines.append(f"- {field}: {val}")
            parts.append("\n".join(lines))

        somatic_summary = somatic_result.get("summary", {})
        if context_config.get("somatic_summary") and somatic_summary:
            lines = ["\n\n## Somatic Architecture Summary"]
            for key, val in somatic_summary.items():
                if val and str(val).lower() != "none":
                    lines.append(f"- **{key}**: {val}")
            parts.append("\n".join(lines))

    if lang_result:
        lang_steps = lang_result.get("enriched_steps", [])
        if context_config.get("language") and lang_steps:
            lines = ["\n\n## Language Enrichment"]
            for step in lang_steps:
                lines.append(f"\n**Step {step.get('step_number', '?')}**")
                for field in ["pedagogical_mode", "teacher_strategy", "sensory_channel", "sensory_inquiry"]:
                    val = step.get(field, "")
                    if val and str(val).lower() != "none":
                        lines.append(f"- {field}: {val}")
            parts.append("\n".join(lines))

        lang_summary = lang_result.get("summary", {})
        if context_config.get("language_summary") and lang_summary:
            lines = ["\n\n## Language Summary"]
            for key, val in lang_summary.items():
                if val and str(val).lower() != "none":
                    lines.append(f"- **{key}**: {val}")
            parts.append("\n".join(lines))

    if context_config.get("condensed") and condensed_result:
        segments = condensed_result.get("segments", [])
        if segments:
            lines = ["\n\n## Condensed Cards"]
            for seg in segments:
                setting = seg.get("setting", {})
                if setting:
                    lines.append(f"\n### Setting: {setting.get('title', '')}")
                    if setting.get("somatic_intent"):
                        lines.append(f"*{setting['somatic_intent']}*")
                    for attr in ["space", "teacher_presence", "arc"]:
                        val = setting.get(attr, "")
                        if val:
                            lines.append(f"- {attr}: {val}")
                for card in seg.get("group_cards", []):
                    lines.append(f"\n### Group {card.get('group_number', '?')}: {card.get('group_label', '')}")
                    lines.append(f"**{card.get('title', '')}**")
                    if card.get("the_action"):
                        lines.append(f"Action: {card['the_action']}")
                    for attr in ["the_body", "the_attention", "the_intention", "the_voice"]:
                        val = card.get(attr, "")
                        if val and val.lower() != "none":
                            lines.append(f"- {attr.replace('the_', '')}: {val}")
            parts.append("\n".join(lines))

    return "\n".join(parts)


@app.post("/api/lesson-chat")
async def api_lesson_chat(request: Request):
    """Lesson chat endpoint — converse with the lesson using analysis context."""
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

    # Somatic (somatic_v2)
    som_suffix = ":somatic_v2"
    if provider_key and provider_key != DEFAULT_LLM:
        som_suffix += f":{provider_key}"
    som_cached = load_cached_analysis(get_cache_key(lesson_text + som_suffix))
    if som_cached:
        # Load pass_a separately (cached with :pass_a suffix)
        pass_a_cached = load_cached_analysis(get_cache_key(lesson_text + ":pass_a"))
        somatic_result = {
            "pass_a": pass_a_cached or {},
            "enriched_steps": som_cached.get("enriched_steps", []),
            "summary": som_cached.get("summary", {}),
        }

    # Language (language_v1)
    lang_suffix = ":language_v1"
    if provider_key and provider_key != DEFAULT_LLM:
        lang_suffix += f":{provider_key}"
    lang_cached = load_cached_analysis(get_cache_key(lesson_text + lang_suffix))
    if lang_cached:
        lang_result = {
            "enriched_steps": lang_cached.get("enriched_steps", []),
            "summary": lang_cached.get("summary", {}),
        }

    # Condensed (condense_v4)
    cond_suffix = ":condense_v4"
    if provider_key and provider_key != DEFAULT_LLM:
        cond_suffix += f":{provider_key}"
    cond_cached = load_cached_analysis(get_cache_key(lesson_text + cond_suffix))
    if cond_cached:
        condensed_result = {"segments": cond_cached.get("segments", [])}

    system_prompt = assemble_chat_context(
        lesson_text, context_config,
        somatic_result=somatic_result,
        lang_result=lang_result,
        condensed_result=condensed_result,
    )

    # Call LLM with chat messages
    try:
        llm = get_llm(provider_key)
        chat_messages = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant")]

        response_text = await asyncio.to_thread(
            llm.chat, chat_messages, system=system_prompt,
        )

        return JSONResponse({"response": response_text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/pass0b-player", response_class=HTMLResponse)
async def pass0b_player_page(request: Request):
    """Pass 0b audio player page."""
    _set_source_access(request)
    return build_pass0b_player_html(search_mode=_is_search_site(request), get_lessons_fn=get_available_lessons)


# --- Pass 0b-lang: Language Analysis ---

@app.get("/pass0b-lang", response_class=HTMLResponse)
async def pass0b_lang_page(request: Request):
    """Pass 0b-lang language analysis page."""
    _set_source_access(request)
    return build_pass0b_lang_html(search_mode=_is_search_site(request), get_lessons_fn=get_available_lessons)


@app.get("/lesson/{document_id}", response_class=HTMLResponse)
async def lesson_detail_page(request: Request, document_id: str):
    """Unified lesson detail page."""
    user = _set_source_access(request)
    if _db and not _db.is_document_accessible(document_id):
        return HTMLResponse("<h1>Not Found</h1>", status_code=404)
    return HTMLResponse(build_lesson_page_html(document_id, user=user, search_mode=_is_search_site(request), superuser_active=_is_superuser_active(request, user)))


@app.post("/api/pass0b-lang/clear-cache")
async def api_pass0b_lang_clear_cache():
    """Clear Pass 0b-lang cache entries."""
    if not _db:
        return JSONResponse({"error": "No database connection"}, status_code=500)

    rows = _db.list_cache()
    keys = []
    for r in rows:
        k = r["key"]
        # Match keys that contain the lang cache suffix
        # Cache keys are hashes, so we need to check the data
        try:
            data = _db.get_cache(k)
        except Exception:
            continue
        if not data:
            continue
        # Lang entries have "summary" alongside "enriched_steps"
        if "enriched_steps" in data and "summary" in data:
            keys.append(k)
    if keys:
        with _db.get_cursor() as cur:
            for k in keys:
                cur.execute("DELETE FROM cache WHERE key = %s", (k,))
    return JSONResponse({"deleted": len(keys)})


@app.post("/api/pass0b-lang")
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
            asyncio.to_thread(run_pass_0b_lang, text, prov, debug_log),
            timeout=ANALYSIS_TIMEOUT,
        )
        # Align with whisper timestamps for step-level timing
        whisper_segments = load_whisper_segments(selected_category, selected_lesson)
        if whisper_segments:
            align_pass0_with_whisper(result.get("pass_a", {}).get("pass_0", {}), whisper_segments)
        result_html = render_pass0b_lang_result(result)
        debug_html = render_debug_log(debug_log) if debug_log else ""
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
        debug_html = render_debug_log(debug_log) if debug_log else ""
        return JSONResponse({
            "error": str(e),
            "debug_html": debug_html,
        }, status_code=500)


def _render_step_bullets(e: dict, html_module) -> str:
    """Render a single enriched step as clarified text + bullet points."""
    esc = html_module.escape
    parts = []

    # Clarified text as the step body
    cl = e.get("clarified", "")
    if cl:
        parts.append(f'<div style="margin-bottom: 6px;">{esc(cl)}</div>')

    # Bullet list of enrichment fields — ordered abstract-to-concrete
    bullets = []
    intent = e.get("intent", "")
    if intent:
        bullets.append(f"<strong>Intent:</strong> {esc(intent)}")

    diff = e.get("differentiation", "")
    if diff and diff.lower() != "none":
        bullets.append(f"<strong>Differentiation:</strong> {esc(diff)}")

    act = e.get("activation", "")
    if act and act.lower() != "none":
        bullets.append(f"<strong>Activation:</strong> {esc(act)}")

    inhib = e.get("inhibition", "")
    if inhib and inhib.lower() != "none":
        bullets.append(f"<strong>Inhibition:</strong> {esc(inhib)}")

    tr = e.get("transmission_route", "")
    if tr and tr.lower() != "none":
        bullets.append(f"<strong>Transmission Route:</strong> {esc(tr)}")

    gp = e.get("ground_push", "")
    if gp and gp.lower() != "none":
        bullets.append(f"<strong>Ground Push:</strong> {esc(gp)}")

    md = e.get("mechanical_directive", "")
    if md and not md.lower().startswith("none"):
        bullets.append(f"<strong>Mechanical Directive:</strong> {esc(md)}")

    qc = e.get("attentional_cue", "")
    if qc and qc.lower() != "none":
        bullets.append(f"<strong>Attentional Cue:</strong> {esc(qc)}")

    si = e.get("sensory_inquiry", "")
    if si:
        bullets.append(f"<strong>Sensory Inquiry:</strong> {esc(si)}")

    if bullets:
        items = "".join(f"<li>{b}</li>" for b in bullets)
        parts.append(f'<ul style="margin: 4px 0 0 0; padding-left: 20px; font-size: 0.9em; '
                     f'color: #444;">{items}</ul>')

    return "\n".join(parts)


def _render_original_segments(pa_step: dict, seg_lookup: dict, html_module) -> str:
    """Render a collapsible <details> with the original transcript segments for a step."""
    seg_ids = pa_step.get("segments", [])
    if not seg_ids:
        return ""
    esc = html_module.escape
    n = len(seg_ids)
    label = f"Original text ({n} segment{'s' if n != 1 else ''})"
    lines = []
    for sid in seg_ids:
        text = seg_lookup.get(sid, "")
        if text:
            lines.append(f'<div style="margin: 2px 0;"><span style="color: #999; font-size: 0.85em;">[{sid}]</span> {esc(text)}</div>')
    if not lines:
        return ""
    return (
        f'<details style="margin-top: 6px;">'
        f'<summary style="cursor: pointer; font-size: 0.85em; color: #888;">{label}</summary>'
        f'<div style="margin-top: 4px; padding: 6px 10px; background: #fafafa; '
        f'border-radius: 4px; font-size: 0.85em; color: #555;">{"".join(lines)}</div>'
        f'</details>'
    )


def render_pass0b_result(result: dict) -> str:
    """Render Pass A + Pass 0b results as grouped steps with bullet-point enrichment."""
    import html as html_module
    pass_a = result.get("pass_a", {})
    groups = pass_a.get("groups", [])
    pa_steps = pass_a.get("steps", [])
    enriched = result.get("enriched_steps", [])

    enriched_lookup = {e["step_number"]: e for e in enriched}
    pa_step_lookup = {s["step_number"]: s for s in pa_steps}

    # Build segment text and time lookups from Pass 0
    pass_0 = pass_a.get("pass_0", {})
    seg_lookup = {s["id"]: s["text"] for s in pass_0.get("segments", [])}
    seg_time_lookup = {s["id"]: (s.get("time_start"), s.get("time_end"))
                       for s in pass_0.get("segments", [])
                       if s.get("time_start") is not None}

    def _step_time_str(pa_step):
        """Get formatted time range for a step from its segments."""
        seg_ids = pa_step.get("segments", [])
        if not seg_ids or not seg_time_lookup:
            return ""
        starts = [seg_time_lookup[sid][0] for sid in seg_ids if sid in seg_time_lookup]
        ends = [seg_time_lookup[sid][1] for sid in seg_ids if sid in seg_time_lookup]
        if not starts or not ends:
            return ""
        return f" ({format_timestamp(min(starts))} – {format_timestamp(max(ends))})"

    # Count totals
    total_pa_steps = len(pa_steps)
    total_enriched = len(enriched)
    irrelevant = sum(1 for e in enriched if not e.get("relevance", True))
    relevant = total_enriched - irrelevant
    num_groups = len(groups)
    missing_count = total_pa_steps - total_enriched

    out = []

    # Segment gap warning (Pass A dropped segments)
    segment_gaps = pass_a.get("segment_gaps", [])
    if segment_gaps:
        gap_str = ", ".join(str(s) for s in segment_gaps[:30])
        if len(segment_gaps) > 30:
            gap_str += f"... ({len(segment_gaps)} total)"
        out.append(
            f'<div style="margin-bottom: 12px; padding: 10px 14px; '
            f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            f'color: #856404;">'
            f'<strong>Warning — Pass A segment gaps:</strong> '
            f'{len(segment_gaps)} transcript segments were not assigned to any step: '
            f'[{html_module.escape(gap_str)}]. '
            f'Clear the Pass A cache and re-run.'
            f'</div>'
        )

    # Truncation warning
    truncation_warning = result.get("truncation_warning")
    if missing_count > 0 or truncation_warning:
        warning_msg = truncation_warning or (
            f"LLM returned {total_enriched} of {total_pa_steps} steps "
            f"({missing_count} steps missing)."
        )
        out.append(
            f'<div style="margin-bottom: 12px; padding: 10px 14px; '
            f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            f'color: #856404;">'
            f'<strong>Warning — Incomplete output:</strong> {html_module.escape(warning_msg)} '
            f'Clear the 0b cache and re-run to retry.'
            f'</div>'
        )

    out.append(f'<p style="color: #666; font-size: 0.9em;">{total_enriched} of {total_pa_steps} steps enriched across '
               f'{num_groups} groups, {relevant} lesson-relevant, {irrelevant} omitted'
               f'{f", {missing_count} missing" if missing_count > 0 else ""}</p>')

    # Render by group
    rendered_steps = set()
    for g in groups:
        gnum = g.get("group_number", 0)
        label = g.get("label", "")
        desc = g.get("description", "")

        group_step_nums = g.get("steps", [])
        group_relevant = [sn for sn in group_step_nums
                          if enriched_lookup.get(sn, {}).get("relevance", True)]
        if not group_relevant:
            for sn in group_step_nums:
                rendered_steps.add(sn)
            continue

        # Group header
        out.append(
            f'<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            f'background: #e8f0fe; border-left: 4px solid #1a73e8; border-radius: 4px;">'
            f'<strong style="color: #1a73e8;">Group {gnum}: {html_module.escape(label)}</strong>'
        )
        if desc:
            out.append(f'<div style="color: #555; font-size: 0.85em; margin-top: 4px;">'
                        f'{html_module.escape(desc)}</div>')
        out.append('</div>')

        for sn in group_step_nums:
            rendered_steps.add(sn)
            e = enriched_lookup.get(sn)
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")

            if e is None:
                # Step exists in Pass A but missing from LLM output (truncation)
                time_str = _step_time_str(pa_step)
                orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
                out.append(
                    f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                    f'background: #fce4ec; border-radius: 4px; border: 1px solid #ef9a9a;">'
                    f'<strong>Step {sn}</strong>'
                    f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                    f' &mdash; <em>{html_module.escape(instruction)}</em>'
                    f'<div style="color: #c62828; font-size: 0.85em; margin-top: 4px;">'
                    f'Not enriched (LLM output truncated)</div>'
                    f'{orig_html}'
                    f'</div>'
                )
                continue

            if not e.get("relevance", True):
                continue

            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                f' &mdash; <em>{html_module.escape(instruction)}</em>'
                f'<div style="margin-top: 6px;">{_render_step_bullets(e, html_module)}</div>'
                f'{orig_html}'
                f'</div>'
            )

    # Render any ungrouped steps
    ungrouped = [e for e in enriched if e["step_number"] not in rendered_steps]
    if ungrouped:
        out.append(
            '<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            'background: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px;">'
            '<strong style="color: #856404;">Ungrouped Steps</strong></div>'
        )
        for e in ungrouped:
            if not e.get("relevance", True):
                continue
            sn = e["step_number"]
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")
            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                f' &mdash; <em>{html_module.escape(instruction)}</em>'
                f'<div style="margin-top: 6px;">{_render_step_bullets(e, html_module)}</div>'
                f'{orig_html}'
                f'</div>'
            )

    return "\n".join(out)


def _render_lang_step_bullets(e: dict, html_module) -> str:
    """Render bullet list of language extraction fields for a step.

    Ordered abstract-to-concrete: explanatory first, literal quotations last.
    """
    esc = html_module.escape
    bullets = []

    el = e.get("explanatory_language", "")
    if el and el.lower() != "none":
        bullets.append(f"<strong>Explanatory:</strong> {esc(el)}")

    ac = e.get("attention_cues", "")
    if ac and ac.lower() != "none":
        bullets.append(f"<strong>Attention Cues:</strong> {esc(ac)}")

    ef = e.get("effort_language", "")
    if ef and ef.lower() != "none":
        bullets.append(f"<strong>Effort:</strong> {esc(ef)}")

    il = e.get("image_language", "")
    if il and il.lower() != "none":
        bullets.append(f"<strong>Imagery:</strong> {esc(il)}")

    dp = e.get("distinctive_phrasing", "")
    if dp and dp.lower() != "none":
        bullets.append(f"<strong>Distinctive:</strong> {esc(dp)}")

    if bullets:
        items = "".join(f"<li>{b}</li>" for b in bullets)
        return (f'<ul style="margin: 4px 0 0 0; padding-left: 20px; font-size: 0.9em; '
                f'color: #444;">{items}</ul>')
    return ""


def _render_lang_summary(summary: dict, html_module) -> str:
    """Render the lesson-level language summary block."""
    esc = html_module.escape
    out = []
    out.append(
        '<div style="margin-bottom: 24px; padding: 16px 20px; '
        'background: #f0f7ff; border: 1px solid #b3d4fc; border-radius: 8px;">'
        '<h3 style="margin: 0 0 12px 0; color: #1a56db;">Lesson-Level Language Summary</h3>'
    )

    # Teaching voice summary
    tvs = summary.get("teaching_voice_summary", "")
    if tvs:
        out.append(
            f'<div style="margin-bottom: 14px; padding: 10px 14px; '
            f'background: white; border-radius: 6px; border-left: 3px solid #1a56db; '
            f'font-style: italic; color: #333;">{esc(tvs)}</div>'
        )

    # Speaker principles
    principles = summary.get("speaker_principles", [])
    if principles:
        out.append('<div style="margin-bottom: 12px;"><strong>Speaker Principles:</strong>')
        out.append('<ol style="margin: 6px 0 0 0; padding-left: 24px;">')
        for p in principles:
            out.append(f'<li style="margin-bottom: 6px;">{esc(p)}</li>')
        out.append('</ol></div>')

    # Signature vocabulary
    vocab = summary.get("signature_vocabulary", [])
    if vocab:
        out.append('<div style="margin-bottom: 12px;"><strong>Signature Vocabulary:</strong>')
        out.append('<ul style="margin: 6px 0 0 0; padding-left: 24px;">')
        for v in vocab:
            out.append(f'<li>{esc(v)}</li>')
        out.append('</ul></div>')

    # Dominant attention mode
    dsm = summary.get("dominant_attention_mode", "")
    if dsm:
        out.append(f'<div style="margin-bottom: 8px;"><strong>Dominant Attention Mode:</strong> {esc(dsm)}</div>')

    # Effort philosophy
    ep = summary.get("effort_philosophy", "")
    if ep:
        out.append(f'<div style="margin-bottom: 8px;"><strong>Effort Philosophy:</strong> {esc(ep)}</div>')

    out.append('</div>')
    return "\n".join(out)


def _render_somatic_summary(summary: dict, html_module) -> str:
    """Render the lesson-level somatic architecture summary block."""
    esc = html_module.escape
    out = []
    out.append(
        '<div style="margin-bottom: 24px; padding: 16px 20px; '
        'background: #f0fff4; border: 1px solid #a3d9a5; border-radius: 8px;">'
        '<h3 style="margin: 0 0 12px 0; color: #2d6a4f;">Lesson-Level Somatic Summary</h3>'
    )

    # Somatic architecture summary
    sas = summary.get("somatic_architecture_summary", "")
    if sas:
        out.append(
            f'<div style="margin-bottom: 14px; padding: 10px 14px; '
            f'background: white; border-radius: 6px; border-left: 3px solid #2d6a4f; '
            f'font-style: italic; color: #333;">{esc(sas)}</div>'
        )

    # Primary transmission routes
    routes = summary.get("primary_transmission_routes", [])
    if routes:
        out.append('<div style="margin-bottom: 12px;"><strong>Primary Transmission Routes:</strong>')
        out.append('<ul style="margin: 6px 0 0 0; padding-left: 24px;">')
        for r in routes:
            out.append(f'<li>{esc(r)}</li>')
        out.append('</ul></div>')

    # Differentiation strategy
    ds = summary.get("differentiation_strategy", "")
    if ds:
        out.append(f'<div style="margin-bottom: 8px;"><strong>Differentiation Strategy:</strong> {esc(ds)}</div>')

    # Key inhibitions
    inhib = summary.get("key_inhibitions", [])
    if inhib:
        out.append('<div style="margin-bottom: 12px;"><strong>Key Inhibitions:</strong>')
        out.append('<ul style="margin: 6px 0 0 0; padding-left: 24px;">')
        for i in inhib:
            out.append(f'<li>{esc(i)}</li>')
        out.append('</ul></div>')

    # Ground force strategy
    gfs = summary.get("ground_force_strategy", "")
    if gfs:
        out.append(f'<div style="margin-bottom: 8px;"><strong>Ground Force Strategy:</strong> {esc(gfs)}</div>')

    out.append('</div>')
    return "\n".join(out)


def render_pass0b_lang_result(result: dict) -> str:
    """Render Pass A + Pass 0b-lang results as summary + grouped steps with language bullets."""
    import html as html_module
    pass_a = result.get("pass_a", {})
    groups = pass_a.get("groups", [])
    pa_steps = pass_a.get("steps", [])
    enriched = result.get("enriched_steps", [])
    summary = result.get("summary", {})

    enriched_lookup = {e["step_number"]: e for e in enriched}
    pa_step_lookup = {s["step_number"]: s for s in pa_steps}

    # Build segment text and time lookups from Pass 0
    pass_0 = pass_a.get("pass_0", {})
    seg_lookup = {s["id"]: s["text"] for s in pass_0.get("segments", [])}
    seg_time_lookup = {s["id"]: (s.get("time_start"), s.get("time_end"))
                       for s in pass_0.get("segments", [])
                       if s.get("time_start") is not None}

    def _step_time_str(pa_step):
        seg_ids = pa_step.get("segments", [])
        if not seg_ids or not seg_time_lookup:
            return ""
        starts = [seg_time_lookup[sid][0] for sid in seg_ids if sid in seg_time_lookup]
        ends = [seg_time_lookup[sid][1] for sid in seg_ids if sid in seg_time_lookup]
        if not starts or not ends:
            return ""
        return f" ({format_timestamp(min(starts))} – {format_timestamp(max(ends))})"

    total_pa_steps = len(pa_steps)
    total_enriched = len(enriched)
    num_groups = len(groups)
    missing_count = total_pa_steps - total_enriched

    out = []

    # Lesson-level summary at the top
    if summary:
        out.append(_render_lang_summary(summary, html_module))

    # Truncation warning
    truncation_warning = result.get("truncation_warning")
    if missing_count > 0 or truncation_warning:
        warning_msg = truncation_warning or (
            f"LLM returned {total_enriched} of {total_pa_steps} steps "
            f"({missing_count} steps missing)."
        )
        out.append(
            f'<div style="margin-bottom: 12px; padding: 10px 14px; '
            f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            f'color: #856404;">'
            f'<strong>Warning — Incomplete output:</strong> {html_module.escape(warning_msg)} '
            f'Clear the cache and re-run to retry.'
            f'</div>'
        )

    out.append(f'<p style="color: #666; font-size: 0.9em;">{total_enriched} of {total_pa_steps} steps analyzed across '
               f'{num_groups} groups'
               f'{f", {missing_count} missing" if missing_count > 0 else ""}</p>')

    # Render by group
    rendered_steps = set()
    for g in groups:
        gnum = g.get("group_number", 0)
        label = g.get("label", "")
        desc = g.get("description", "")
        group_step_nums = g.get("steps", [])

        # Group header
        out.append(
            f'<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            f'background: #e8f0fe; border-left: 4px solid #1a73e8; border-radius: 4px;">'
            f'<strong style="color: #1a73e8;">Group {gnum}: {html_module.escape(label)}</strong>'
        )
        if desc:
            out.append(f'<div style="color: #555; font-size: 0.85em; margin-top: 4px;">'
                        f'{html_module.escape(desc)}</div>')
        out.append('</div>')

        for sn in group_step_nums:
            rendered_steps.add(sn)
            e = enriched_lookup.get(sn)
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")

            if e is None:
                time_str = _step_time_str(pa_step)
                orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
                out.append(
                    f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                    f'background: #fce4ec; border-radius: 4px; border: 1px solid #ef9a9a;">'
                    f'<strong>Step {sn}</strong>'
                    f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                    f' &mdash; <em>{html_module.escape(instruction)}</em>'
                    f'<div style="color: #c62828; font-size: 0.85em; margin-top: 4px;">'
                    f'Not analyzed (LLM output truncated)</div>'
                    f'{orig_html}'
                    f'</div>'
                )
                continue

            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                f' &mdash; <em>{html_module.escape(instruction)}</em>'
                f'<div style="margin-top: 6px;">{_render_lang_step_bullets(e, html_module)}</div>'
                f'{orig_html}'
                f'</div>'
            )

    # Render any ungrouped steps
    ungrouped = [e for e in enriched if e["step_number"] not in rendered_steps]
    if ungrouped:
        out.append(
            '<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            'background: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px;">'
            '<strong style="color: #856404;">Ungrouped Steps</strong></div>'
        )
        for e in ungrouped:
            sn = e["step_number"]
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")
            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{html_module.escape(time_str)}</span>'
                f' &mdash; <em>{html_module.escape(instruction)}</em>'
                f'<div style="margin-top: 6px;">{_render_lang_step_bullets(e, html_module)}</div>'
                f'{orig_html}'
                f'</div>'
            )

    return "\n".join(out)


def render_pass0b_combined_result(somatic_result: dict, lang_result: dict) -> str:
    """Render merged somatic + language results as a single step list.

    Language summary at top, then each step shows somatic bullets followed by language bullets.
    """
    import html as html_module
    esc = html_module.escape

    pass_a = somatic_result.get("pass_a", {})
    groups = pass_a.get("groups", [])
    pa_steps = pass_a.get("steps", [])

    somatic_enriched = somatic_result.get("enriched_steps", [])
    lang_enriched = lang_result.get("enriched_steps", [])
    lang_summary = lang_result.get("summary", {})
    somatic_summary = somatic_result.get("summary", {})

    somatic_lookup = {e["step_number"]: e for e in somatic_enriched}
    lang_lookup = {e["step_number"]: e for e in lang_enriched}
    pa_step_lookup = {s["step_number"]: s for s in pa_steps}

    # Build segment text and time lookups from Pass 0
    pass_0 = pass_a.get("pass_0", {})
    seg_lookup = {s["id"]: s["text"] for s in pass_0.get("segments", [])}
    seg_time_lookup = {s["id"]: (s.get("time_start"), s.get("time_end"))
                       for s in pass_0.get("segments", [])
                       if s.get("time_start") is not None}

    def _step_time_str(pa_step):
        seg_ids = pa_step.get("segments", [])
        if not seg_ids or not seg_time_lookup:
            return ""
        starts = [seg_time_lookup[sid][0] for sid in seg_ids if sid in seg_time_lookup]
        ends = [seg_time_lookup[sid][1] for sid in seg_ids if sid in seg_time_lookup]
        if not starts or not ends:
            return ""
        return f" ({format_timestamp(min(starts))} – {format_timestamp(max(ends))})"

    total_pa_steps = len(pa_steps)
    total_somatic = len(somatic_enriched)
    total_lang = len(lang_enriched)
    num_groups = len(groups)

    out = []

    # Summaries at top
    if somatic_summary:
        out.append(_render_somatic_summary(somatic_summary, html_module))
    if lang_summary:
        out.append(_render_lang_summary(lang_summary, html_module))

    # Warnings
    for label, total in [("Somatic", total_somatic), ("Language", total_lang)]:
        missing = total_pa_steps - total
        if missing > 0:
            out.append(
                f'<div style="margin-bottom: 12px; padding: 10px 14px; '
                f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
                f'color: #856404;">'
                f'<strong>Warning — {label}:</strong> {total} of {total_pa_steps} steps '
                f'({missing} missing). Clear cache and re-run.'
                f'</div>'
            )

    somatic_warning = somatic_result.get("truncation_warning")
    if somatic_warning:
        out.append(
            f'<div style="margin-bottom: 12px; padding: 10px 14px; '
            f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            f'color: #856404;">'
            f'<strong>Warning — Somatic:</strong> {esc(somatic_warning)}'
            f'</div>'
        )

    # Segment gap warning
    segment_gaps = pass_a.get("segment_gaps", [])
    if segment_gaps:
        gap_str = ", ".join(str(s) for s in segment_gaps[:30])
        if len(segment_gaps) > 30:
            gap_str += f"... ({len(segment_gaps)} total)"
        out.append(
            f'<div style="margin-bottom: 12px; padding: 10px 14px; '
            f'background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            f'color: #856404;">'
            f'<strong>Warning — Pass A segment gaps:</strong> '
            f'{len(segment_gaps)} transcript segments not assigned to any step: '
            f'[{esc(gap_str)}].'
            f'</div>'
        )

    irrelevant = sum(1 for e in somatic_enriched if not e.get("relevance", True))
    relevant = total_somatic - irrelevant
    out.append(f'<p style="color: #666; font-size: 0.9em;">{total_pa_steps} steps across '
               f'{num_groups} groups, {relevant} lesson-relevant, {irrelevant} omitted</p>')

    # Render by group
    rendered_steps = set()
    for g in groups:
        gnum = g.get("group_number", 0)
        label = g.get("label", "")
        desc = g.get("description", "")
        group_step_nums = g.get("steps", [])

        group_relevant = [sn for sn in group_step_nums
                          if somatic_lookup.get(sn, {}).get("relevance", True)]
        if not group_relevant:
            for sn in group_step_nums:
                rendered_steps.add(sn)
            continue

        # Group header
        out.append(
            f'<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            f'background: #e8f0fe; border-left: 4px solid #1a73e8; border-radius: 4px;">'
            f'<strong style="color: #1a73e8;">Group {gnum}: {esc(label)}</strong>'
        )
        if desc:
            out.append(f'<div style="color: #555; font-size: 0.85em; margin-top: 4px;">'
                        f'{esc(desc)}</div>')
        out.append('</div>')

        for sn in group_step_nums:
            rendered_steps.add(sn)
            som = somatic_lookup.get(sn)
            lang = lang_lookup.get(sn)
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")
            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)

            if som is None and lang is None:
                out.append(
                    f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                    f'background: #fce4ec; border-radius: 4px; border: 1px solid #ef9a9a;">'
                    f'<strong>Step {sn}</strong>'
                    f'<span style="color: #888; font-size: 0.85em;">{esc(time_str)}</span>'
                    f' &mdash; <em>{esc(instruction)}</em>'
                    f'<div style="color: #c62828; font-size: 0.85em; margin-top: 4px;">'
                    f'Not enriched (LLM output truncated)</div>'
                    f'{orig_html}'
                    f'</div>'
                )
                continue

            # Skip irrelevant steps (somatic pass marks relevance)
            if som and not som.get("relevance", True):
                continue

            # Build merged bullet content
            parts = []
            if som:
                somatic_html = _render_step_bullets(som, html_module)
                if somatic_html:
                    parts.append(somatic_html)
            if lang:
                lang_html = _render_lang_step_bullets(lang, html_module)
                if lang_html:
                    parts.append(lang_html)

            merged = "\n".join(parts)

            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{esc(time_str)}</span>'
                f' &mdash; <em>{esc(instruction)}</em>'
                f'<div style="margin-top: 6px;">{merged}</div>'
                f'{orig_html}'
                f'</div>'
            )

    # Ungrouped steps
    ungrouped_nums = [s["step_number"] for s in pa_steps if s["step_number"] not in rendered_steps]
    if ungrouped_nums:
        out.append(
            '<div style="margin-top: 24px; margin-bottom: 8px; padding: 8px 12px; '
            'background: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px;">'
            '<strong style="color: #856404;">Ungrouped Steps</strong></div>'
        )
        for sn in ungrouped_nums:
            som = somatic_lookup.get(sn)
            lang = lang_lookup.get(sn)
            if som and not som.get("relevance", True):
                continue
            pa_step = pa_step_lookup.get(sn, {})
            instruction = pa_step.get("instruction", "")
            time_str = _step_time_str(pa_step)
            orig_html = _render_original_segments(pa_step, seg_lookup, html_module)
            parts = []
            if som:
                somatic_html = _render_step_bullets(som, html_module)
                if somatic_html:
                    parts.append(somatic_html)
            if lang:
                lang_html = _render_lang_step_bullets(lang, html_module)
                if lang_html:
                    parts.append(lang_html)
            merged = "\n".join(parts)
            out.append(
                f'<div style="margin: 12px 0 4px 0; padding: 8px 12px; '
                f'background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;">'
                f'<strong>Step {sn}</strong>'
                f'<span style="color: #888; font-size: 0.85em;">{esc(time_str)}</span>'
                f' &mdash; <em>{esc(instruction)}</em>'
                f'<div style="margin-top: 6px;">{merged}</div>'
                f'{orig_html}'
                f'</div>'
            )

    return "\n".join(out)


@app.get("/api/pipeline/ingestion")
async def pipeline_ingestion():
    """Return the ingestion tab content HTML for refresh."""
    if not _db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _build_ingestion_html()
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@app.post("/api/ingestion/resubmit/{document_id}")
async def resubmit_document(document_id: str):
    """Clear all pipeline state for a document so it gets reprocessed from scratch."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        cleared = _db.resubmit_document(document_id)
        _db.log_event("resubmit", document_id=document_id,
                       detail={"cleared": cleared})
        return JSONResponse({"status": "ok", "cleared": cleared})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ingestion/retry-all-failed")
async def retry_all_failed():
    """Resubmit all documents in 'failed' pipeline stage."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        documents = _db.get_document_pipeline_view(limit=500)
        failed = [d for d in documents if d.get("pipeline_stage") == "failed"]
        results = []
        for doc in failed:
            doc_id = str(doc["id"])
            cleared = _db.resubmit_document(doc_id)
            results.append({"document_id": doc_id, "title": doc.get("title"), "cleared": cleared})
        if results:
            _db.log_event("retry_all_failed", detail={"count": len(results),
                          "documents": [r["title"] for r in results]})
        return JSONResponse({"status": "ok", "count": len(results), "results": results})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)





# ── Analysis pipeline gate ────────────────────────────────────────────


@app.get("/analysis", response_class=HTMLResponse)
async def analysis_dashboard(request: Request):
    """Analysis pipeline dashboard with gate management."""
    if IS_LAMBDA and _auth_mod.get_access_tier(request) != "admin":
        return RedirectResponse("/")
    group_id = request.query_params.get("group")
    group_id = int(group_id) if group_id else None
    return _build_analysis_page(group_id)


@app.get("/api/analysis/dashboard")
async def analysis_dashboard_data():
    """Return group-level analysis dashboard HTML for AJAX refresh."""
    if not _db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _build_analysis_html()
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@app.get("/api/analysis/group/{group_id}")
async def analysis_group_data(group_id: int):
    """Return document drill-down HTML for a group."""
    if not _db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _build_analysis_group_html(group_id)
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@app.get("/api/analysis/status")
async def analysis_status_data():
    """Return pipeline progress + errors HTML for AJAX refresh."""
    if not _db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _build_pipeline_status_html()
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@app.post("/api/analysis/gate")
async def update_analysis_gate(request: Request):
    """Toggle permitted flag for a (group, step) pair."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    body = await request.json()
    group_id = body.get("group_id")
    step_name = body.get("step_name")
    permitted = body.get("permitted")
    if not group_id or not step_name or permitted is None:
        return JSONResponse({"error": "Missing group_id, step_name, or permitted"}, status_code=400)
    try:
        _db.set_group_gate(int(group_id), step_name, permitted)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/analysis/groups")
async def list_analysis_groups():
    """List all lesson groups as JSON."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        groups = _db.get_lesson_groups()
        return JSONResponse([{
            "id": g["id"], "name": g["name"],
            "doc_count": g["doc_count"],
            "auto_source_id": g.get("auto_source_id"),
        } for g in groups])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/analysis/groups")
async def create_analysis_group(request: Request):
    """Create a custom lesson group."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    body = await request.json()
    name = body.get("name")
    document_ids = body.get("document_ids", [])
    if not name:
        return JSONResponse({"error": "Missing name"}, status_code=400)
    try:
        group_id = _db.create_lesson_group(name, body.get("description"))
        if document_ids:
            _db.add_group_members(group_id, document_ids)
        return JSONResponse({"status": "ok", "group_id": group_id})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/analysis/run")
async def run_analysis_pipeline(request: Request):
    """Toggle the pipeline on/off. Non-blocking — runs in background thread."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)

    if _pipeline_is_running():
        # Stop — set DB flag, worker will notice and exit
        _pipeline_set_running(False)
        return JSONResponse({"status": "stopping"})
    else:
        # Start — set DB flag and launch worker thread
        _pipeline_set_running(True)
        t = threading.Thread(target=_pipeline_worker, daemon=True)
        t.start()
        return JSONResponse({"status": "started"})


@app.get("/api/analysis/pipeline-status")
async def pipeline_status():
    """Return whether the pipeline is currently running."""
    return JSONResponse({"running": _pipeline_is_running()})


@app.post("/api/analysis/clear-error")
async def clear_analysis_error(request: Request):
    """Clear the error on a document analysis state entry so it can be retried."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    body = await request.json()
    document_id = body.get("document_id")
    step_name = body.get("step_name")
    if not document_id or not step_name:
        return JSONResponse({"error": "Missing document_id or step_name"}, status_code=400)
    try:
        _db.clear_step_error(document_id, step_name)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/analysis/clear-all-errors")
async def clear_all_analysis_errors(request: Request):
    """Clear all errors so failed steps become runnable again."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        count = _db.clear_all_errors()
        return JSONResponse({"status": "ok", "cleared": count})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/admin/clear-cache")
async def admin_clear_cache(request: Request):
    """Clear all cached analysis results."""
    if IS_LAMBDA and _auth_mod.get_access_tier(request) != "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if _db:
        count = _db.clear_cache()
    else:
        count = 0
    return JSONResponse({"cleared": count})


@app.get("/admin/cache")
async def admin_cache_list(request: Request):
    """List cached entries in database."""
    if IS_LAMBDA and _auth_mod.get_access_tier(request) != "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if _db:
        entries = _db.list_cache()
        for e in entries:
            e["size_kb"] = round(e.pop("size_bytes", 0) / 1024, 1)
            # Convert datetimes to strings for JSON
            for k in ("created_at", "updated_at"):
                if e.get(k):
                    e[k] = str(e[k])
    else:
        entries = []
    return JSONResponse({"count": len(entries), "entries": entries})


@app.get("/version")
async def version():
    sha = os.environ.get("DEPLOY_SHA", "local")
    return {"sha": sha, "short": sha[:7] if len(sha) > 7 else sha}


# --- Conditional book router mount ---

_book_available = False
_private_data = Path(__file__).parent.parent.parent / "private" / "data"
_book_data_dir = _private_data if _private_data.exists() else DATA_DIR
try:
    from web_book import create_book_router
    _book_router = create_book_router(db=_db, data_dir=_book_data_dir)
    app.include_router(_book_router)
    # Expose the book pipeline section builder for build_pipeline_html()
    from web_book import _build_book_pipeline_section
    app.state.build_book_pipeline_section = _build_book_pipeline_section
    _book_available = True
except ImportError:
    pass


# --- Finder (external lesson search) ---

def _build_finder_html():
    """Build the finder landing page HTML."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Feldenkrais Lesson Finder</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #fafafa; color: #333; min-height: 100vh;
         display: flex; flex-direction: column; align-items: center; }}
  .hero {{ text-align: center; padding: 60px 20px 30px; max-width: 640px; }}
  .hero h1 {{ font-size: 1.8rem; font-weight: 600; margin-bottom: 8px; }}
  .hero p {{ color: #666; font-size: 1rem; margin-bottom: 30px; }}
  .search-box {{ display: flex; gap: 8px; max-width: 560px; width: 100%; margin: 0 auto; }}
  .search-box input {{ flex: 1; padding: 12px 16px; font-size: 1rem; border: 1px solid #ccc;
                       border-radius: 8px; outline: none; }}
  .search-box input:focus {{ border-color: #4a90d9; box-shadow: 0 0 0 2px rgba(74,144,217,0.2); }}
  .search-box button {{ padding: 12px 24px; font-size: 1rem; background: #4a90d9; color: #fff;
                        border: none; border-radius: 8px; cursor: pointer; font-weight: 500; }}
  .search-box button:hover {{ background: #3a7bc8; }}
  .results {{ max-width: 640px; width: 100%; padding: 20px; }}
  .result-card {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 8px;
                  padding: 16px; margin-bottom: 12px; }}
  .result-card h3 {{ font-size: 1rem; margin-bottom: 4px; }}
  .result-card h3 a {{ color: #2c5282; text-decoration: none; }}
  .result-card h3 a:hover {{ text-decoration: underline; }}
  .result-card .desc {{ color: #555; font-size: 0.9rem; margin-bottom: 8px; }}
  .result-card .meta {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .tag {{ background: #edf2f7; color: #4a5568; padding: 2px 8px; border-radius: 4px;
          font-size: 0.78rem; }}
  .source {{ color: #888; font-size: 0.82rem; }}
  .score {{ color: #aaa; font-size: 0.78rem; }}
  .empty {{ text-align: center; color: #999; padding: 40px 0; }}
  .loading {{ text-align: center; padding: 30px 0; color: #888; }}
  .stats {{ text-align: center; color: #aaa; font-size: 0.82rem; margin-top: 4px; }}
</style>
</head>
<body>
<div class="hero">
  <h1>Feldenkrais Lesson Finder</h1>
  <p>Search across hundreds of Feldenkrais lessons from around the web.</p>
  <div class="search-box">
    <input type="text" id="q" placeholder="e.g. rolling from back to sitting, or jaw and tongue..."
           autofocus autocomplete="off">
    <button onclick="doSearch()">Search</button>
  </div>
  <div class="stats" id="stats"></div>
</div>
<div class="results" id="results"></div>
<script>
const input = document.getElementById('q');
const results = document.getElementById('results');
const stats = document.getElementById('stats');

// Load stats on page load
fetch('/api/finder/stats').then(r=>r.json()).then(d=>{{
  stats.textContent = d.total + ' lessons indexed from ' + d.sources + ' sources';
}}).catch(()=>{{}});

input.addEventListener('keydown', e => {{ if (e.key === 'Enter') doSearch(); }});

async function doSearch() {{
  const q = input.value.trim();
  if (!q) return;
  results.innerHTML = '<div class="loading">Searching...</div>';
  try {{
    const resp = await fetch('/api/finder/search?q=' + encodeURIComponent(q));
    const data = await resp.json();
    if (!data.results || data.results.length === 0) {{
      results.innerHTML = '<div class="empty">No results found. Try different keywords.</div>';
      return;
    }}
    results.innerHTML = data.results.map(r => {{
      const tags = (r.tags || []).slice(0, 6).map(t => '<span class="tag">' + t + '</span>').join('');
      const desc = r.description ? '<div class="desc">' + r.description.slice(0, 200) +
                   (r.description.length > 200 ? '...' : '') + '</div>' : '';
      const link = r.url ? '<a href="' + r.url + '" target="_blank" rel="noopener">' + r.title + '</a>'
                         : r.title;
      const source = r.source_name ? '<span class="source">' + r.source_name + '</span>' : '';
      return '<div class="result-card"><h3>' + link + '</h3>' + desc +
             '<div class="meta">' + source + tags + '</div></div>';
    }}).join('');
  }} catch(e) {{
    results.innerHTML = '<div class="empty">Search failed. Please try again.</div>';
  }}
}}
</script>
</body>
</html>"""


@app.get("/api/finder/search")
async def finder_search(q: str, limit: int = 20):
    """Semantic search across external lessons."""
    # Check LLM kill switch
    settings = _db.get_cache("settings:llm_enabled") if _db else None
    if not settings or not settings.get("enabled", False):
        return JSONResponse({"error": "Search is temporarily disabled"}, status_code=503)
    import openai
    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "").strip())
    resp = client.embeddings.create(model="text-embedding-3-small", input=[q])
    vec = resp.data[0].embedding

    with _db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT el.title, el.url, el.description, el.tags, el.access,
                   el.content_type, ls.name as source_name,
                   1 - (el.embedding <=> %s::vector) as similarity
            FROM external_lesson el
            LEFT JOIN lesson_source ls ON ls.id = el.lesson_source_id
            WHERE el.embedding IS NOT NULL
            ORDER BY el.embedding <=> %s::vector
            LIMIT %s
        """, (str(vec), str(vec), limit))
        rows = cur.fetchall()

    return {
        "query": q,
        "results": [
            {
                "title": r["title"],
                "url": r["url"],
                "description": r["description"],
                "tags": r["tags"],
                "access": r["access"],
                "content_type": r["content_type"],
                "source_name": r["source_name"],
                "similarity": round(r["similarity"], 3),
            }
            for r in rows
        ],
    }


@app.get("/api/finder/stats")
async def finder_stats():
    """Return basic stats for the finder."""
    with _db.get_cursor(commit=False) as cur:
        cur.execute("SELECT COUNT(*) as total FROM external_lesson WHERE embedding IS NOT NULL")
        total = cur.fetchone()["total"]
        cur.execute("""
            SELECT COUNT(DISTINCT lesson_source_id) as sources
            FROM external_lesson WHERE embedding IS NOT NULL
        """)
        sources = cur.fetchone()["sources"]
    return {"total": total, "sources": sources}


# --- Landing page ---

@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Landing page: search home, plus dev tools for admin or local dev."""
    # Interiority subdomain serves the research booklet
    if _is_interiority_site(request):
        # Lambda: file is bundled at private/docs/interiority/index.html
        # Local: relative to repo root
        candidates = [
            Path(__file__).parent / "private" / "docs" / "interiority" / "index.html",
            Path(__file__).parent.parent.parent / "private" / "docs" / "interiority" / "index.html",
        ]
        for p in candidates:
            if p.exists():
                return HTMLResponse(p.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Coming soon</h1>", status_code=404)

    # Finder subdomain gets its own UI
    if _is_finder_site(request):
        return HTMLResponse(_build_finder_html())
    user = _set_source_access(request)
    search_mode = _is_search_site(request)
    su_active = _is_superuser_active(request, user)
    # Search subdomain: always show clean search page, no dev tools
    if search_mode:
        return HTMLResponse(build_search_home_html(user=user, superuser_active=su_active, get_lessons_fn=get_available_lessons))
    show_dev = not IS_LAMBDA or (user and user.get("tier") == "admin")
    if not show_dev:
        return HTMLResponse(build_search_home_html(user=user, superuser_active=su_active, get_lessons_fn=get_available_lessons))
    # Show dev tool links for admin or local dev
    links = []
    if _book_available:
        links.append('<li><a href="/book"><strong>Book</strong></a></li>')
        links.append('<li><a href="/analyzer">Analyzer</a></li>')
        links.append('<li><a href="/explore">Explore</a></li>')
    links.append('<li><a href="/ingestion">Ingestion Dashboard</a></li>')
    links.append('<li><a href="/analysis">Analysis Pipeline</a></li>')
    links.append('<li><a href="/pipeline">Pipeline Dashboard</a></li>')
    links.append('<li><a href="/pass0b">Pass 0b Analysis</a></li>')
    links.append('<li><a href="/pass0b-player">Pass 0b Player</a></li>')
    links.append('<li><a href="/pass0b-lang">Pass 0b Language</a></li>')
    dev_section = f"""<h2 style="margin-top:40px; border-top:1px solid #e0e0e0; padding-top:20px;">Dev Tools</h2>
<ul style="list-style:none; padding:0;">{"".join(links)}</ul>"""
    return HTMLResponse(build_search_home_html(extra_html=dev_section, user=user, superuser_active=su_active, get_lessons_fn=get_available_lessons))


# Lambda handler
from mangum import Mangum

# Create base handler for HTTP requests
_http_handler = Mangum(app, lifespan="off")


def handler(event, context):
    """
    Lambda handler that supports both:
    1. HTTP requests via API Gateway (normal web traffic)
    2. Async invocations for background job processing
    """
    # Check if this is an async job processing invocation
    if isinstance(event, dict) and event.get("action") == "process" and event.get("job_id"):
        job_id = event["job_id"]
        try:
            from web_book import process_job
            process_job(job_id)
        except ImportError:
            return {"statusCode": 501, "body": json.dumps({"error": "web_book not available"})}
        return {"statusCode": 200, "body": json.dumps({"processed": job_id})}

    # Otherwise, handle as HTTP request
    return _http_handler(event, context)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
