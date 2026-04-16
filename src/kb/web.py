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

# Allow `import web` (used by routes/* and book/state.py) to resolve to this
# module when this file is loaded as `kb.web` (e.g. via tests). Without this
# alias, Python would try to load src/kb/web.py a second time under the name
# `web`, causing a circular import with routes.auth.
if __name__ != "web" and "web" not in sys.modules:
    sys.modules["web"] = sys.modules[__name__]

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
    # Localhost gets full access (no filtering)
    host = request.headers.get("host", "")
    if host.startswith("localhost") or host.startswith("127.0.0.1"):
        _db.allowed_source_ids = None
        return _auth_mod.get_current_user(request)
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


# ── Pipeline runner lock (used by the dormant book pipeline if mounted) ──

_pipeline_run_lock = asyncio.Lock()


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


# ── Auth routes extracted to routes/auth.py ──────────────────────────

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
    build_search_home_html,
    build_lesson_page_html,
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


# ── Renderers (extracted to renderers.py) ──────────────────────────────
from renderers import (  # noqa: E402
    render_debug_log,
)

# ── HTML builders (extracted to html_builders.py) ──────────────────────
from html_builders import (  # noqa: E402
    _build_ingestion_html, _build_ingestion_page,
    _build_analysis_html, _build_analysis_group_html,
    _build_analysis_page, _build_pipeline_status_html,
    build_pipeline_html, _build_finder_html,
    _analysis_step_names, _STEP_DISPLAY_NAMES,
)

ANALYSIS_TIMEOUT = 900  # 15 minutes


# ── Search helpers ────────────────────────────────────────────────────

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

    # Always run all available searches
    default_hits = _db.search_lessons(embedding, limit=1000, method="default")
    concepts_hits = _db.search_lessons(embedding, limit=1000, method="concepts")
    positions_hits = _db.search_lessons(embedding, limit=1000, method="positions")

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
        # Ambiguous — RRF merge of default + concepts + positions
        POSITIONS_WEIGHT = 2.0
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

        for rank, hit in enumerate(positions_hits, 1):
            doc_id = hit["document_id"]
            scores[doc_id] = scores.get(doc_id, 0) + POSITIONS_WEIGHT / (RRF_K + rank)
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


# ── Search, pass0b, lesson routes extracted to routes/ ────────────────


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


# ── Include route modules ─────────────────────────────────────────────
from routes.auth import router as auth_router
from routes.ingestion import router as ingestion_router
from routes.analysis_routes import router as analysis_router
from routes.search import router as search_router
from routes.finder import router as finder_router
from routes.admin import router as admin_router
from routes.pages import router as pages_router
from routes.lesson_data import router as lesson_data_router

app.include_router(auth_router)
app.include_router(ingestion_router)
app.include_router(analysis_router)
app.include_router(search_router)
app.include_router(finder_router)
app.include_router(admin_router)
app.include_router(pages_router)
app.include_router(lesson_data_router)


# ── Conditional book router mount ─────────────────────────────────────
# The book pipeline is dormant — its code lives in the private submodule
# at private/products/book/src/. It mounts only when explicitly enabled
# via BOOK_PIPELINE_ENABLED=1 AND the private submodule is present.
# Production Lambda has neither, so the book routes are never exposed there.

_book_available = False
_project_root = Path(__file__).parent.parent.parent
_private_data = _project_root / "private" / "data"
_book_src = _project_root / "private" / "products" / "book" / "src"
if (
    os.environ.get("BOOK_PIPELINE_ENABLED") == "1"
    and _private_data.exists()
    and _book_src.exists()
):
    sys.path.insert(0, str(_book_src))
    try:
        from web_book import create_book_router
        _book_router = create_book_router(db=_db, data_dir=_private_data)
        app.include_router(_book_router)
        # Expose the book pipeline section builder for build_pipeline_html()
        from web_book import _build_book_pipeline_section
        app.state.build_book_pipeline_section = _build_book_pipeline_section
        _book_available = True
    except ImportError:
        pass


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
