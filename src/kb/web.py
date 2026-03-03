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

# Add private/src/ to sys.path so we can import modules from the submodule
_private_src = Path(__file__).parent.parent.parent / "private" / "src"
if _private_src.exists():
    sys.path.insert(0, str(_private_src))

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from llm import LLM
from storage import Storage

# ── Search subdomain detection ────────────────────────────────────────
SEARCH_DOMAIN = os.environ.get("SEARCH_DOMAIN", "")


def _is_search_site(request: Request) -> bool:
    """True when served from the search subdomain."""
    if not SEARCH_DOMAIN:
        return False
    host = request.headers.get("host", "")
    return host == SEARCH_DOMAIN or host.startswith(SEARCH_DOMAIN + ":")


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


@asynccontextmanager
async def _lifespan(app):
    if _db and not IS_LAMBDA:
        # Clear zombie RUNNING states from previous server run
        with _db.get_cursor() as cur:
            cur.execute(
                "UPDATE pipeline_node_state SET status = 'stale' WHERE status = 'running'"
            )
        print("[pipeline] on-demand mode (no background runner)")
    yield


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
    _db.public_only = IS_LAMBDA
    _db.initialize()

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



def get_available_lessons(public_only: bool = False) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    """Get categories and their display names.

    DB documents with category_name set are grouped under that name (override).
    DB documents without category_name are grouped under their source's category.

    Args:
        public_only: If True, only include sources marked as public.

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
            prev_public = _db.public_only
            _db.public_only = public_only
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
        finally:
            _db.public_only = prev_public

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

    # Count analyzed / embedded from pipeline_stage
    analyzed_count = sum(1 for d in documents if d.get("pipeline_stage") in ("analyzed", "embedded"))
    embedded_count = sum(1 for d in documents if d.get("pipeline_stage") == "embedded")

    # Summary cards
    doc_total = summary.get("documents", {}).get("total", 0)
    transcribed = summary.get("transcribed", 0)
    pending_docs = summary.get("documents", {}).get("pending", 0)
    jobs = summary.get("transcription_jobs", {})
    pending_jobs = jobs.get("pending", 0)
    failed_jobs = jobs.get("failed", 0)

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
            "embedded": ("Embedded", "#d4edda", "#155724"),
            "analyzed": ("Analyzed", "#cce5ff", "#004085"),
            "transcribed": ("Transcribed", "#d1ecf1", "#0c5460"),
            "transcribing": ("Transcribing", "#fff3cd", "#856404"),
            "failed": ("Failed", "#f8d7da", "#721c24"),
            "awaiting_transcription": ("New", "#e2e3e5", "#383d41"),
        }
        label, bg, fg = stage_styles.get(stage, ("Unknown", "#e2e3e5", "#383d41"))
        title_attr = f' title="{latest_error}"' if latest_error and stage == "failed" else ""
        status_badge = f'<span style="padding: 2px 8px; border-radius: 10px; font-size: 0.8em; background: {bg}; color: {fg};"{title_attr}>{label}</span>'

        doc_rows.append(f"""
            <tr>
                <td>{created}</td>
                <td>{title}</td>
                <td style="font-size: 0.85em;">{category}</td>
                <td>{status_badge}</td>
                <td style="text-align: center;">{doc.get("job_count", 0)}</td>
            </tr>
        """)

    events_html = "\n".join(event_rows) if event_rows else '<tr><td colspan="5" style="text-align: center; color: #999;">No events yet</td></tr>'
    docs_html = "\n".join(doc_rows) if doc_rows else '<tr><td colspan="5" style="text-align: center; color: #999;">No documents yet</td></tr>'

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
            <button id="auto-analyze-btn" style="padding: 4px 12px; border: 1px solid #28a745; background: white; color: #28a745; border-radius: 4px; cursor: pointer; font-size: 0.85em;">Auto-Analyze</button>
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
                <div class="value">{analyzed_count}</div>
                <div class="label">Analyzed</div>
            </div>
            <div class="card">
                <div class="value">{embedded_count}</div>
                <div class="label">Embedded</div>
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
                </tr>
            </thead>
            <tbody>
                {docs_html}
            </tbody>
        </table>
    """


def build_pipeline_html() -> str:
    """Build the pipeline dashboard HTML page."""
    if not _db:
        return """
<!DOCTYPE html><html><head><title>Pipeline Dashboard</title>
<style>body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }</style>
</head><body><h1>Pipeline Dashboard</h1><p>Database not configured. Set DATABASE_URL to enable.</p>
<p><a href="/">&larr; Home</a></p></body></html>"""

    try:
        ingestion_html = _build_ingestion_html()
    except Exception as e:
        ingestion_html = f'<div style="color: red;">Error loading ingestion data: {e}</div>'

    # Book pipeline section (injected by web_book router when available)
    book_pipeline_fn = getattr(app.state, 'build_book_pipeline_section', None)
    if book_pipeline_fn:
        try:
            book_pipeline_html = book_pipeline_fn()
        except Exception as e:
            book_pipeline_html = f'<p style="color: #999;">Book pipeline error: {e}</p>'
    else:
        book_pipeline_html = None
    has_book_tab = book_pipeline_html is not None

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
        .tabs {{
            display: flex;
            gap: 0;
            border-bottom: 2px solid #dee2e6;
            margin-bottom: 20px;
        }}
        .tab {{
            padding: 10px 24px;
            cursor: pointer;
            border: none;
            background: none;
            font-size: 1em;
            color: #666;
            border-bottom: 2px solid transparent;
            margin-bottom: -2px;
            transition: color 0.15s, border-color 0.15s;
        }}
        .tab:hover {{
            color: #333;
        }}
        .tab.active {{
            color: #007bff;
            border-bottom-color: #007bff;
            font-weight: 600;
        }}
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
    </style>
</head>
<body>
    <div class="nav"><a href="/">&larr; Home</a></div>
    <h1>Pipeline Dashboard</h1>

    <div class="tabs">
        {"<button class='tab active' data-tab='analysis'>Analysis</button>" if has_book_tab else ""}
        <button class="tab {"" if has_book_tab else "active"}" data-tab="ingestion">Document Ingestion</button>
    </div>

    {"<div id='tab-analysis' class='tab-content active'>" + (book_pipeline_html or "") + "</div>" if has_book_tab else ""}

    <div id="tab-ingestion" class="tab-content {"" if has_book_tab else "active"}">
        {ingestion_html}
    </div>

    <script>
        document.querySelectorAll('.tab').forEach(function(btn) {{
            btn.addEventListener('click', function() {{
                document.querySelectorAll('.tab').forEach(function(t) {{ t.classList.remove('active'); }});
                document.querySelectorAll('.tab-content').forEach(function(c) {{ c.classList.remove('active'); }});
                btn.classList.add('active');
                document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
            }});
        }});

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
                        // Will be refreshed by auto-poll
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
            // Store all options (skip placeholder at index 0)
            var allOpts = [];
            for (var i = 1; i < sel.options.length; i++) {{
                var o = sel.options[i];
                allOpts.push({{value: o.value, text: o.text, source: o.dataset.source}});
            }}
            filter.addEventListener('change', function() {{
                var sourceId = filter.value;
                // Remove all except placeholder
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

        // Auto-refresh: poll pipeline status every 5 seconds (only when analysis tab exists)
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

            // Pause refresh when user is interacting with buttons
            container.addEventListener('mousedown', function() {{ paused = true; }});
            document.addEventListener('mouseup', function() {{
                setTimeout(function() {{ paused = false; }}, 1000);
            }});
        }})();

        // Ingestion tab refresh button + auto-analyze button
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
                        document.getElementById('tab-ingestion').innerHTML = html;
                    }})
                    .catch(function(err) {{
                        if (statusEl) statusEl.textContent = 'Error: ' + err;
                        btn.disabled = false;
                        btn.textContent = 'Refresh';
                    }});
            }}
            if (e.target && e.target.id === 'auto-analyze-btn') {{
                var btn = e.target;
                var statusEl = document.getElementById('refresh-status');
                btn.disabled = true;
                btn.textContent = 'Starting...';
                if (statusEl) statusEl.textContent = '';
                fetch('/api/pipeline/auto-analyze', {{ method: 'POST' }})
                    .then(function(r) {{ return r.json(); }})
                    .then(function(data) {{
                        btn.textContent = data.status === 'started' ? 'Running...' : (data.status || 'Done');
                        if (statusEl) statusEl.textContent = data.status === 'already_running' ? 'Already running' : 'Analysis started in background';
                        setTimeout(function() {{
                            btn.disabled = false;
                            btn.textContent = 'Auto-Analyze';
                        }}, 5000);
                    }})
                    .catch(function(err) {{
                        if (statusEl) statusEl.textContent = 'Error: ' + err;
                        btn.disabled = false;
                        btn.textContent = 'Auto-Analyze';
                    }});
            }}
        }});
    </script>
</body>
</html>
"""


@app.get("/pipeline", response_class=HTMLResponse)
async def pipeline_dashboard(request: Request):
    """Pipeline monitoring dashboard — ingestion + book generation."""
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
    auth_bar = _auth_bar_html(user)
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
    prev_public = _db.public_only
    try:
        _db.public_only = _auth_mod.get_access_tier(request) != "admin"

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
    finally:
        _db.public_only = prev_public


@app.get("/pass0b", response_class=HTMLResponse)
async def pass0b_page(request: Request):
    """Pass 0b experiment page."""
    return build_pass0b_html(search_mode=_is_search_site(request))


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
            cache_key = get_cache_key(text.strip() + ":pass_0b_concepts_v2")
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

    # Somatic (pass_0b_v2)
    som_suffix = ":pass_0b_v2"
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

    # Language (pass_0b_lang_v1)
    lang_suffix = ":pass_0b_lang_v1"
    if provider_key and provider_key != DEFAULT_LLM:
        lang_suffix += f":{provider_key}"
    lang_cached = load_cached_analysis(get_cache_key(lesson_text + lang_suffix))
    if lang_cached:
        lang_result = {
            "enriched_steps": lang_cached.get("enriched_steps", []),
            "summary": lang_cached.get("summary", {}),
        }

    # Condensed (pass_0b_condensed_v4)
    cond_suffix = ":pass_0b_condensed_v4"
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
    return build_pass0b_player_html(search_mode=_is_search_site(request))


# --- Pass 0b-lang: Language Analysis ---

@app.get("/pass0b-lang", response_class=HTMLResponse)
async def pass0b_lang_page(request: Request):
    """Pass 0b-lang language analysis page."""
    return build_pass0b_lang_html(search_mode=_is_search_site(request))


@app.get("/lesson/{document_id}", response_class=HTMLResponse)
async def lesson_detail_page(request: Request, document_id: str):
    """Unified lesson detail page."""
    user = _auth_mod.get_current_user(request)
    return HTMLResponse(build_lesson_page_html(document_id, user=user, search_mode=_is_search_site(request)))


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


def build_pass0b_html(search_mode: bool = False) -> str:
    """Build the Pass 0b experiment page HTML."""
    categories, display_names = get_available_lessons(public_only=search_mode)

    category_options = ['<option value="">-- Select category --</option>']
    for cat_name in categories.keys():
        cat_display = display_names.get(cat_name, cat_name.replace("_", " ").title())
        category_options.append(f'<option value="{cat_name}">{cat_display}</option>')
    category_select = "\n".join(category_options)

    lessons_json = json.dumps({
        cat: [(t[0], t[1], t[2] if len(t) > 2 else "") for t in lessons]
        for cat, lessons in categories.items()
    })

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Pass 0b: Step-Level Analysis</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; }}
        select {{
            width: 100%;
            padding: 10px;
            font-size: 14px;
            border: 1px solid #ccc;
            border-radius: 4px;
            margin-bottom: 10px;
        }}
        textarea {{
            width: 100%;
            height: 200px;
            font-family: monospace;
            font-size: 14px;
            padding: 10px;
            border: 1px solid #ccc;
            border-radius: 4px;
        }}
        button {{
            background-color: #007bff;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            margin-top: 10px;
        }}
        button:hover {{ background-color: #0056b3; }}
        button:disabled {{ background-color: #ccc; cursor: not-allowed; }}
        .error {{ color: red; padding: 10px; background: #fee; border-radius: 4px; }}
        label {{ font-weight: bold; display: block; margin-bottom: 5px; }}
        .form-group {{ margin-bottom: 15px; }}
        .or-divider {{ text-align: center; color: #666; margin: 10px 0; }}
        .loading-spinner {{
            display: none;
            margin-top: 20px;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 8px;
            text-align: center;
        }}
        .loading-spinner .spinner {{
            display: inline-block;
            width: 24px;
            height: 24px;
            border: 3px solid #ddd;
            border-top-color: #007bff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            vertical-align: middle;
            margin-right: 10px;
        }}
        @keyframes spin {{
            to {{ transform: rotate(360deg); }}
        }}
        .search-box {{
            display: flex;
            gap: 8px;
            margin-bottom: 8px;
        }}
        .search-box input {{
            flex: 1;
            padding: 10px;
            font-size: 14px;
            border: 1px solid #ccc;
            border-radius: 4px;
        }}
        .search-box button {{
            margin-top: 0;
            white-space: nowrap;
        }}
        .search-result {{
            padding: 8px 12px;
            border-bottom: 1px solid #eee;
            cursor: pointer;
        }}
        .search-result:hover {{
            background: #f0f7ff;
        }}
        #search-results {{
            max-height: 300px;
            overflow-y: auto;
            border: 1px solid #ddd;
            border-radius: 4px;
            margin-bottom: 16px;
        }}
        #search-results:empty {{
            border: none;
        }}
    </style>
    <script>
        const lessonsData = {lessons_json};

        function updatePlayerLink() {{
            const cat = document.getElementById('selected_category').value;
            const lesson = document.getElementById('selected_lesson').value;
            const link = document.getElementById('player-link');
            if (link) {{
                let href = '/pass0b-player';
                if (cat && lesson) href += '?category=' + encodeURIComponent(cat) + '&lesson=' + encodeURIComponent(lesson);
                link.href = href;
            }}
        }}

        function onCategoryChange(select) {{
            const category = select.value;
            const lessonSelect = document.getElementById('selected_lesson');
            lessonSelect.innerHTML = '<option value="">-- Select lesson --</option>';
            document.getElementById('source-link').innerHTML = '';
            if (category && lessonsData[category]) {{
                lessonsData[category].forEach(([filename, display, sourceUrl]) => {{
                    const option = document.createElement('option');
                    option.value = filename;
                    option.textContent = display;
                    if (sourceUrl) option.dataset.sourceUrl = sourceUrl;
                    lessonSelect.appendChild(option);
                }});
            }}
            document.getElementById('lesson_text').value = '';
            updatePlayerLink();
        }}

        async function onLessonSelect(select) {{
            const textarea = document.getElementById('lesson_text');
            const category = document.getElementById('selected_category').value;
            const linkEl = document.getElementById('source-link');
            const selectedOption = select.options[select.selectedIndex];
            const sourceUrl = selectedOption ? selectedOption.dataset.sourceUrl : '';
            if (sourceUrl) {{
                linkEl.innerHTML = '<a href="' + sourceUrl + '" target="_blank" style="color: #007bff; font-size: 0.9em;">View original lesson &rarr;</a>';
            }} else {{
                linkEl.innerHTML = '';
            }}
            if (select.value && category) {{
                textarea.value = '';
                textarea.disabled = true;
                textarea.placeholder = 'Loading lesson...';
                textarea.style.opacity = '0.5';
                try {{
                    const response = await fetch('/api/lesson/' + encodeURIComponent(category) + '/' + encodeURIComponent(select.value));
                    const data = await response.json();
                    if (data.text) textarea.value = data.text;
                    else textarea.placeholder = 'Error loading lesson';
                }} catch (error) {{
                    textarea.placeholder = 'Error loading lesson';
                }}
                textarea.disabled = false;
                textarea.style.opacity = '1';
            }} else {{
                textarea.value = '';
                textarea.disabled = false;
                textarea.style.opacity = '1';
                textarea.placeholder = 'Paste lesson text here...';
                linkEl.innerHTML = '';
            }}
            updatePlayerLink();
        }}

        async function submitForm(event) {{
            event.preventDefault();
            const btn = document.getElementById('submit-btn');
            const spinner = document.getElementById('loading-spinner');
            const resultDiv = document.getElementById('result');
            const debugDiv = document.getElementById('debug');
            const form = document.getElementById('pass0b-form');

            btn.disabled = true;
            btn.textContent = 'Running Pass A + somatic + language...';
            spinner.style.display = 'block';
            resultDiv.innerHTML = '';
            debugDiv.innerHTML = '';
            var startTime = Date.now();
            var timer = setInterval(function() {{
                var elapsed = Math.floor((Date.now() - startTime) / 1000);
                document.getElementById('loading-elapsed').textContent = elapsed + 's';
            }}, 1000);

            try {{
                const response = await fetch('/api/pass0b-combined', {{
                    method: 'POST',
                    body: new FormData(form)
                }});
                const data = await response.json();
                clearInterval(timer);

                if (data.error) {{
                    var msg = data.error;
                    if (msg.includes('\\n')) {{
                        resultDiv.innerHTML = '<div class="error"><strong>Error:</strong><pre style="white-space: pre-wrap; word-wrap: break-word; margin-top: 8px;">' + msg.replace(/</g, '&lt;') + '</pre></div>';
                    }} else {{
                        resultDiv.innerHTML = '<div class="error">Error: ' + msg + '</div>';
                    }}
                    if (data.debug_html) debugDiv.innerHTML = data.debug_html;
                    resetForm();
                    return;
                }}

                if (data.result_html) {{
                    var parts = [];
                    var somTime = data.cached_somatic ? 'cached' : (data.elapsed_somatic || '?') + 's';
                    var langTime = data.cached_lang ? 'cached' : (data.elapsed_lang || '?') + 's';
                    parts.push('Somatic: ' + somTime + ', Language: ' + langTime);
                    parts.push((data.step_count || '?') + ' steps in ' + (data.group_count || '?') + ' groups');
                    if (data.tokens_in && data.tokens_out) {{
                        parts.push(data.tokens_in.toLocaleString() + ' in / ' + data.tokens_out.toLocaleString() + ' out tokens');
                    }}
                    resultDiv.innerHTML = '<p style="color: #28a745; font-weight: bold;">' + parts.join(' | ') + '</p>' + data.result_html;
                }}
                if (data.debug_html) debugDiv.innerHTML = data.debug_html;
                resetForm();
            }} catch (error) {{
                clearInterval(timer);
                resultDiv.innerHTML = '<div class="error">Error: ' + error.message + '</div>';
                resetForm();
            }}
        }}

        function resetForm() {{
            document.getElementById('submit-btn').disabled = false;
            document.getElementById('submit-btn').textContent = 'Run Analysis';
            document.getElementById('loading-spinner').style.display = 'none';
        }}

        async function searchLessons() {{
            const query = document.getElementById('search-query').value.trim();
            if (!query) return;
            const method = document.getElementById('search-method').value;
            const btn = document.getElementById('search-btn');
            const resultsDiv = document.getElementById('search-results');
            btn.disabled = true;
            btn.textContent = 'Searching...';
            resultsDiv.innerHTML = '';
            try {{
                const fd = new FormData();
                fd.append('query', query);
                fd.append('method', method);
                const response = await fetch('/api/search', {{ method: 'POST', body: fd }});
                const data = await response.json();
                if (data.error) {{
                    resultsDiv.innerHTML = '<div style="color: #999; padding: 8px;">Error: ' + data.error + '</div>';
                }} else if (data.results && data.results.length > 0) {{
                    let html = '';
                    const isTagSearch = data.method === 'tags';
                    const isCombined = data.method === 'combined';
                    const cascadeMode = data.cascade_mode || '';
                    if (data.specificity) {{
                        const s = data.specificity;
                        const bandColors = {{low: '#dc3545', mid: '#fd7e14', 'mid-high': '#28a745', high: '#007bff'}};
                        const bandLabels = {{low: 'Low', mid: 'Mid', 'mid-high': 'Mid-High', high: 'High'}};
                        const bandExplainers = {{
                            low: 'Many lessons match equally well \u2014 results may all be relevant.',
                            mid: 'Several good matches \u2014 the top results are somewhat differentiated.',
                            'mid-high': 'The top results stand out \u2014 your query is fairly specific.',
                            high: 'Your query strongly targets a few lessons \u2014 the top result is a clear match.'
                        }};
                        const color = bandColors[s.band] || '#666';
                        const label = bandLabels[s.band] || s.band;
                        const explainer = bandExplainers[s.band] || '';
                        let specHtml = 'Specificity: <span style="color: ' + color + '; font-weight: 600;">' + label + '</span> \u2014 ' + explainer;
                        if (cascadeMode) specHtml += ' <span style="color: #666;">(' + cascadeMode + ')</span>';
                        html += '<div style="font-size: 0.8em; color: #888; margin-bottom: 6px;">' + specHtml + '</div>';
                    }}
                    data.results.forEach(r => {{
                        let scoreDisplay;
                        if (isTagSearch) scoreDisplay = r.similarity + ' matches';
                        else scoreDisplay = Math.round(r.similarity * 100) + '%';
                        const snippet = (r.snippet || '').replace(/</g, '&lt;').replace(/\\n/g, ' ');
                        const playerUrl = r.category ? '/pass0b-player?category=' + encodeURIComponent(r.category) + '&lesson=' + encodeURIComponent(r.document_id) : '';
                        html += '<div class="search-result" data-doc-id="' + r.document_id + '">';
                        html += '<div style="display: flex; justify-content: space-between; align-items: baseline;">';
                        html += '<strong style="flex: 1; cursor: pointer;" onclick="selectSearchResult(this.closest(&quot;.search-result&quot;))">' + (r.title || '').replace(/</g, '&lt;') + '</strong>';
                        if (playerUrl) html += '<a href="' + playerUrl + '" style="color: #007bff; font-size: 0.85em; margin-left: 12px; white-space: nowrap; text-decoration: none;" title="Open in audio player">&#9654; Player</a>';
                        html += '<span style="color: #28a745; font-size: 0.85em; margin-left: 8px; white-space: nowrap;">' + scoreDisplay + '</span>';
                        html += '</div>';
                        if (snippet) html += '<div style="color: #666; font-size: 0.85em; margin-top: 2px; cursor: pointer;" onclick="selectSearchResult(this.closest(&quot;.search-result&quot;))">' + snippet + '</div>';
                        html += '</div>';
                    }});
                    resultsDiv.innerHTML = html;
                }} else {{
                    resultsDiv.innerHTML = '<div style="color: #999; padding: 8px;">No results found.</div>';
                }}
            }} catch (error) {{
                resultsDiv.innerHTML = '<div style="color: red; padding: 8px;">Search failed: ' + error.message + '</div>';
            }}
            btn.disabled = false;
            btn.textContent = 'Search';
        }}

        function selectSearchResult(el) {{
            const docId = el.dataset.docId;
            for (const [cat, lessons] of Object.entries(lessonsData)) {{
                for (const [lessonKey, display, sourceUrl] of lessons) {{
                    if (lessonKey === docId) {{
                        const catSelect = document.getElementById('selected_category');
                        catSelect.value = cat;
                        onCategoryChange(catSelect);
                        setTimeout(() => {{
                            const lessonSelect = document.getElementById('selected_lesson');
                            lessonSelect.value = docId;
                            onLessonSelect(lessonSelect);
                        }}, 50);
                        document.getElementById('search-results').innerHTML = '';
                        return;
                    }}
                }}
            }}
        }}

        async function clearCache() {{
            const btn = document.getElementById('clear-cache-btn');
            btn.disabled = true;
            btn.textContent = 'Clearing...';
            try {{
                // Clear both caches in parallel
                const [r1, r2] = await Promise.all([
                    fetch('/api/pass0b/clear-cache', {{ method: 'POST' }}),
                    fetch('/api/pass0b-lang/clear-cache', {{ method: 'POST' }})
                ]);
                const [d1, d2] = await Promise.all([r1.json(), r2.json()]);
                var total = (d1.deleted || 0) + (d2.deleted || 0);
                btn.textContent = 'Cleared ' + total + ' entries';
                setTimeout(() => {{ btn.textContent = 'Clear Cache'; btn.disabled = false; }}, 2000);
            }} catch (error) {{
                btn.textContent = 'Error';
                setTimeout(() => {{ btn.textContent = 'Clear Cache'; btn.disabled = false; }}, 2000);
            }}
        }}
    </script>
</head>
<body>
    <h1>Pass 0b: Step-Level Analysis</h1>
    <p>Runs Pass A (decomposition) then somatic enrichment and language analysis in parallel.
       <span style="float: right; font-size: 0.9em;">
           <a id="player-link" href="/pass0b-player" style="color: #007bff; text-decoration: none; margin-right: 12px;">Audio Player &rarr;</a>
           {'<a href="/analyzer" style="color: #007bff; text-decoration: none;">&larr; Main Analyzer</a>' if not search_mode else ''}
       </span>
    </p>

    <div class="form-group">
        <label>Search lessons:</label>
        <div class="search-box">
            <input type="text" id="search-query" placeholder="e.g. rolling pelvis, eyes and head..." onkeydown="if(event.key==='Enter'){{event.preventDefault();searchLessons();}}">
            <select id="search-method" style="padding:6px 8px;font-size:13px;border:1px solid #ccc;border-radius:4px;">
                <option value="combined">Combined</option>
                <option value="default">Standard</option>
                <option value="concepts">Distinctive</option>
                <option value="tags">Tag Match</option>
            </select>
            <button type="button" id="search-btn" onclick="searchLessons()">Search</button>
        </div>
        <div id="search-results"></div>
    </div>

    <form id="pass0b-form" onsubmit="submitForm(event)">
        <div class="form-group">
            <label for="selected_category">Category:</label>
            <select name="selected_category" id="selected_category" onchange="onCategoryChange(this)">
                {category_select}
            </select>
        </div>

        <div class="form-group">
            <label for="selected_lesson">Lesson:</label>
            <select name="selected_lesson" id="selected_lesson" onchange="onLessonSelect(this)">
                <option value="">-- Select lesson --</option>
            </select>
            <div id="source-link" style="margin-top: 4px;"></div>
        </div>

        <div class="or-divider">— OR —</div>

        <div class="form-group">
            <label for="lesson_text">Paste Lesson Text:</label>
            <textarea name="lesson_text" id="lesson_text" placeholder="Paste lesson text here..."></textarea>
        </div>

        <div style="margin: 10px 0;">
            <label style="font-size: 0.9em;">
                Provider:
                <select name="provider" style="margin-left: 4px; padding: 2px 6px; width: auto;">
                    <option value="">Default (Gemini)</option>
                    <option value="gemini">Gemini 3 Flash</option>
                    <option value="openai">OpenAI (GPT-5)</option>
                    <option value="anthropic">Sonnet 4.6</option>
                    <option value="anthropic-sonnet-4.5">Sonnet 4.5</option>
                    <option value="anthropic-haiku">Haiku 4.5</option>
                </select>
            </label>
        </div>

        <button type="submit" id="submit-btn">Run Analysis</button>
        <button type="button" id="clear-cache-btn" onclick="clearCache()" style="background-color: #6c757d; margin-left: 8px;">Clear Cache</button>
    </form>

    <div id="loading-spinner" class="loading-spinner">
        <span class="spinner"></span>
        <span>Running Pass A + somatic &amp; language analysis...</span>
        <div id="loading-elapsed" style="font-size: 0.85em; color: #999; margin-top: 8px;"></div>
    </div>

    <div id="result" style="margin-top: 20px;"></div>
    <div id="debug" style="margin-top: 20px;"></div>
</body>
</html>
"""


def _auth_bar_html(user: dict | None) -> str:
    """Render logout link for logged-in users."""
    if user:
        login = html_mod.escape(user.get("login", ""))
        return f'<div class="auth-bar">{login} &middot; <a href="/auth/logout">Log out</a> &middot; <a href="/docs/demo_overview">Docs</a></div>'
    return ''


_AUTH_BAR_CSS = """
        .auth-bar { text-align: right; font-size: 0.85em; color: #666; margin-bottom: 8px; }
        .auth-bar a { color: #007bff; text-decoration: none; }
        .auth-bar a:hover { text-decoration: underline; }
"""


def build_search_home_html(extra_html: str = "", user: dict | None = None) -> str:
    """Build the search subdomain home page with search box and browse-by-source."""
    categories, display_names = get_available_lessons(public_only=True)

    # Count total lessons across all categories
    total_lessons = sum(len(lessons) for lessons in categories.values())

    # Build lesson list HTML grouped by source
    lesson_list_html = ""
    for cat_key, lessons in categories.items():
        source_name = html_mod.escape(display_names.get(cat_key, cat_key))
        lesson_list_html += f'<h3 class="source-header">{source_name} ({len(lessons)})</h3>\n'
        lesson_list_html += '<ul class="lesson-list">\n'
        for lesson in lessons:
            doc_id, display_name = lesson[0], lesson[1]
            esc_name = html_mod.escape(display_name)
            lesson_list_html += f'<li><a href="/lesson/{html_mod.escape(doc_id)}">{esc_name}</a></li>\n'
        lesson_list_html += '</ul>\n'

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Feldenkrais Lesson Search</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px; margin: 0 auto; padding: 24px;
            line-height: 1.6; background: #fafafa; color: #333;
        }}
        h1 {{ margin-bottom: 4px; }}
        .subtitle {{ color: #555; font-size: 0.95em; margin-bottom: 24px; line-height: 1.5; }}

        /* Example queries */
        .example-queries {{ font-size: 0.85em; color: #888; margin-bottom: 24px; }}
        .example-queries a {{
            color: #007bff; text-decoration: none; cursor: pointer;
        }}
        .example-queries a:hover {{ text-decoration: underline; }}

        /* Suggested searches */
        .suggested-searches {{
            display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px;
        }}
        .suggested-searches a {{
            padding: 6px 14px; background: #fff; border: 1px solid #e0e0e0;
            border-radius: 16px; font-size: 0.85em; color: #007bff;
            text-decoration: none; cursor: pointer;
            transition: border-color 0.15s, background 0.15s;
        }}
        .suggested-searches a:hover {{ border-color: #007bff; background: #f0f7ff; }}

        /* Search */
        .search-box {{
            display: flex; gap: 8px; margin-bottom: 8px;
        }}
        .search-box input {{
            flex: 1; padding: 10px 14px; font-size: 15px;
            border: 1px solid #ccc; border-radius: 6px;
        }}
        .search-box input:focus {{ outline: none; border-color: #007bff; box-shadow: 0 0 0 2px rgba(0,123,255,0.15); }}
        .search-box button {{
            padding: 10px 20px; font-size: 15px; font-weight: 600;
            border: none; border-radius: 6px; background: #007bff; color: white; cursor: pointer;
        }}
        .search-box button:hover {{ background: #0056b3; }}
        .search-box button:disabled {{ background: #ccc; cursor: not-allowed; }}
        #search-results {{ margin-bottom: 24px; }}
        .search-result {{
            padding: 10px 12px; border-bottom: 1px solid #eee;
        }}
        .search-result:hover {{ background: #f5f5f5; }}
        .search-result a {{ color: #007bff; text-decoration: none; font-weight: 500; }}
        .search-result a:hover {{ text-decoration: underline; }}
        .search-result .snippet {{ color: #666; font-size: 0.85em; margin-top: 2px; }}
        .search-result .view-link {{ color: #007bff; font-size: 0.82em; text-decoration: none; }}
        .search-result .view-link:hover {{ text-decoration: underline; }}
        .search-result .similarity {{ color: #28a745; font-size: 0.85em; white-space: nowrap; }}

        /* Browse */
        h2 {{ font-size: 1.15em; color: #444; margin-top: 32px; margin-bottom: 12px; }}
        .source-header {{ font-size: 1em; color: #555; margin-top: 24px; margin-bottom: 4px; }}
        .lesson-list {{ list-style: none; padding: 0; margin: 4px 0 0 0; }}
        .lesson-list li {{ padding: 4px 0 4px 16px; }}
        .lesson-list li a {{ color: #007bff; text-decoration: none; }}
        .lesson-list li a:hover {{ text-decoration: underline; }}
        /* Scored lesson list (after search) */
        .scored-lesson-list {{ list-style: none; padding: 0; margin: 4px 0 0 0; }}
        .scored-lesson-list li {{
            padding: 4px 16px; display: flex; justify-content: space-between; align-items: baseline;
        }}
        .scored-lesson-list li a {{ color: #007bff; text-decoration: none; }}
        .scored-lesson-list li a:hover {{ text-decoration: underline; }}
        .scored-lesson-list .score {{ color: #28a745; font-size: 0.82em; white-space: nowrap; margin-left: 12px; }}
        .clear-search {{ font-size: 0.85em; color: #007bff; cursor: pointer; text-decoration: none; margin-left: 12px; }}
        .clear-search:hover {{ text-decoration: underline; }}
{_AUTH_BAR_CSS}
    </style>
</head>
<body>
    {_auth_bar_html(user)}
    <h1>Feldenkrais Lesson Search</h1>
    <p class="subtitle">Search across {total_lessons} lessons by meaning, not keywords.<br>
    Each lesson is automatically analyzed into movement steps, body positions,
    and somatic cues &mdash; search runs against that structured understanding.</p>

    <div class="search-box">
        <input type="text" id="search-query" placeholder="e.g. rolling pelvis, eyes and head..."
               onkeydown="if(event.key==='Enter'){{event.preventDefault();searchLessons();}}">
        <select id="search-method" style="padding:8px 10px;font-size:14px;border:1px solid #ccc;border-radius:6px;background:#fff;">
            <option value="combined">Combined</option>
            <option value="default">Standard</option>
            <option value="concepts">Distinctive</option>
            <option value="tags">Tag Match</option>
        </select>
        <button type="button" id="search-btn" onclick="searchLessons()">Search</button>
    </div>
    <div class="suggested-searches">
        <a onclick="tryQuery(this)">freeing the shoulders</a>
        <a onclick="tryQuery(this)">rolling patterns</a>
        <a onclick="tryQuery(this)">pelvic clock</a>
        <a onclick="tryQuery(this)">breathing and ribs</a>
        <a onclick="tryQuery(this)">jaw and tongue</a>
        <a onclick="tryQuery(this)">standing balance</a>
    </div>
    <div id="search-results"></div>

    <div id="all-lessons">
    <h2>All Lessons ({total_lessons})</h2>
    {lesson_list_html}
    </div>

    <script>
        async function searchLessons() {{
            const query = document.getElementById('search-query').value.trim();
            if (!query) return;
            const method = document.getElementById('search-method').value;
            const btn = document.getElementById('search-btn');
            const resultsDiv = document.getElementById('search-results');
            btn.disabled = true;
            btn.textContent = 'Searching...';
            resultsDiv.innerHTML = '';
            try {{
                const fd = new FormData();
                fd.append('query', query);
                fd.append('method', method);
                const response = await fetch('/api/search', {{ method: 'POST', body: fd }});
                const data = await response.json();
                if (data.error) {{
                    resultsDiv.innerHTML = '<div style="color:#999;padding:8px;">Error: ' + escHtml(data.error) + '</div>';
                }} else if (data.results && data.results.length > 0) {{
                    let html = '';
                    if (data.method) {{
                        const methodLabels = {{combined:'Combined',default:'Standard',concepts:'Distinctive',tags:'Tag Match'}};
                        html += '<div style="font-size:0.8em;color:#888;margin-bottom:4px;">Method: <strong>' + (methodLabels[data.method] || data.method) + '</strong></div>';
                    }}
                    if (data.specificity) {{
                        const s = data.specificity;
                        const bandColors = {{low:'#dc3545',mid:'#fd7e14','mid-high':'#28a745',high:'#007bff'}};
                        const bandLabels = {{low:'Low',mid:'Mid','mid-high':'Mid-High',high:'High'}};
                        const bandExplainers = {{
                            low:'Many lessons match equally well.',
                            mid:'Several good matches \u2014 top results are somewhat differentiated.',
                            'mid-high':'Top results stand out \u2014 your query is fairly specific.',
                            high:'Strong match \u2014 the top result is a clear winner.'
                        }};
                        const color = bandColors[s.band] || '#666';
                        const label = bandLabels[s.band] || s.band;
                        const explainer = bandExplainers[s.band] || '';
                        const cascadeMode = data.cascade_mode || '';
                        let specHtml = 'Specificity: <span style="color:'+color+';font-weight:600;">'+label+'</span> \u2014 '+explainer;
                        if (cascadeMode) specHtml += ' <span style="color:#666;">('+cascadeMode+')</span>';
                        html += '<div style="font-size:0.8em;color:#888;margin-bottom:6px;">'+specHtml+'</div>';
                    }}
                    const isTagSearch = data.method === 'tags';
                    const topResults = data.results.slice(0, 10);
                    topResults.forEach(r => {{
                        let scoreDisplay;
                        if (isTagSearch) scoreDisplay = r.similarity + ' matches';
                        else scoreDisplay = Math.round(r.similarity * 100) + '%';
                        let snippet = (r.snippet || '').replace(/</g, '&lt;').replace(/\\n/g, ' ');
                        snippet = snippet.replace(/^Setting:\s*/i, '');
                        html += '<div class="search-result">';
                        html += '<div style="display:flex;justify-content:space-between;align-items:baseline;">';
                        html += '<a href="/lesson/'+r.document_id+'">' + escHtml(r.title || '') + '</a>';
                        html += '<span class="similarity">'+scoreDisplay+'</span>';
                        html += '</div>';
                        if (snippet) html += '<div class="snippet">'+snippet+'</div>';
                        html += '</div>';
                    }});
                    resultsDiv.innerHTML = html;
                    // Update All Lessons section with scored list
                    updateAllLessons(data.results, data.method);
                }} else {{
                    resultsDiv.innerHTML = '<div style="color:#999;padding:8px;">No results found.</div>';
                }}
            }} catch (error) {{
                resultsDiv.innerHTML = '<div style="color:red;padding:8px;">Search failed: '+error.message+'</div>';
            }}
            btn.disabled = false;
            btn.textContent = 'Search';
        }}

        // Save original All Lessons HTML for restore
        var originalAllLessons = document.getElementById('all-lessons').innerHTML;

        function updateAllLessons(results, method) {{
            const container = document.getElementById('all-lessons');
            const isTagSearch = method === 'tags';
            let html = '<h2>All Lessons \u2014 sorted by relevance (' + results.length + ') <a class="clear-search" onclick="clearSearch()">Clear search</a></h2>';
            html += '<ul class="scored-lesson-list">';
            results.forEach(r => {{
                let scoreDisplay;
                if (isTagSearch) scoreDisplay = r.similarity + ' matches';
                else scoreDisplay = Math.round(r.similarity * 100) + '%';
                html += '<li>';
                html += '<a href="/lesson/' + r.document_id + '">' + escHtml(r.title || '') + '</a>';
                html += '<span class="score">' + scoreDisplay + '</span>';
                html += '</li>';
            }});
            html += '</ul>';
            container.innerHTML = html;
        }}

        function clearSearch() {{
            document.getElementById('all-lessons').innerHTML = originalAllLessons;
            document.getElementById('search-results').innerHTML = '';
            document.getElementById('search-query').value = '';
        }}

        function tryQuery(el) {{
            const input = document.getElementById('search-query');
            input.value = el.textContent;
            searchLessons();
        }}

        function escHtml(str) {{
            const d = document.createElement('div');
            d.textContent = str;
            return d.innerHTML;
        }}
    </script>
    {extra_html}
</body>
</html>"""


def build_lesson_page_html(document_id: str, user: dict | None = None, search_mode: bool = False) -> str:
    """Build the unified lesson detail page HTML."""
    esc_id = html_mod.escape(document_id)

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Lesson</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px; margin: 0 auto; padding: 20px;
            line-height: 1.6; background: #fafafa;
        }}
        h1 {{ color: #333; margin-bottom: 2px; font-size: 1.4em; }}
{_AUTH_BAR_CSS}
        .source-line {{ color: #666; font-size: 0.9em; margin-bottom: 16px; }}
        .source-line a {{ color: #007bff; text-decoration: none; }}
        .source-line a:hover {{ text-decoration: underline; }}
        .back-link {{ font-size: 0.9em; margin-bottom: 12px; }}
        .back-link a {{ color: #007bff; text-decoration: none; }}
        .back-link a:hover {{ text-decoration: underline; }}

        /* Tabs */
        .tab-bar {{
            display: flex; gap: 0; border-bottom: 2px solid #e0e0e0; margin-bottom: 16px;
        }}
        .tab-bar button {{
            padding: 10px 24px; font-size: 14px; font-weight: 600;
            border: none; background: none; cursor: pointer;
            color: #666; border-bottom: 2px solid transparent;
            margin-bottom: -2px; transition: color 0.15s, border-color 0.15s;
        }}
        .tab-bar button:hover {{ color: #333; }}
        .tab-bar button.active {{ color: #007bff; border-bottom-color: #007bff; }}
        .tab-intro {{
            font-size: 0.85em; color: #888; margin-bottom: 12px; font-style: italic;
        }}

        /* Word-level annotation colors */
        .word-directive {{ border-bottom: 2px solid rgba(97, 97, 97, 0.3); }}
        .word-directive.word-active {{ background: rgba(97, 97, 97, 0.18); border-bottom-color: #616161; }}
        .word-attention {{ border-bottom: 2px solid rgba(255, 152, 0, 0.3); }}
        .word-attention.word-active {{ background: rgba(255, 152, 0, 0.18); border-bottom-color: #ff9800; }}
        .word-imagery {{ border-bottom: 2px solid rgba(156, 39, 176, 0.3); }}
        .word-imagery.word-active {{ background: rgba(156, 39, 176, 0.18); border-bottom-color: #9c27b0; }}
        .word-inquiry {{ border-bottom: 2px solid rgba(33, 150, 243, 0.3); }}
        .word-inquiry.word-active {{ background: rgba(33, 150, 243, 0.18); border-bottom-color: #2196f3; }}
        .word-distinctive {{ border-bottom: 2px solid rgba(233, 30, 99, 0.3); }}
        .word-distinctive.word-active {{ background: rgba(233, 30, 99, 0.18); border-bottom-color: #e91e63; }}
        .note-line {{
            font-size: 0.82em; color: #555; min-height: 1.4em;
            padding: 4px 8px; margin-top: 4px;
            border-top: 1px solid #f0e68c; font-style: italic;
        }}
        .note-category {{
            font-weight: 600; font-style: normal; font-size: 0.85em;
            text-transform: uppercase; letter-spacing: 0.3px; margin-right: 4px;
        }}
        .note-cat-directive {{ color: #616161; }}
        .note-cat-attention {{ color: #e65100; }}
        .note-cat-imagery {{ color: #7b1fa2; }}
        .note-cat-inquiry {{ color: #1565c0; }}
        .note-cat-distinctive {{ color: #c2185b; }}

        /* Condensed cards */
        .condensed-card {{
            background: #fff; border: 1px solid #e0e0e0; border-radius: 10px;
            padding: 20px; margin-bottom: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }}
        .condensed-card.group-card {{ margin-left: 24px; }}
        .condensed-card h3 {{ margin: 0 0 4px 0; font-size: 1.1em; color: #222; }}
        .condensed-card .group-tag {{
            font-size: 0.8em; font-weight: 700; color: #1a73e8;
            text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px;
        }}
        .condensed-card .setting-tag {{
            font-size: 0.8em; font-weight: 700; color: #e65100;
            text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px;
        }}
        .phase-header {{
            margin: 20px 0 10px 0; padding: 8px 12px;
            background: #f0f4ff; border-left: 3px solid #4a6fa5;
            font-size: 0.9em; color: #4a6fa5; font-weight: 600;
        }}
        .phase-header .phase-time {{ font-weight: normal; color: #888; margin-left: 6px; }}
        .phase-header .phase-note {{ font-weight: normal; color: #666; margin-left: 10px; }}
        .pov-section {{
            margin-bottom: 10px; padding: 10px 14px;
            border-radius: 6px; border-left: 4px solid;
            font-size: 0.92em; line-height: 1.55;
        }}
        .pov-section .pov-label {{
            font-size: 0.75em; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.5px; margin-bottom: 4px;
        }}
        .pov-action {{ background: #f5f5f5; border-color: #616161; }}
        .pov-action .pov-label {{ color: #616161; }}
        .pov-body {{ background: #e8f5e9; border-color: #4caf50; }}
        .pov-body .pov-label {{ color: #2e7d32; }}
        .pov-attention {{ background: #fff3e0; border-color: #ff9800; }}
        .pov-attention .pov-label {{ color: #e65100; }}
        .pov-intention {{ background: #e3f2fd; border-color: #2196f3; }}
        .pov-intention .pov-label {{ color: #1565c0; }}
        .pov-voice {{ background: #f3e5f5; border-color: #9c27b0; }}
        .pov-voice .pov-label {{ color: #7b1fa2; }}
        .setting-field {{
            margin-bottom: 10px; padding: 10px 14px;
            border-radius: 6px; border-left: 4px solid #e65100;
            background: #fff8f0; font-size: 0.92em; line-height: 1.55;
        }}
        .setting-field .field-label {{
            font-size: 0.75em; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.5px; margin-bottom: 4px; color: #e65100;
        }}

        /* Audio player */
        .audio-container {{
            margin-bottom: 16px; padding: 12px 16px;
            background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
        }}
        .audio-container audio {{ width: 100%; }}
        .audio-hidden {{ display: none; }}

        /* Card */
        .card {{
            background: #fff; border: 1px solid #e0e0e0; border-radius: 10px;
            padding: 16px 20px; margin-bottom: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            min-height: 250px; display: flex; flex-direction: column;
        }}
        .card-placeholder {{
            color: #999; text-align: center; padding: 60px 20px; font-size: 1.1em;
        }}
        .group-label {{
            font-size: 0.75em; font-weight: 700; color: #1a73e8;
            text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px;
        }}
        .step-header {{ font-size: 1.05em; font-weight: 700; color: #222; margin-bottom: 1px; }}
        .step-time {{ color: #888; font-weight: 400; font-size: 0.82em; }}
        .step-position {{
            color: #888; font-size: 0.75em; text-transform: uppercase; letter-spacing: 0.3px;
            margin-bottom: 2px;
        }}
        .step-instruction {{
            color: #444; font-style: italic; margin-bottom: 8px; font-size: 0.88em; line-height: 1.4;
        }}

        /* Segment highlight box */
        .segment-box {{
            background: #fffde7; border: 2px solid #fdd835;
            border-radius: 6px; padding: 12px 16px;
            margin-top: 10px; font-size: 0.95em;
            line-height: 1.5; color: #555; min-height: 50px;
        }}
        .segment-box strong {{ transition: color 0.15s; }}

        /* Analysis sections */
        .analysis-section {{ margin-bottom: 8px; }}
        .analysis-section h4 {{
            font-size: 0.75em; font-weight: 700; color: #666;
            text-transform: uppercase; letter-spacing: 0.5px;
            margin: 0 0 4px 0; padding-bottom: 3px; border-bottom: 1px solid #eee;
        }}
        .analysis-section ul {{ margin: 0; padding-left: 16px; font-size: 0.84em; color: #444; line-height: 1.4; }}
        .analysis-section li {{ margin-bottom: 2px; }}
        .analysis-section strong {{ color: #333; }}

        /* Transcript segments */
        .segment-details {{ margin-top: 6px; }}
        .segment-details summary {{
            cursor: pointer; font-size: 0.85em; color: #888;
        }}
        .segment-details .seg-container {{
            margin-top: 4px; padding: 6px 10px; background: #fafafa;
            border-radius: 4px; font-size: 0.85em; color: #555;
        }}
        .segment-details .seg-line {{ margin: 2px 0; }}
        .segment-details .seg-id {{ color: #999; font-size: 0.85em; }}

        /* Navigation */
        .card-nav {{
            display: flex; justify-content: space-between; align-items: center;
            margin-top: auto; padding-top: 16px; border-top: 1px solid #eee;
        }}
        .card-nav button {{
            padding: 6px 16px; font-size: 13px; border: 1px solid #ccc;
            border-radius: 4px; background: #fff; cursor: pointer;
            color: #333; font-weight: 500;
        }}
        .card-nav button:hover {{ background: #f0f0f0; }}
        .card-nav button:disabled {{ opacity: 0.3; cursor: not-allowed; }}
        .card-nav .step-counter {{ color: #888; font-size: 0.85em; }}

        /* Progress bar */
        .progress-bar {{ font-size: 0.85em; color: #888; text-align: center; }}

        /* Detailed view */
        .detailed-group-header {{
            margin-top: 24px; margin-bottom: 8px; padding: 8px 12px;
            background: #e8f0fe; border-left: 4px solid #1a73e8; border-radius: 4px;
        }}
        .detailed-group-header strong {{ color: #1a73e8; }}
        .detailed-group-desc {{ color: #555; font-size: 0.85em; margin-top: 4px; }}
        .detailed-group-position {{ color: #888; font-size: 0.8em; margin-top: 2px; text-transform: uppercase; letter-spacing: 0.3px; }}
        .detailed-step {{
            margin: 12px 0 4px 0; padding: 8px 12px;
            background: #f8f9fa; border-radius: 4px; border: 1px solid #e0e0e0;
        }}
        .detailed-step-header {{ margin-bottom: 4px; }}
        .detailed-step-header strong {{ color: #333; }}
        .detailed-step-time {{ color: #888; font-size: 0.85em; }}
        .detailed-step-position {{ color: #888; font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 2px; }}
        .detailed-step em {{ color: #444; }}
        .detailed-clarified {{ margin-bottom: 6px; color: #333; }}
        .detailed-bullets {{ margin: 4px 0 0 0; padding-left: 20px; font-size: 0.9em; color: #444; }}
        .detailed-bullets li {{ margin-bottom: 3px; }}
        .detailed-bullets strong {{ color: #333; }}
        .detailed-section-label {{
            font-size: 0.8em; font-weight: 700; color: #666;
            text-transform: uppercase; letter-spacing: 0.5px;
            margin: 8px 0 6px 0; padding-bottom: 4px; border-bottom: 1px solid #eee;
        }}

        /* Loading / status */
        .status-msg {{ text-align: center; padding: 12px; color: #666; font-size: 0.9em; }}
        .status-msg.error {{ color: #c62828; background: #ffebee; border-radius: 6px; }}
        .spinner-inline {{
            display: inline-block; width: 16px; height: 16px;
            border: 2px solid #ddd; border-top-color: #007bff;
            border-radius: 50%; animation: spin 0.8s linear infinite;
            vertical-align: middle; margin-right: 6px;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

        /* Chat panel */
        .chat-panel {{
            border: 1px solid #e0e0e0; border-radius: 10px;
            background: #fff; overflow: hidden;
        }}
        .chat-context-config {{
            padding: 8px 16px; background: #fafafa; border-bottom: 1px solid #eee;
            font-size: 0.8em; color: #555;
        }}
        .chat-context-config label {{ margin-right: 12px; cursor: pointer; white-space: nowrap; }}
        .chat-context-config input[type="checkbox"] {{ margin-right: 3px; }}
        .chat-messages {{
            height: 350px; overflow-y: auto; padding: 12px 16px;
            display: flex; flex-direction: column; gap: 10px;
        }}
        .chat-msg {{
            max-width: 85%; padding: 8px 12px; border-radius: 8px;
            font-size: 0.9em; line-height: 1.5; white-space: pre-wrap;
        }}
        .chat-msg.user {{ align-self: flex-end; background: #e3f2fd; color: #1a237e; }}
        .chat-msg.assistant {{ align-self: flex-start; background: #f5f5f5; color: #333; }}
        .chat-msg.error {{ align-self: center; background: #ffebee; color: #c62828; font-size: 0.85em; }}
        .chat-input-row {{
            display: flex; gap: 8px; padding: 10px 16px; border-top: 1px solid #eee;
        }}
        .chat-input-row input {{
            flex: 1; padding: 8px 12px; font-size: 0.9em;
            border: 1px solid #ccc; border-radius: 6px;
        }}
        .chat-input-row button {{
            padding: 8px 16px; font-size: 0.9em; font-weight: 500;
            border: none; border-radius: 6px; background: #1976d2;
            color: #fff; cursor: pointer;
        }}
        .chat-input-row button:hover {{ background: #1565c0; }}
        .chat-input-row button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .chat-suggestions {{ display: flex; flex-wrap: wrap; gap: 8px; }}
        .chat-suggestion {{
            padding: 8px 14px; font-size: 0.85em; border: 1px solid #d0d7de;
            border-radius: 18px; background: #f6f8fa; color: #333;
            cursor: pointer; transition: background 0.15s;
        }}
        .chat-suggestion:hover {{ background: #e3f2fd; border-color: #90caf9; }}
        .chat-msg.assistant {{ white-space: normal; }}
        .chat-msg.assistant p {{ margin: 0 0 8px 0; }}
        .chat-msg.assistant p:last-child {{ margin-bottom: 0; }}
        .chat-msg.assistant ul, .chat-msg.assistant ol {{ margin: 4px 0 8px 0; padding-left: 20px; }}
        .chat-msg.assistant li {{ margin-bottom: 2px; }}
        .chat-msg.assistant strong {{ font-weight: 600; }}
        .chat-msg.assistant code {{ background: #e8e8e8; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }}
        .chat-msg.assistant h1, .chat-msg.assistant h2, .chat-msg.assistant h3 {{ font-size: 1em; font-weight: 600; margin: 8px 0 4px 0; }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</head>
<body>
    {_auth_bar_html(user)}
    <div class="back-link"><a href="/">&larr; All lessons</a></div>
    <h1 id="lesson-title">Loading...</h1>
    <div class="source-line" id="source-line"></div>

    <div class="audio-container audio-hidden" id="audio-container">
        <audio id="audio-player" controls preload="auto"></audio>
        <div class="segment-box" id="segment-box">
            <div id="segment-text"></div>
            <div class="note-line" id="annotation-note"></div>
        </div>
    </div>
    <div id="no-audio-msg" style="display:none; color: #888; font-size: 0.9em; font-style: italic; margin-bottom: 12px;">Audio unavailable for this lesson.</div>

    <div class="tab-bar" id="tab-bar">
        <button class="active" onclick="switchTab('steps')">Step by Step</button>
        <button onclick="switchTab('condensed')">Overview</button>
        <button onclick="switchTab('concepts')">Search Specifics</button>
        <button onclick="switchTab('chat')">Q&amp;A</button>
        <button onclick="switchTab('detailed')">All Details</button>
    </div>
    <div id="status-area"></div>

    <!-- Steps/Cards tab (default) -->
    <div id="tab-steps">
        <div class="tab-intro">Follow along with the lesson one movement at a time. Each card shows what's happening biomechanically and linguistically, synced with the transcript and audio.</div>
        <div class="card" id="card">
            <div class="card-placeholder" id="card-placeholder">
                <span class="spinner-inline"></span> Loading analysis...
            </div>
            <div id="card-content" style="display:none; flex: 1; flex-direction: column;">
                <div class="group-label" id="card-group"></div>
                <div class="step-header">
                    <span id="card-step-label"></span>
                    <span class="step-time" id="card-step-time"></span>
                </div>
                <div class="step-position" id="card-position" style="display:none;"></div>
                <div class="step-instruction" id="card-instruction"></div>
                <div id="analysis-somatic" class="analysis-section" style="display:none;">
                    <h4>Somatic</h4>
                    <ul id="somatic-bullets"></ul>
                </div>
                <div id="analysis-lang" class="analysis-section" style="display:none;">
                    <h4>Language</h4>
                    <ul id="lang-bullets"></ul>
                </div>
                <details id="segment-details" class="segment-details" style="display:none;">
                    <summary>Original text</summary>
                    <div id="segment-list"></div>
                </details>
                <div class="card-nav">
                    <button id="nav-prev" onclick="navigate(-1)">&larr; Previous</button>
                    <span class="step-counter" id="step-counter"></span>
                    <button id="nav-next" onclick="navigate(1)">Next &rarr;</button>
                </div>
            </div>
        </div>
        <div class="progress-bar" id="progress-bar"></div>
    </div>

    <!-- Condensed/Outline tab -->
    <div id="tab-condensed" style="display:none;">
        <div class="tab-intro">The lesson at a glance &mdash; its overall arc, then each movement sequence summarized with body focus, attention, and intention.</div>
        <div id="condensed-setting-card"></div>
        <div id="condensed-group-cards"></div>
        <div class="progress-bar" id="condensed-progress"></div>
    </div>

    <!-- Search Specifics tab -->
    <div id="tab-concepts" style="display:none;">
        <div class="tab-intro">How this lesson is indexed for search &mdash; what makes it distinctive, and how different search methods find it.</div>
        <div style="background:#f8f9fa;border:1px solid #e9ecef;border-radius:8px;padding:14px 18px;margin-bottom:16px;font-size:0.88em;line-height:1.6;color:#555;">
            <div style="font-weight:600;color:#333;margin-bottom:6px;">How search works</div>
            All Feldenkrais lessons share common vocabulary &mdash; &ldquo;lie on your back,&rdquo; &ldquo;notice the difference&rdquo; &mdash;
            which makes them hard to distinguish by text similarity alone. To improve search, each lesson goes through a concept extraction pass
            that identifies what is <em>specific and distinctive</em> about it. This produces three things used by four search methods:
            <ul style="margin:8px 0 4px 0;padding-left:20px;">
                <li><strong>Combined</strong> (default) &mdash; Fuses results from all three methods below using reciprocal rank fusion. Lessons that score well across multiple signals rise to the top.</li>
                <li><strong>Standard</strong> &mdash; Embedding similarity against the full lesson analysis. Broadest coverage (all lessons), but generic language can make results muddy.</li>
                <li><strong>Distinctive</strong> &mdash; Embedding similarity against a detailed physical description of what makes this lesson unique. Captures specific positions, movement sequences, and key connections.</li>
                <li><strong>Tag Match</strong> &mdash; Direct text matching against concept tags and search terms. Best for specific queries like body parts or named movement patterns.</li>
            </ul>
        </div>
        <div id="concepts-content">
            <div style="color:#999; padding:16px;"><span class="spinner-inline"></span> Loading concept data...</div>
        </div>
    </div>

    <!-- Q&A tab -->
    <div id="tab-chat" style="display:none;">
        <div class="tab-intro">Ask any question about this lesson. Answers draw from the transcript and all analyses.</div>
        <div class="chat-panel">
            <div class="chat-context-config" id="chat-context-config" {'style="display:none;"' if search_mode else ''}>
                <strong>Context:</strong>
                <label><input type="checkbox" id="ctx-lesson-text" checked> Lesson text</label>
                <label><input type="checkbox" id="ctx-pass-a" checked> Pass A</label>
                <label><input type="checkbox" id="ctx-somatic" checked> Somatic</label>
                <label><input type="checkbox" id="ctx-language" checked> Language</label>
                <label><input type="checkbox" id="ctx-somatic-summary" checked> Somatic summary</label>
                <label><input type="checkbox" id="ctx-language-summary" checked> Language summary</label>
                <label><input type="checkbox" id="ctx-condensed" checked> Condensed cards</label>
            </div>
            <div class="chat-messages" id="chat-messages">
                <div class="chat-hint" id="chat-hint">
                    <div style="color:#666; font-size:0.9em; margin-bottom:10px;">Try one of these, or type your own question:</div>
                    <div class="chat-suggestions">
                        <button class="chat-suggestion" onclick="askSuggestion(this)">What is the main movement pattern in this lesson?</button>
                        <button class="chat-suggestion" onclick="askSuggestion(this)">How does the teacher use imagery?</button>
                        <button class="chat-suggestion" onclick="askSuggestion(this)">What body parts are involved?</button>
                        <button class="chat-suggestion" onclick="askSuggestion(this)">Summarize this lesson in simple terms</button>
                    </div>
                </div>
            </div>
            <div class="chat-input-row">
                <input type="text" id="chat-input" placeholder="Ask about this lesson..."
                       onkeydown="if(event.key==='Enter') sendChat()">
                <button id="chat-send-btn" onclick="sendChat()">Send</button>
            </div>
        </div>
    </div>

    <!-- Annotated tab (hidden) -->
    <div id="tab-annotated" style="display:none;">
        <div class="progress-bar" id="annotated-progress"></div>
    </div>

    <!-- Detailed tab -->
    <div id="tab-detailed" style="display:none;">
        <div class="tab-intro">The full structured analysis output with all fields, tables, and raw data.</div>
        <div id="detailed-content"></div>
    </div>

<script>
// ── Constants ──
const DOCUMENT_ID = '{esc_id}';
const PROVIDER = new URLSearchParams(window.location.search).get('provider') || '';

// ── State ──
let whisperSegments = null;
let whisperWords = null;
let analysisData = null;
let wordAnnotations = null;
let currentStepIdx = 0;
let audioReady = false;
let autoAdvance = true;
let lessonText = null;
let chatMessages = [];
let _stepAnnotationCache = {{}};
let condensedLoaded = false;
let annotatedLoaded = false;
let detailedRendered = false;
let activeTab = 'steps';

const audio = document.getElementById('audio-player');
const audioContainer = document.getElementById('audio-container');

// ── Tab switching ──

const TAB_ORDER = ['steps', 'condensed', 'concepts', 'chat', 'detailed'];
let conceptsLoaded = false;

function switchTab(tab) {{
    activeTab = tab;
    document.querySelectorAll('.tab-bar button').forEach((b, i) => {{
        b.classList.toggle('active', TAB_ORDER[i] === tab);
    }});
    TAB_ORDER.concat(['annotated']).forEach(t => {{
        const el = document.getElementById('tab-' + t);
        if (el) el.style.display = t === tab ? '' : 'none';
    }});

    if (tab === 'condensed' && !condensedLoaded) loadCondensed();
    if (tab === 'annotated' && !annotatedLoaded) loadAnnotated();
    if (tab === 'concepts' && !conceptsLoaded) loadConcepts();
    if (tab === 'detailed' && !detailedRendered && analysisData) renderDetailed();
    if (tab === 'steps' && analysisData) renderCard();
    if (tab === 'chat') document.getElementById('chat-input').focus();
}}

// ── Concepts loading ──

async function loadConcepts() {{
    conceptsLoaded = true;
    const div = document.getElementById('concepts-content');
    try {{
        const res = await fetch('/api/lesson/' + DOCUMENT_ID + '/concepts');
        const data = await res.json();
        if (data.error) {{
            div.innerHTML = '<div style="color:#999;padding:16px;">No concept data available for this lesson.</div>';
            return;
        }}
        let html = '';
        // Distinctive summary
        if (data.distinctive_summary) {{
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#1a73e8;margin-bottom:6px;">What Makes This Lesson Distinctive</div>';
            html += '<div style="font-size:0.95em;line-height:1.6;color:#333;">' + escConcept(data.distinctive_summary) + '</div>';
            html += '</div>';
        }}
        // Search terms
        if (data.search_terms && data.search_terms.length) {{
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#e65100;margin-bottom:8px;">Search Terms</div>';
            html += '<div style="display:flex;flex-wrap:wrap;gap:6px;">';
            data.search_terms.forEach(t => {{
                html += '<span style="padding:4px 12px;background:#fff3e0;border:1px solid #ffe0b2;border-radius:14px;font-size:0.85em;color:#e65100;">' + escConcept(t) + '</span>';
            }});
            html += '</div></div>';
        }}
        // Concepts by category
        if (data.concepts && data.concepts.length) {{
            const byCategory = {{}};
            data.concepts.forEach(c => {{
                if (!byCategory[c.category]) byCategory[c.category] = [];
                byCategory[c.category].push(c.value);
            }});
            const catColors = {{
                body_region: '#2196f3', movement_pattern: '#4caf50', position: '#9c27b0',
                feldenkrais_concept: '#ff9800', teaching_strategy: '#e91e63', theme: '#607d8b'
            }};
            const catLabels = {{
                body_region: 'Body Region', movement_pattern: 'Movement Pattern', position: 'Position',
                feldenkrais_concept: 'Feldenkrais Concept', teaching_strategy: 'Teaching Strategy', theme: 'Theme'
            }};
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#333;margin-bottom:12px;">Concept Tags</div>';
            Object.keys(byCategory).sort().forEach(cat => {{
                const color = catColors[cat] || '#666';
                const label = catLabels[cat] || cat.replace(/_/g, ' ');
                html += '<div style="margin-bottom:10px;">';
                html += '<div style="font-size:0.8em;font-weight:600;color:' + color + ';margin-bottom:4px;text-transform:capitalize;">' + label + '</div>';
                html += '<div style="display:flex;flex-wrap:wrap;gap:5px;">';
                byCategory[cat].forEach(v => {{
                    html += '<span style="padding:3px 10px;border:1px solid #ddd;border-radius:12px;font-size:0.85em;color:#444;background:#fafafa;">' + escConcept(v) + '</span>';
                }});
                html += '</div></div>';
            }});
            html += '</div>';
        }}
        if (!html) html = '<div style="color:#999;padding:16px;">No concept data available for this lesson.</div>';
        div.innerHTML = html;
    }} catch (e) {{
        div.innerHTML = '<div style="color:#999;padding:16px;">Failed to load concept data.</div>';
    }}
}}

function escConcept(str) {{
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}}

// ── Card rendering ──

function renderCard() {{
    if (!analysisData || !analysisData.steps.length) return;

    document.getElementById('card-placeholder').style.display = 'none';
    const content = document.getElementById('card-content');
    content.style.display = 'flex';

    const step = analysisData.steps[currentStepIdx];
    const group = analysisData.groups.find(g => g.group_number === step.group_number);

    const groupPos = group && group.starting_position ? ' (' + group.starting_position + ')' : '';
    document.getElementById('card-group').textContent =
        group ? 'Group ' + group.group_number + ': ' + group.label + groupPos : '';
    document.getElementById('card-step-label').textContent = 'Step ' + step.step_number;
    if (step.time_start != null && step.time_end != null) {{
        document.getElementById('card-step-time').textContent =
            '(' + fmtTime(step.time_start) + ' \\u2013 ' + fmtTime(step.time_end) + ')';
    }} else {{
        document.getElementById('card-step-time').textContent = '';
    }}
    document.getElementById('card-instruction').textContent = step.instruction || '';
    updateSegmentForTime(audio.currentTime);

    // Somatic bullets
    const somDiv = document.getElementById('analysis-somatic');
    const somUl = document.getElementById('somatic-bullets');
    somUl.innerHTML = '';
    if (step.somatic || step.starting_position) {{
        let hasBullets = false;
        // Starting position as first bullet
        if (step.starting_position) {{
            const li = document.createElement('li');
            li.innerHTML = '<strong>Position:</strong> ' + escHtml(step.starting_position);
            somUl.appendChild(li);
            hasBullets = true;
        }}
        if (step.somatic) {{
            const fields = [
                ['Intent', 'intent'], ['Differentiation', 'differentiation'],
                ['Activation', 'activation'], ['Inhibition', 'inhibition'],
                ['Transmission Route', 'transmission_route'], ['Ground Push', 'ground_push'],
                ['Mechanical Directive', 'mechanical_directive'],
                ['Attentional Cue', 'attentional_cue'],
            ];
            fields.forEach(([label, key]) => {{
                const val = step.somatic[key];
                if (val && val.toLowerCase() !== 'none') {{
                    const li = document.createElement('li');
                    li.innerHTML = '<strong>' + label + ':</strong> ' + escHtml(val);
                    somUl.appendChild(li);
                    hasBullets = true;
                }}
            }});
            if (step.somatic.clarified) {{
                const li = document.createElement('li');
                li.innerHTML = '<strong>Clarified:</strong> ' + escHtml(step.somatic.clarified);
                li.style.fontStyle = 'italic';
                somUl.prepend(li);
                hasBullets = true;
            }}
        }}
        somDiv.style.display = hasBullets ? '' : 'none';
    }} else {{
        somDiv.style.display = 'none';
    }}

    // Language bullets
    const langDiv = document.getElementById('analysis-lang');
    const langUl = document.getElementById('lang-bullets');
    langUl.innerHTML = '';
    if (step.language) {{
        const fields = [
            ['Explanatory', 'explanatory_language'], ['Attention Cues', 'attention_cues'],
            ['Effort', 'effort_language'], ['Imagery', 'image_language'],
            ['Distinctive', 'distinctive_phrasing'], ['Sensory Inquiry', 'sensory_inquiry'],
        ];
        let hasBullets = false;
        fields.forEach(([label, key]) => {{
            const val = step.language[key];
            if (val && val.toLowerCase() !== 'none') {{
                const li = document.createElement('li');
                li.innerHTML = '<strong>' + label + ':</strong> ' + escHtml(val);
                langUl.appendChild(li);
                hasBullets = true;
            }}
        }});
        langDiv.style.display = hasBullets ? '' : 'none';
    }} else {{
        langDiv.style.display = 'none';
    }}

    // Transcript segments
    const segDetails = document.getElementById('segment-details');
    const segList = document.getElementById('segment-list');
    segList.innerHTML = '';
    const segs = (step.segments || []).filter(s => s.text);
    if (segs.length) {{
        const n = segs.length;
        segDetails.querySelector('summary').textContent =
            'Original text (' + n + ' segment' + (n !== 1 ? 's' : '') + ')';
        const container = document.createElement('div');
        container.className = 'seg-container';
        segs.forEach(s => {{
            const line = document.createElement('div');
            line.className = 'seg-line';
            line.innerHTML = '<span class="seg-id">[' + s.id + ']</span> ' + escHtml(s.text);
            container.appendChild(line);
        }});
        segList.appendChild(container);
        segDetails.style.display = '';
        segDetails.removeAttribute('open');
    }} else {{
        segDetails.style.display = 'none';
    }}

    // Navigation
    document.getElementById('nav-prev').disabled = (currentStepIdx === 0);
    document.getElementById('nav-next').disabled = (currentStepIdx === analysisData.steps.length - 1);
    document.getElementById('step-counter').textContent =
        'Step ' + (currentStepIdx + 1) + ' of ' + analysisData.steps.length;
}}

function navigate(delta) {{
    if (!analysisData) return;
    const newIdx = currentStepIdx + delta;
    if (newIdx < 0 || newIdx >= analysisData.steps.length) return;
    autoAdvance = false;
    currentStepIdx = newIdx;
    renderCard();
    const step = analysisData.steps[currentStepIdx];
    if (step.time_start != null && audioReady) {{
        audio.currentTime = step.time_start;
    }}
    setTimeout(() => {{ autoAdvance = true; }}, 500);
}}

// ── Audio sync ──

audio.addEventListener('timeupdate', function() {{
    const t = audio.currentTime;
    updateSegmentForTime(t);
    if (analysisData && autoAdvance) {{
        const stepIdx = findStepForTime(t);
        if (stepIdx !== -1 && stepIdx !== currentStepIdx) {{
            currentStepIdx = stepIdx;
            renderCard();
        }}
    }}
}});

function getAnnotationsForStep(stepNumber) {{
    if (!wordAnnotations) return [];
    const step = wordAnnotations.find(s => s.step_number === stepNumber);
    return step ? (step.phrases || []) : [];
}}

function buildStepAnnotationMap(stepNumber) {{
    if (_stepAnnotationCache[stepNumber]) return _stepAnnotationCache[stepNumber];
    const phrases = getAnnotationsForStep(stepNumber);
    const annMap = {{}};
    if (!phrases.length || !whisperWords || !analysisData) {{
        _stepAnnotationCache[stepNumber] = annMap;
        return annMap;
    }}
    const step = analysisData.steps.find(s => s.step_number === stepNumber);
    if (!step || !step.segments || !step.segments.length) {{
        _stepAnnotationCache[stepNumber] = annMap;
        return annMap;
    }}
    let stepStart = Infinity, stepEnd = -Infinity;
    for (const seg of step.segments) {{
        if (seg.time_start != null && seg.time_start < stepStart) stepStart = seg.time_start;
        if (seg.time_end != null && seg.time_end > stepEnd) stepEnd = seg.time_end;
    }}
    const stepWords = [];
    for (const w of whisperWords) {{
        if (w.start >= (stepStart - 0.5) && w.end <= (stepEnd + 0.5)) stepWords.push(w);
        if (w.start > stepEnd + 1) break;
    }}
    if (!stepWords.length) {{
        _stepAnnotationCache[stepNumber] = annMap;
        return annMap;
    }}
    const lcWords = stepWords.map(w => w.word.toLowerCase().replace(/[.,!?;:'"]/g, ''));
    for (const phrase of phrases) {{
        const phraseTokens = phrase.text.toLowerCase().replace(/[.,!?;:'"]/g, '').split(/\\s+/).filter(t => t);
        if (!phraseTokens.length) continue;
        for (let i = 0; i <= lcWords.length - phraseTokens.length; i++) {{
            let match = true;
            for (let j = 0; j < phraseTokens.length; j++) {{
                if (lcWords[i + j] !== phraseTokens[j]) {{ match = false; break; }}
            }}
            if (match) {{
                for (let j = 0; j < phraseTokens.length; j++) {{
                    annMap[stepWords[i + j].start] = {{ category: phrase.category, note: phrase.note, text: phrase.text }};
                }}
                break;
            }}
        }}
    }}
    _stepAnnotationCache[stepNumber] = annMap;
    return annMap;
}}

function renderTextWithWordHighlight(text, t, timeStart, timeEnd, stepNumber) {{
    if (!whisperWords || !whisperWords.length) return escHtml(text);
    const rangeWords = [];
    for (const w of whisperWords) {{
        if (w.start >= (timeStart - 0.5) && w.end <= (timeEnd + 0.5)) rangeWords.push(w);
        if (w.start > timeEnd + 1) break;
    }}
    if (!rangeWords.length) return escHtml(text);
    const annMap = stepNumber != null ? buildStepAnnotationMap(stepNumber) : {{}};
    let activePhrase = null;
    for (let i = 0; i < rangeWords.length; i++) {{
        const w = rangeWords[i];
        if (t >= w.start - 0.05 && t <= w.end + 0.05) {{
            const ann = annMap[w.start];
            if (ann) activePhrase = ann.text;
            break;
        }}
    }}
    let html = '';
    let currentNote = null;
    for (let i = 0; i < rangeWords.length; i++) {{
        const w = rangeWords[i];
        const isCurrent = (t >= w.start - 0.05 && t <= w.end + 0.05);
        const ann = annMap[w.start];
        if (i > 0) html += ' ';
        let wordHtml = escHtml(w.word);
        if (isCurrent) {{
            wordHtml = '<strong style="color:#1a237e;font-size:1.05em;">' + wordHtml + '</strong>';
            if (ann) currentNote = ann;
        }}
        if (ann) {{
            const isActive = activePhrase && ann.text === activePhrase;
            const cls = 'word-' + ann.category + (isActive ? ' word-active' : '');
            wordHtml = '<span class="' + cls + '">' + wordHtml + '</span>';
        }}
        html += wordHtml;
    }}
    const noteLine = document.getElementById('annotation-note');
    if (noteLine) {{
        if (currentNote) {{
            noteLine.innerHTML = '<span class="note-category note-cat-' + currentNote.category + '">'
                + currentNote.category + '</span> ' + escHtml(currentNote.note);
        }} else {{
            noteLine.innerHTML = '';
        }}
    }}
    return html;
}}

function updateSegmentForTime(t) {{
    const segBox = document.getElementById('segment-text');
    if (!segBox) return;
    if (analysisData) {{
        const step = analysisData.steps[currentStepIdx];
        const stepNum = step ? step.step_number : null;
        if (step && step.segments) {{
            for (const seg of step.segments) {{
                if (seg.time_start != null && t >= seg.time_start && t <= seg.time_end) {{
                    segBox.innerHTML = renderTextWithWordHighlight(seg.text, t, seg.time_start, seg.time_end, stepNum);
                    return;
                }}
            }}
            if (step.segments.length > 0) {{
                let closest = step.segments[0];
                let minDist = Infinity;
                for (const seg of step.segments) {{
                    if (seg.time_start != null) {{
                        const dist = Math.abs(seg.time_start - t);
                        if (dist < minDist) {{ minDist = dist; closest = seg; }}
                    }}
                }}
                segBox.innerHTML = renderTextWithWordHighlight(
                    closest.text || '', t, closest.time_start || 0, closest.time_end || 0, stepNum);
            }}
            return;
        }}
    }}
    if (whisperSegments) {{
        for (const ws of whisperSegments) {{
            if (t >= ws.start && t <= ws.end) {{
                segBox.innerHTML = renderTextWithWordHighlight(ws.text, t, ws.start, ws.end, null);
                return;
            }}
        }}
    }}
}}

function findStepForTime(t) {{
    if (!analysisData) return -1;
    for (let i = 0; i < analysisData.steps.length; i++) {{
        const s = analysisData.steps[i];
        if (s.time_start != null && s.time_end != null) {{
            if (t >= s.time_start && t <= s.time_end) return i;
        }}
    }}
    return -1;
}}

// ── Condensed view ──

function renderSettingCard(s, label) {{
    let html = '<div class="condensed-card">';
    html += '<div class="setting-tag">' + escHtml(label) + '</div>';
    html += '<h3>' + escHtml(s.title || '') + '</h3>';
    if (s.somatic_intent) {{
        html += '<p style="font-style:italic;margin:8px 0 12px 0;color:#444;">' + escHtml(s.somatic_intent) + '</p>';
    }}
    const settingAttrs = [];
    if (s.space) settingAttrs.push('<li><strong>Space:</strong> ' + escHtml(s.space) + '</li>');
    if (s.teacher_presence) settingAttrs.push('<li><strong>Teacher Presence:</strong> ' + escHtml(s.teacher_presence) + '</li>');
    if (s.arc) settingAttrs.push('<li><strong>Arc:</strong> ' + escHtml(s.arc) + '</li>');
    if (settingAttrs.length) {{
        html += '<ul style="margin:0;padding-left:20px;color:#444;font-size:0.95em;">' + settingAttrs.join('') + '</ul>';
    }}
    html += '</div>';
    return html;
}}

function getGroupTimeRange(groupNumber) {{
    if (!analysisData || !analysisData.steps) return null;
    let minStart = Infinity, maxEnd = -Infinity;
    for (const s of analysisData.steps) {{
        if (s.group_number === groupNumber && s.time_start != null && s.time_end != null) {{
            if (s.time_start < minStart) minStart = s.time_start;
            if (s.time_end > maxEnd) maxEnd = s.time_end;
        }}
    }}
    if (minStart === Infinity) return null;
    return {{ start: minStart, end: maxEnd }};
}}

function renderGroupCards(cards) {{
    let html = '';
    cards.forEach(card => {{
        html += '<div class="condensed-card group-card">';
        const tr = getGroupTimeRange(card.group_number);
        const timeStr = tr ? ' <span style="font-weight:normal;color:#888;">(' + fmtTime(tr.start) + '\\u2013' + fmtTime(tr.end) + ')</span>' : '';
        html += '<div class="group-tag">Group ' + card.group_number + ': ' + escHtml(card.group_label || '') + timeStr + '</div>';
        html += '<h3>' + escHtml(card.title || '') + '</h3>';
        if (card.the_action) {{
            html += '<p style="margin:8px 0 12px 0;color:#444;">' + escHtml(card.the_action) + '</p>';
        }}
        const groupAttrs = [];
        // Look up starting_position from analysisData groups
        const adGroup = analysisData && analysisData.groups ? analysisData.groups.find(g => g.group_number === card.group_number) : null;
        if (adGroup && adGroup.starting_position) groupAttrs.push('<li><strong>Position:</strong> ' + escHtml(adGroup.starting_position) + '</li>');
        if (card.the_body) groupAttrs.push('<li><strong>Body:</strong> ' + escHtml(card.the_body) + '</li>');
        if (card.the_attention) groupAttrs.push('<li><strong>Attention:</strong> ' + escHtml(card.the_attention) + '</li>');
        if (card.the_intention) groupAttrs.push('<li><strong>Intention:</strong> ' + escHtml(card.the_intention) + '</li>');
        if (card.the_voice && card.the_voice.toLowerCase() !== 'none') groupAttrs.push('<li><strong>Voice:</strong> ' + escHtml(card.the_voice) + '</li>');
        if (groupAttrs.length) {{
            html += '<ul style="margin:0;padding-left:20px;color:#444;font-size:0.95em;">' + groupAttrs.join('') + '</ul>';
        }}
        html += '</div>';
    }});
    return html;
}}

function renderCondensedCards(data) {{
    const settingDiv = document.getElementById('condensed-setting-card');
    const groupsDiv = document.getElementById('condensed-group-cards');
    settingDiv.innerHTML = '';
    groupsDiv.innerHTML = '';
    const segments = data.segments || [];
    if (segments.length <= 1) {{
        const seg = segments[0] || {{}};
        if (seg.setting) settingDiv.innerHTML = renderSettingCard(seg.setting, 'Lesson Setting');
        if (seg.group_cards) groupsDiv.innerHTML = renderGroupCards(seg.group_cards);
    }} else {{
        // Depth-first: each phase's setting + groups together
        let allHtml = '';
        segments.forEach(seg => {{
            const segLabel = 'Phase ' + seg.segment_number + ': ' + (seg.theme || '');
            // Phase header with time range
            let segTimeStr = '';
            if (seg.group_cards && seg.group_cards.length && analysisData) {{
                let sMin = Infinity, sMax = -Infinity;
                seg.group_cards.forEach(c => {{
                    const tr = getGroupTimeRange(c.group_number);
                    if (tr) {{ if (tr.start < sMin) sMin = tr.start; if (tr.end > sMax) sMax = tr.end; }}
                }});
                if (sMin !== Infinity) segTimeStr = ' (' + fmtTime(sMin) + '\\u2013' + fmtTime(sMax) + ')';
            }}
            allHtml += '<div class="phase-header">'
                + escHtml(segLabel) + '<span class="phase-time">' + segTimeStr + '</span>'
                + (seg.transition_note ? '<span class="phase-note">' + escHtml(seg.transition_note) + '</span>' : '')
                + '</div>';
            // Setting card for this phase
            if (seg.setting) allHtml += renderSettingCard(seg.setting, segLabel + ' Setting');
            // Group cards for this phase
            if (seg.group_cards) allHtml += renderGroupCards(seg.group_cards);
        }});
        groupsDiv.innerHTML = allHtml;
    }}
}}

async function loadCondensed() {{
    condensedLoaded = true;
    showStatus('<span class="spinner-inline"></span> Generating condensed lesson cards...');
    try {{
        const form = new FormData();
        form.append('lesson_text', lessonText);
        form.append('provider', PROVIDER);
        const res = await fetch('/api/pass0b-condensed', {{ method: 'POST', body: form }});
        const data = await res.json();
        if (data.error) {{ showStatus('Error: ' + data.error, true); return; }}
        renderCondensedCards(data);
        clearStatus();
        const timing = data.cached ? 'cached' : (data.elapsed || '?') + 's';
        const segs = data.segments || [];
        const totalCards = segs.reduce((n, seg) => n + (seg.group_cards ? seg.group_cards.length : 0), 0);
        const segLabel = segs.length > 1 ? ' | ' + segs.length + ' phases' : '';
        document.getElementById('condensed-progress').textContent =
            totalCards + ' movement sequences' + segLabel + ' | ' + timing;
    }} catch (e) {{
        showStatus('Error: ' + e.message, true);
    }}
}}

async function loadAnnotated() {{
    annotatedLoaded = true;
    showStatus('<span class="spinner-inline"></span> Generating word-level language annotations...');
    try {{
        const form = new FormData();
        form.append('lesson_text', lessonText);
        form.append('selected_category', 'db:0');
        form.append('selected_lesson', DOCUMENT_ID);
        form.append('provider', PROVIDER);
        const res = await fetch('/api/pass0b-word-lang-json', {{ method: 'POST', body: form }});
        const data = await res.json();
        if (data.error) {{ showStatus('Error: ' + data.error, true); return; }}
        wordAnnotations = data.word_annotations || [];
        _stepAnnotationCache = {{}};
        clearStatus();
        const timing = data.cached ? 'cached' : (data.elapsed || '?') + 's';
        const totalPhrases = wordAnnotations.reduce((n, s) => n + (s.phrases ? s.phrases.length : 0), 0);
        document.getElementById('annotated-progress').textContent =
            totalPhrases + ' annotations across ' + wordAnnotations.length + ' steps | ' + timing;
        // Switch back to steps view to see annotations in context
        switchTab('steps');
    }} catch (e) {{
        showStatus('Error: ' + e.message, true);
    }}
}}

// ── Detailed view ──

function renderDetailed() {{
    if (!analysisData) return;
    detailedRendered = true;
    const groups = analysisData.groups || [];
    const steps = analysisData.steps || [];
    const stepLookup = {{}};
    steps.forEach(s => {{ stepLookup[s.step_number] = s; }});

    function fmtTimeRange(step) {{
        if (step.time_start == null || step.time_end == null) return '';
        return ' (' + fmtTime(step.time_start) + ' \u2013 ' + fmtTime(step.time_end) + ')';
    }}

    function renderBullets(obj, fields) {{
        let items = '';
        fields.forEach(([label, key]) => {{
            const val = obj[key];
            if (val && val.toLowerCase() !== 'none') {{
                items += '<li><strong>' + label + ':</strong> ' + escHtml(val) + '</li>';
            }}
        }});
        return items ? '<ul class="detailed-bullets">' + items + '</ul>' : '';
    }}

    function renderSegments(step) {{
        const segs = (step.segments || []).filter(s => s.text);
        if (!segs.length) return '';
        const n = segs.length;
        const label = 'Original text (' + n + ' segment' + (n !== 1 ? 's' : '') + ')';
        let lines = '';
        segs.forEach(s => {{
            lines += '<div style="margin: 2px 0;"><span style="color: #999; font-size: 0.85em;">[' + s.id + ']</span> ' + escHtml(s.text) + '</div>';
        }});
        return '<details style="margin-top: 6px;">' +
            '<summary style="cursor: pointer; font-size: 0.85em; color: #888;">' + label + '</summary>' +
            '<div style="margin-top: 4px; padding: 6px 10px; background: #fafafa; border-radius: 4px; font-size: 0.85em; color: #555;">' + lines + '</div>' +
            '</details>';
    }}

    function renderStep(step) {{
        if (step.relevance === false) return '';
        const timeStr = fmtTimeRange(step);
        let html = '<div class="detailed-step">';
        if (step.starting_position) {{
            html += '<div class="detailed-step-position">' + escHtml(step.starting_position) + '</div>';
        }}
        html += '<div class="detailed-step-header"><strong>Step ' + step.step_number + '</strong>';
        html += '<span class="detailed-step-time">' + escHtml(timeStr) + '</span>';
        html += ' &mdash; <em>' + escHtml(step.instruction || '') + '</em></div>';
        if (step.somatic) {{
            if (step.somatic.clarified) {{
                html += '<div class="detailed-clarified">' + escHtml(step.somatic.clarified) + '</div>';
            }}
            const somBullets = renderBullets(step.somatic, [
                ['Intent', 'intent'], ['Differentiation', 'differentiation'],
                ['Activation', 'activation'], ['Inhibition', 'inhibition'],
                ['Transmission Route', 'transmission_route'], ['Ground Push', 'ground_push'],
                ['Mechanical Directive', 'mechanical_directive'],
                ['Attentional Cue', 'attentional_cue'],
            ]);
            if (somBullets) {{
                html += '<div class="detailed-section-label">Somatic</div>' + somBullets;
            }}
        }}
        if (step.language) {{
            const langBullets = renderBullets(step.language, [
                ['Explanatory', 'explanatory_language'], ['Attention Cues', 'attention_cues'],
                ['Effort', 'effort_language'], ['Imagery', 'image_language'],
                ['Distinctive', 'distinctive_phrasing'], ['Sensory Inquiry', 'sensory_inquiry'],
            ]);
            if (langBullets) {{
                html += '<div class="detailed-section-label">Language</div>' + langBullets;
            }}
        }}
        html += renderSegments(step);
        html += '</div>';
        return html;
    }}

    let html = '';
    const renderedSteps = new Set();
    const totalSteps = steps.length;
    const relevant = steps.filter(s => s.relevance !== false).length;
    const irrelevant = totalSteps - relevant;
    html += '<p style="color: #666; font-size: 0.9em;">' + totalSteps + ' steps across ' +
        groups.length + ' groups, ' + relevant + ' lesson-relevant' +
        (irrelevant ? ', ' + irrelevant + ' omitted' : '') + '</p>';

    groups.forEach(g => {{
        const groupStepNums = g.steps || [];
        const groupRelevant = groupStepNums.filter(sn => {{
            const s = stepLookup[sn];
            return s && s.relevance !== false;
        }});
        if (!groupRelevant.length) {{
            groupStepNums.forEach(sn => renderedSteps.add(sn));
            return;
        }}
        html += '<div class="detailed-group-header">';
        html += '<strong>Group ' + g.group_number + ': ' + escHtml(g.label || '') + '</strong>';
        if (g.starting_position) {{
            html += '<div class="detailed-group-position">' + escHtml(g.starting_position) + '</div>';
        }}
        if (g.description) {{
            html += '<div class="detailed-group-desc">' + escHtml(g.description) + '</div>';
        }}
        html += '</div>';
        groupStepNums.forEach(sn => {{
            renderedSteps.add(sn);
            const step = stepLookup[sn];
            if (step) html += renderStep(step);
        }});
    }});

    const ungrouped = steps.filter(s => !renderedSteps.has(s.step_number) && s.relevance !== false);
    if (ungrouped.length) {{
        html += '<div class="detailed-group-header" style="background: #fff3cd; border-left-color: #ffc107;">';
        html += '<strong style="color: #856404;">Ungrouped Steps</strong></div>';
        ungrouped.forEach(step => {{ html += renderStep(step); }});
    }}

    document.getElementById('detailed-content').innerHTML = html;
}}

// ── Keyboard nav ──

document.addEventListener('keydown', function(e) {{
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (e.key === 'ArrowLeft') {{ navigate(-1); e.preventDefault(); }}
    if (e.key === 'ArrowRight') {{ navigate(1); e.preventDefault(); }}
    if (e.key === ' ') {{
        if (audioReady) {{
            if (audio.paused) audio.play(); else audio.pause();
            e.preventDefault();
        }}
    }}
}});

// ── Helpers ──

function fmtTime(seconds) {{
    if (seconds == null) return '';
    const s = Math.floor(seconds);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
    return m + ':' + String(sec).padStart(2, '0');
}}

function escHtml(str) {{
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}}

function showStatus(msg, isError) {{
    const el = document.getElementById('status-area');
    el.innerHTML = '<div class="status-msg' + (isError ? ' error' : '') + '">' + msg + '</div>';
}}

function clearStatus() {{
    document.getElementById('status-area').innerHTML = '';
}}

// ── Chat ──

function getChatContextConfig() {{
    return {{
        lesson_text: document.getElementById('ctx-lesson-text').checked,
        pass_a: document.getElementById('ctx-pass-a').checked,
        somatic: document.getElementById('ctx-somatic').checked,
        language: document.getElementById('ctx-language').checked,
        somatic_summary: document.getElementById('ctx-somatic-summary').checked,
        language_summary: document.getElementById('ctx-language-summary').checked,
        condensed: document.getElementById('ctx-condensed').checked,
    }};
}}

function renderChatMessages() {{
    const container = document.getElementById('chat-messages');
    container.innerHTML = chatMessages.map(m => {{
        const cls = m.role === 'user' ? 'user' : (m.role === 'error' ? 'error' : 'assistant');
        const content = m.role === 'assistant' ? marked.parse(m.content) : escHtml(m.content);
        return '<div class="chat-msg ' + cls + '">' + content + '</div>';
    }}).join('');
    container.scrollTop = container.scrollHeight;
}}

function askSuggestion(btn) {{
    document.getElementById('chat-input').value = btn.textContent;
    sendChat();
}}

async function sendChat() {{
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;
    // Remove hint on first message
    const hint = document.getElementById('chat-hint');
    if (hint) hint.remove();
    if (!lessonText) {{
        chatMessages.push({{role: 'error', content: 'No lesson text available.'}});
        renderChatMessages();
        return;
    }}
    chatMessages.push({{role: 'user', content: text}});
    input.value = '';
    renderChatMessages();
    const btn = document.getElementById('chat-send-btn');
    btn.disabled = true;
    btn.textContent = '...';
    try {{
        const apiMessages = chatMessages.filter(m => m.role === 'user' || m.role === 'assistant');
        const res = await fetch('/api/lesson-chat', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
                messages: apiMessages,
                provider: PROVIDER,
                context_config: getChatContextConfig(),
                lesson_text: lessonText,
            }}),
        }});
        const data = await res.json();
        if (data.error) {{
            chatMessages.push({{role: 'error', content: data.error}});
        }} else {{
            chatMessages.push({{role: 'assistant', content: data.response}});
        }}
    }} catch (e) {{
        chatMessages.push({{role: 'error', content: 'Request failed: ' + e.message}});
    }}
    btn.disabled = false;
    btn.textContent = 'Send';
    renderChatMessages();
}}

// ── Page init ──

(async function init() {{
    try {{
        // 1. Fetch lesson info
        const infoRes = await fetch('/api/lesson-info/' + encodeURIComponent(DOCUMENT_ID));
        const info = await infoRes.json();
        if (info.error) {{
            showStatus('Lesson not found.', true);
            document.getElementById('card-placeholder').innerHTML = 'Lesson not found.';
            return;
        }}
        document.title = info.title || 'Lesson';
        document.getElementById('lesson-title').textContent = info.title || 'Untitled Lesson';
        let sourceLine = info.source_name || '';
        if (info.source_url) {{
            sourceLine = '<a href="' + escHtml(info.source_url) + '" target="_blank">' + escHtml(sourceLine || 'Source') + '</a>';
        }}
        document.getElementById('source-line').innerHTML = sourceLine;
        lessonText = info.text || '';

        // 2. Load audio + whisper (parallel) if has_audio
        if (info.has_audio) {{
            const [audioRes, segRes, wordRes] = await Promise.all([
                fetch('/api/audio-url/' + encodeURIComponent(DOCUMENT_ID)),
                fetch('/api/whisper-segments/' + encodeURIComponent(DOCUMENT_ID)),
                fetch('/api/whisper-words/' + encodeURIComponent(DOCUMENT_ID)),
            ]);
            const audioData = await audioRes.json();
            const segData = await segRes.json();
            const wordData = await wordRes.json();
            if (audioData.audio_url) {{
                audio.src = audioData.audio_url;
                audioContainer.classList.remove('audio-hidden');
                audioReady = true;
            }}
            if (segData.segments) whisperSegments = segData.segments;
            if (wordData.words) whisperWords = wordData.words;
        }} else {{
            document.getElementById('no-audio-msg').style.display = '';
        }}

        // 3. Auto-run analysis
        if (!lessonText) {{
            document.getElementById('card-placeholder').innerHTML = 'No lesson text available.';
            return;
        }}
        showStatus('<span class="spinner-inline"></span> Running analysis...');
        const form = new FormData();
        form.append('lesson_text', lessonText);
        form.append('selected_category', 'db:0');
        form.append('selected_lesson', DOCUMENT_ID);
        form.append('provider', PROVIDER);
        const res = await fetch('/api/pass0b-combined-json', {{ method: 'POST', body: form }});
        const data = await res.json();
        if (data.error) {{
            showStatus('Error: ' + data.error, true);
            document.getElementById('card-placeholder').innerHTML = 'Analysis failed.';
            return;
        }}
        analysisData = data;
        currentStepIdx = 0;
        renderCard();
        clearStatus();
        const somTime = data.cached_somatic ? 'cached' : (data.elapsed_somatic || '?') + 's';
        const langTime = data.cached_lang ? 'cached' : (data.elapsed_lang || '?') + 's';
        document.getElementById('progress-bar').textContent =
            data.steps.length + ' steps | Somatic: ' + somTime + ', Language: ' + langTime;
        // Pre-load condensed data for Outline tab
        if (!condensedLoaded) loadCondensed();
    }} catch (e) {{
        showStatus('Error loading lesson: ' + e.message, true);
        document.getElementById('card-placeholder').innerHTML = 'Failed to load lesson.';
    }}
}})();

</script>
</body>
</html>"""


def build_pass0b_player_html(search_mode: bool = False) -> str:
    """Build the Pass 0b audio player page HTML."""
    categories, display_names = get_available_lessons(public_only=search_mode)

    category_options = ['<option value="">-- Select category --</option>']
    for cat_name in categories.keys():
        cat_display = display_names.get(cat_name, cat_name.replace("_", " ").title())
        category_options.append(f'<option value="{cat_name}">{cat_display}</option>')
    category_select = "\n".join(category_options)

    lessons_json = json.dumps({
        cat: [(t[0], t[1], t[2] if len(t) > 2 else "") for t in lessons]
        for cat, lessons in categories.items()
    })

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Pass 0b: Audio Player</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1500px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
            background: #fafafa;
        }}
        h1 {{ color: #333; margin-bottom: 4px; }}
        .nav-links {{ margin-bottom: 16px; font-size: 0.9em; }}
        .nav-links a {{ color: #007bff; text-decoration: none; margin-right: 16px; }}
        .nav-links a:hover {{ text-decoration: underline; }}

        .controls {{
            display: flex; gap: 8px; align-items: end; flex-wrap: wrap;
            margin-bottom: 16px;
        }}
        .controls .field {{ flex: 1; min-width: 150px; }}
        .controls label {{ font-size: 0.8em; font-weight: 600; color: #555; display: block; margin-bottom: 2px; }}
        .controls select {{
            width: 100%; padding: 8px; font-size: 13px;
            border: 1px solid #ccc; border-radius: 4px;
        }}
        .controls button {{
            padding: 8px 20px; font-size: 14px; border: none; border-radius: 4px;
            cursor: pointer; font-weight: 600; white-space: nowrap;
        }}
        .btn-run {{ background: #007bff; color: white; }}
        .btn-run:hover {{ background: #0056b3; }}
        .btn-run:disabled {{ background: #ccc; cursor: not-allowed; }}
        .btn-condensed {{ background: #6c757d; color: white; }}
        .btn-condensed:hover {{ background: #545b62; }}
        .btn-condensed:disabled {{ background: #ccc; cursor: not-allowed; }}
        .btn-annotate {{ background: #9c27b0; color: white; }}
        .btn-annotate:hover {{ background: #7b1fa2; }}
        .btn-annotate:disabled {{ background: #ccc; cursor: not-allowed; }}

        /* Word-level annotation colors — muted underline by default, lit up when active */
        .word-directive {{ border-bottom: 2px solid rgba(97, 97, 97, 0.3); }}
        .word-directive.word-active {{ background: rgba(97, 97, 97, 0.18); border-bottom-color: #616161; }}
        .word-attention {{ border-bottom: 2px solid rgba(255, 152, 0, 0.3); }}
        .word-attention.word-active {{ background: rgba(255, 152, 0, 0.18); border-bottom-color: #ff9800; }}
        .word-imagery {{ border-bottom: 2px solid rgba(156, 39, 176, 0.3); }}
        .word-imagery.word-active {{ background: rgba(156, 39, 176, 0.18); border-bottom-color: #9c27b0; }}
        .word-inquiry {{ border-bottom: 2px solid rgba(33, 150, 243, 0.3); }}
        .word-inquiry.word-active {{ background: rgba(33, 150, 243, 0.18); border-bottom-color: #2196f3; }}
        .word-distinctive {{ border-bottom: 2px solid rgba(233, 30, 99, 0.3); }}
        .word-distinctive.word-active {{ background: rgba(233, 30, 99, 0.18); border-bottom-color: #e91e63; }}
        .note-line {{
            font-size: 0.82em; color: #555; min-height: 1.4em;
            padding: 4px 8px; margin-top: 4px;
            border-top: 1px solid #f0e68c;
            font-style: italic;
        }}
        .note-category {{
            font-weight: 600; font-style: normal; font-size: 0.85em;
            text-transform: uppercase; letter-spacing: 0.3px; margin-right: 4px;
        }}
        .note-cat-directive {{ color: #616161; }}
        .note-cat-attention {{ color: #e65100; }}
        .note-cat-imagery {{ color: #7b1fa2; }}
        .note-cat-inquiry {{ color: #1565c0; }}
        .note-cat-distinctive {{ color: #c2185b; }}

        /* Condensed cards */
        .condensed-card {{
            background: #fff; border: 1px solid #e0e0e0; border-radius: 10px;
            padding: 24px; margin-bottom: 16px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }}
        .condensed-card h3 {{
            margin: 0 0 4px 0; font-size: 1.2em; color: #222;
        }}
        .condensed-card .group-tag {{
            font-size: 0.8em; font-weight: 700; color: #1a73e8;
            text-transform: uppercase; letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}
        .condensed-card .setting-tag {{
            font-size: 0.8em; font-weight: 700; color: #e65100;
            text-transform: uppercase; letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}
        .pov-section {{
            margin-bottom: 10px; padding: 10px 14px;
            border-radius: 6px; border-left: 4px solid;
            font-size: 0.92em; line-height: 1.55;
        }}
        .pov-section .pov-label {{
            font-size: 0.75em; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.5px; margin-bottom: 4px;
        }}
        .pov-action {{ background: #f5f5f5; border-color: #616161; }}
        .pov-action .pov-label {{ color: #616161; }}
        .pov-body {{ background: #e8f5e9; border-color: #4caf50; }}
        .pov-body .pov-label {{ color: #2e7d32; }}
        .pov-attention {{ background: #fff3e0; border-color: #ff9800; }}
        .pov-attention .pov-label {{ color: #e65100; }}
        .pov-intention {{ background: #e3f2fd; border-color: #2196f3; }}
        .pov-intention .pov-label {{ color: #1565c0; }}
        .pov-voice {{ background: #f3e5f5; border-color: #9c27b0; }}
        .pov-voice .pov-label {{ color: #7b1fa2; }}
        .setting-field {{
            margin-bottom: 10px; padding: 10px 14px;
            border-radius: 6px; border-left: 4px solid #e65100;
            background: #fff8f0; font-size: 0.92em; line-height: 1.55;
        }}
        .setting-field .field-label {{
            font-size: 0.75em; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.5px; margin-bottom: 4px; color: #e65100;
        }}

        /* Audio player */
        .audio-container {{
            margin-bottom: 16px; padding: 12px 16px;
            background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
        }}
        .audio-container audio {{ width: 100%; }}
        .audio-hidden {{ display: none; }}

        /* Card */
        .card {{
            background: #fff; border: 1px solid #e0e0e0; border-radius: 10px;
            padding: 24px; margin-bottom: 16px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            min-height: 300px;
            display: flex; flex-direction: column;
        }}
        .card-placeholder {{
            color: #999; text-align: center; padding: 60px 20px;
            font-size: 1.1em;
        }}
        .group-label {{
            font-size: 0.8em; font-weight: 700; color: #1a73e8;
            text-transform: uppercase; letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        .step-header {{
            font-size: 1.15em; font-weight: 700; color: #222;
            margin-bottom: 2px;
        }}
        .step-time {{ color: #888; font-weight: 400; font-size: 0.85em; }}
        .step-position {{
            color: #888; font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.3px;
            margin-bottom: 4px;
        }}
        .step-instruction {{
            color: #444; font-style: italic; margin-bottom: 12px;
            font-size: 0.95em;
        }}

        /* Segment highlight box */
        .segment-box {{
            background: #fffde7; border: 2px solid #fdd835;
            border-radius: 6px; padding: 12px 16px;
            margin-top: 10px; font-size: 0.95em;
            line-height: 1.5; color: #555;
            min-height: 50px;
        }}
        .segment-box strong {{
            transition: color 0.15s;
        }}

        /* Analysis sections */
        .analysis-section {{
            margin-bottom: 12px;
        }}
        .analysis-section h4 {{
            font-size: 0.8em; font-weight: 700; color: #666;
            text-transform: uppercase; letter-spacing: 0.5px;
            margin: 0 0 6px 0; padding-bottom: 4px;
            border-bottom: 1px solid #eee;
        }}
        .analysis-section ul {{
            margin: 0; padding-left: 18px; font-size: 0.9em; color: #444;
        }}
        .analysis-section li {{ margin-bottom: 4px; }}
        .analysis-section strong {{ color: #333; }}

        /* Navigation */
        .card-nav {{
            display: flex; justify-content: space-between; align-items: center;
            margin-top: auto; padding-top: 16px;
            border-top: 1px solid #eee;
        }}
        .card-nav button {{
            padding: 6px 16px; font-size: 13px; border: 1px solid #ccc;
            border-radius: 4px; background: #fff; cursor: pointer;
            color: #333; font-weight: 500;
        }}
        .card-nav button:hover {{ background: #f0f0f0; }}
        .card-nav button:disabled {{ opacity: 0.3; cursor: not-allowed; }}
        .card-nav .step-counter {{ color: #888; font-size: 0.85em; }}

        /* Progress bar */
        .progress-bar {{
            font-size: 0.85em; color: #888; text-align: center;
        }}

        /* Loading / status */
        .status-msg {{
            text-align: center; padding: 12px; color: #666;
            font-size: 0.9em;
        }}
        .status-msg.error {{ color: #c62828; background: #ffebee; border-radius: 6px; }}
        .spinner-inline {{
            display: inline-block; width: 16px; height: 16px;
            border: 2px solid #ddd; border-top-color: #007bff;
            border-radius: 50%; animation: spin 0.8s linear infinite;
            vertical-align: middle; margin-right: 6px;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

        /* Chat panel */
        .chat-panel {{
            border: 1px solid #e0e0e0; border-radius: 10px;
            background: #fff; overflow: hidden;
        }}
        .chat-context-config {{
            padding: 8px 16px; background: #fafafa; border-bottom: 1px solid #eee;
            font-size: 0.8em; color: #555;
        }}
        .chat-context-config label {{
            margin-right: 12px; cursor: pointer; white-space: nowrap;
        }}
        .chat-context-config input[type="checkbox"] {{ margin-right: 3px; }}
        .chat-messages {{
            height: 350px; overflow-y: auto; padding: 12px 16px;
            display: flex; flex-direction: column; gap: 10px;
        }}
        .chat-msg {{
            max-width: 85%; padding: 8px 12px; border-radius: 8px;
            font-size: 0.9em; line-height: 1.5; white-space: pre-wrap;
        }}
        .chat-msg.user {{
            align-self: flex-end; background: #e3f2fd; color: #1a237e;
        }}
        .chat-msg.assistant {{
            align-self: flex-start; background: #f5f5f5; color: #333;
        }}
        .chat-msg.error {{
            align-self: center; background: #ffebee; color: #c62828; font-size: 0.85em;
        }}
        .chat-input-row {{
            display: flex; gap: 8px; padding: 10px 16px;
            border-top: 1px solid #eee;
        }}
        .chat-input-row input {{
            flex: 1; padding: 8px 12px; font-size: 0.9em;
            border: 1px solid #ccc; border-radius: 6px;
        }}
        .chat-input-row button {{
            padding: 8px 16px; font-size: 0.9em; font-weight: 500;
            border: none; border-radius: 6px; background: #1976d2;
            color: #fff; cursor: pointer;
        }}
        .chat-input-row button:hover {{ background: #1565c0; }}
        .chat-input-row button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .chat-msg.assistant {{ white-space: normal; }}
        .chat-msg.assistant p {{ margin: 0 0 8px 0; }}
        .chat-msg.assistant p:last-child {{ margin-bottom: 0; }}
        .chat-msg.assistant ul, .chat-msg.assistant ol {{ margin: 4px 0 8px 0; padding-left: 20px; }}
        .chat-msg.assistant li {{ margin-bottom: 2px; }}
        .chat-msg.assistant strong {{ font-weight: 600; }}
        .chat-msg.assistant code {{ background: #e8e8e8; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }}
        .chat-msg.assistant h1, .chat-msg.assistant h2, .chat-msg.assistant h3 {{ font-size: 1em; font-weight: 600; margin: 8px 0 4px 0; }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</head>
<body>
    <h1>Pass 0b: Audio Player</h1>
    <div class="nav-links">
        <a href="/pass0b">&larr; Back to Pass 0b</a>
        {'<a href="/analyzer">Main Analyzer</a>' if not search_mode else ''}
    </div>

    <div class="controls">
        <div class="field">
            <label>Category</label>
            <select id="selected_category" onchange="onCategoryChange(this)">
                {category_select}
            </select>
        </div>
        <div class="field">
            <label>Lesson</label>
            <select id="selected_lesson" onchange="onLessonSelect(this)">
                <option value="">-- Select lesson --</option>
            </select>
        </div>
        <div class="field" style="flex: 0.5;">
            <label>Provider</label>
            <select id="provider">
                <option value="">Default (Gemini)</option>
                <option value="gemini">Gemini 3 Flash</option>
                <option value="openai">OpenAI (GPT-5)</option>
                <option value="anthropic">Sonnet 4.6</option>
                <option value="anthropic-sonnet-4.5">Sonnet 4.5</option>
                <option value="anthropic-haiku">Haiku 4.5</option>
            </select>
        </div>
        <div class="field" style="flex: 0.5;">
            <label>Condensed</label>
            <select id="condensed-provider">
                <option value="">Same</option>
                <option value="anthropic">Sonnet 4.6</option>
                <option value="anthropic-sonnet-4.5">Sonnet 4.5</option>
                <option value="anthropic-haiku">Haiku 4.5</option>
                <option value="gemini">Gemini 3 Flash</option>
                <option value="openai">OpenAI (GPT-5)</option>
            </select>
        </div>
        <button class="btn-run" id="run-btn" onclick="runAnalysis()" disabled>Run</button>
        <button class="btn-condensed" id="condensed-btn" onclick="runCondensed()" disabled>Condensed</button>
        <button class="btn-annotate" id="annotate-btn" onclick="runAnnotate()" disabled>Annotate</button>
        <label style="font-size:0.8em;color:#666;margin-left:8px;cursor:pointer;"><input type="checkbox" id="skip-cache-cb" style="margin-right:3px;">Skip cache</label>
    </div>
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
        <label style="font-size:0.85em;color:#555;white-space:nowrap;">Hint:</label>
        <input type="text" id="condensed-hint" placeholder="e.g. this lesson is really about eye-opposition as a structural mechanism"
               style="flex:1;padding:6px 10px;font-size:0.9em;border:1px solid #ccc;border-radius:4px;">
    </div>

    <div class="audio-container audio-hidden" id="audio-container">
        <audio id="audio-player" controls preload="auto"></audio>
        <div class="segment-box" id="segment-box">
            <div id="segment-text"></div>
            <div class="note-line" id="annotation-note"></div>
        </div>
    </div>

    <div id="status-area"></div>

    <div class="card" id="card">
        <div class="card-placeholder" id="card-placeholder">
            Select a lesson to begin. Audio will load automatically.<br>
            Click <strong>Run</strong> to add somatic &amp; language analysis.
        </div>
        <div id="card-content" style="display:none; flex: 1; flex-direction: column;">
            <div class="group-label" id="card-group"></div>
            <div class="step-header">
                <span id="card-step-label"></span>
                <span class="step-time" id="card-step-time"></span>
            </div>
            <div class="step-position" id="card-position" style="display:none;"></div>
            <div class="step-instruction" id="card-instruction"></div>

            <div id="analysis-somatic" class="analysis-section" style="display:none;">
                <h4>Somatic</h4>
                <ul id="somatic-bullets"></ul>
            </div>
            <div id="analysis-lang" class="analysis-section" style="display:none;">
                <h4>Language</h4>
                <ul id="lang-bullets"></ul>
            </div>

            <div class="card-nav">
                <button id="nav-prev" onclick="navigate(-1)">&larr; Previous</button>
                <span class="step-counter" id="step-counter"></span>
                <button id="nav-next" onclick="navigate(1)">Next &rarr;</button>
            </div>
        </div>
    </div>

    <div class="progress-bar" id="progress-bar"></div>

    <!-- Condensed cards view (hidden by default) -->
    <div id="condensed-view" style="display:none;">
        <div id="condensed-setting-card"></div>
        <div id="condensed-group-cards"></div>
    </div>

    <!-- Lesson Q&A panel -->
    <div class="chat-panel" id="chat-panel">
        <div class="chat-header" onclick="toggleChat()">
            <h3>Lesson Q&amp;A</h3>
            <span class="toggle" id="chat-toggle">&#9654; Open</span>
        </div>
        <div class="chat-body" id="chat-body">
            <div class="chat-context-config" id="chat-context-config" {'style="display:none;"' if search_mode else ''}>
                <strong>Context:</strong>
                <label><input type="checkbox" id="ctx-lesson-text" checked> Lesson text</label>
                <label><input type="checkbox" id="ctx-pass-a" checked> Pass A</label>
                <label><input type="checkbox" id="ctx-somatic" checked> Somatic</label>
                <label><input type="checkbox" id="ctx-language" checked> Language</label>
                <label><input type="checkbox" id="ctx-somatic-summary" checked> Somatic summary</label>
                <label><input type="checkbox" id="ctx-language-summary" checked> Language summary</label>
                <label><input type="checkbox" id="ctx-condensed" checked> Condensed cards</label>
            </div>
            <div class="chat-messages" id="chat-messages"></div>
            <div class="chat-input-row">
                <input type="text" id="chat-input" placeholder="Ask about this lesson..."
                       onkeydown="if(event.key==='Enter') sendChat()">
                <button id="chat-send-btn" onclick="sendChat()">Send</button>
            </div>
        </div>
    </div>

<script>
const lessonsData = {lessons_json};

// State
let whisperSegments = null;  // raw whisper segments
let whisperWords = null;     // word-level timestamps
let analysisData = null;     // structured analysis JSON
let wordAnnotations = null;  // word-level language annotations
let currentStepIdx = 0;
let audioReady = false;
let selectedDocId = null;
let selectedCategory = '';
let autoAdvance = true;

const audio = document.getElementById('audio-player');
const audioContainer = document.getElementById('audio-container');

// ── Category / lesson selection ──

function onCategoryChange(sel) {{
    const lessonSel = document.getElementById('selected_lesson');
    lessonSel.innerHTML = '<option value="">-- Select lesson --</option>';
    selectedCategory = sel.value;
    if (sel.value && lessonsData[sel.value]) {{
        lessonsData[sel.value].forEach(([id, display, sourceUrl]) => {{
            const opt = document.createElement('option');
            opt.value = id;
            opt.textContent = display;
            lessonSel.appendChild(opt);
        }});
    }}
    resetPlayer();
}}

async function onLessonSelect(sel) {{
    resetPlayer();
    selectedDocId = sel.value;
    if (!selectedDocId || !selectedCategory) return;

    showStatus('Loading audio and segments...');

    // Load audio URL, whisper segments, and word timestamps in parallel
    try {{
        const [audioRes, segRes, wordRes] = await Promise.all([
            fetch('/api/audio-url/' + encodeURIComponent(selectedDocId)),
            fetch('/api/whisper-segments/' + encodeURIComponent(selectedDocId)),
            fetch('/api/whisper-words/' + encodeURIComponent(selectedDocId)),
        ]);
        const audioData = await audioRes.json();
        const segData = await segRes.json();
        const wordData = await wordRes.json();

        if (audioData.audio_url) {{
            audio.src = audioData.audio_url;
            audioContainer.classList.remove('audio-hidden');
            audioReady = true;
        }} else {{
            showStatus('No audio available for this lesson.', true);
        }}

        if (segData.segments) {{
            whisperSegments = segData.segments;
        }}

        if (wordData.words) {{
            whisperWords = wordData.words;
        }}

        document.getElementById('run-btn').disabled = false;
        document.getElementById('condensed-btn').disabled = false;
        document.getElementById('annotate-btn').disabled = false;
        condensedLessonText = null;
        annotateLessonText = null;
        wordAnnotations = null;
        clearStatus();
    }} catch (e) {{
        showStatus('Error loading lesson data: ' + e.message, true);
    }}
}}

// ── Run analysis ──

async function runAnalysis() {{
    // Switch back to step view if condensed is showing
    document.getElementById('condensed-view').style.display = 'none';
    document.getElementById('card').style.display = '';

    const btn = document.getElementById('run-btn');
    btn.disabled = true;
    btn.textContent = 'Running...';
    showStatus('<span class="spinner-inline"></span> Running Pass A + somatic & language analysis...');

    // We need lesson text — fetch it
    try {{
        const textRes = await fetch('/api/lesson/' + encodeURIComponent(selectedCategory) + '/' + encodeURIComponent(selectedDocId));
        const textData = await textRes.json();
        if (!textData.text) {{
            showStatus('Could not load lesson text.', true);
            btn.disabled = false;
            btn.textContent = 'Run';
            return;
        }}

        const form = new FormData();
        form.append('lesson_text', textData.text);
        form.append('selected_category', selectedCategory);
        form.append('selected_lesson', selectedDocId);
        form.append('provider', document.getElementById('provider').value);

        const res = await fetch('/api/pass0b-combined-json', {{
            method: 'POST',
            body: form,
        }});
        const data = await res.json();

        if (data.error) {{
            showStatus('Error: ' + data.error, true);
            btn.disabled = false;
            btn.textContent = 'Run';
            return;
        }}

        analysisData = data;
        currentStepIdx = 0;
        renderCard();
        clearStatus();

        // Show timing info
        const somTime = data.cached_somatic ? 'cached' : (data.elapsed_somatic || '?') + 's';
        const langTime = data.cached_lang ? 'cached' : (data.elapsed_lang || '?') + 's';
        document.getElementById('progress-bar').textContent =
            data.steps.length + ' steps | Somatic: ' + somTime + ', Language: ' + langTime;

        btn.textContent = 'Run';
        btn.disabled = false;
    }} catch (e) {{
        showStatus('Error: ' + e.message, true);
        btn.textContent = 'Run';
        btn.disabled = false;
    }}
}}

// ── Card rendering ──

function renderCard() {{
    if (!analysisData || !analysisData.steps.length) return;

    document.getElementById('card-placeholder').style.display = 'none';
    const content = document.getElementById('card-content');
    content.style.display = 'flex';

    const step = analysisData.steps[currentStepIdx];
    const group = analysisData.groups.find(g => g.group_number === step.group_number);

    // Group label
    const groupPos = group && group.starting_position ? ' (' + group.starting_position + ')' : '';
    document.getElementById('card-group').textContent =
        group ? 'Group ' + group.group_number + ': ' + group.label + groupPos : '';

    // Step header
    document.getElementById('card-step-label').textContent = 'Step ' + step.step_number;
    if (step.time_start != null && step.time_end != null) {{
        document.getElementById('card-step-time').textContent =
            '(' + fmtTime(step.time_start) + ' \u2013 ' + fmtTime(step.time_end) + ')';
    }} else {{
        document.getElementById('card-step-time').textContent = '';
    }}

    // Instruction
    document.getElementById('card-instruction').textContent = step.instruction || '';

    // Segment text — show first segment initially
    updateSegmentForTime(audio.currentTime);

    // Somatic bullets
    const somDiv = document.getElementById('analysis-somatic');
    const somUl = document.getElementById('somatic-bullets');
    somUl.innerHTML = '';
    if (step.somatic || step.starting_position) {{
        let hasBullets = false;
        // Starting position as first bullet
        if (step.starting_position) {{
            const li = document.createElement('li');
            li.innerHTML = '<strong>Position:</strong> ' + escHtml(step.starting_position);
            somUl.appendChild(li);
            hasBullets = true;
        }}
        if (step.somatic) {{
            const fields = [
                ['Intent', 'intent'], ['Differentiation', 'differentiation'],
                ['Activation', 'activation'], ['Inhibition', 'inhibition'],
                ['Transmission Route', 'transmission_route'], ['Ground Push', 'ground_push'],
                ['Mechanical Directive', 'mechanical_directive'],
                ['Attentional Cue', 'attentional_cue'],
            ];
            fields.forEach(([label, key]) => {{
                const val = step.somatic[key];
                if (val && val.toLowerCase() !== 'none') {{
                    const li = document.createElement('li');
                    li.innerHTML = '<strong>' + label + ':</strong> ' + escHtml(val);
                    somUl.appendChild(li);
                    hasBullets = true;
                }}
            }});
            // Show clarified text
            if (step.somatic.clarified) {{
                const li = document.createElement('li');
                li.innerHTML = '<strong>Clarified:</strong> ' + escHtml(step.somatic.clarified);
                li.style.fontStyle = 'italic';
                somUl.prepend(li);
                hasBullets = true;
            }}
        }}
        somDiv.style.display = hasBullets ? '' : 'none';
    }} else {{
        somDiv.style.display = 'none';
    }}

    // Language bullets
    const langDiv = document.getElementById('analysis-lang');
    const langUl = document.getElementById('lang-bullets');
    langUl.innerHTML = '';
    if (step.language) {{
        const fields = [
            ['Explanatory', 'explanatory_language'], ['Attention Cues', 'attention_cues'],
            ['Effort', 'effort_language'], ['Imagery', 'image_language'],
            ['Distinctive', 'distinctive_phrasing'], ['Sensory Inquiry', 'sensory_inquiry'],
        ];
        let hasBullets = false;
        fields.forEach(([label, key]) => {{
            const val = step.language[key];
            if (val && val.toLowerCase() !== 'none') {{
                const li = document.createElement('li');
                li.innerHTML = '<strong>' + label + ':</strong> ' + escHtml(val);
                langUl.appendChild(li);
                hasBullets = true;
            }}
        }});
        langDiv.style.display = hasBullets ? '' : 'none';
    }} else {{
        langDiv.style.display = 'none';
    }}

    // Navigation
    document.getElementById('nav-prev').disabled = (currentStepIdx === 0);
    document.getElementById('nav-next').disabled = (currentStepIdx === analysisData.steps.length - 1);
    document.getElementById('step-counter').textContent =
        'Step ' + (currentStepIdx + 1) + ' of ' + analysisData.steps.length;
}}

function navigate(delta) {{
    if (!analysisData) return;
    const newIdx = currentStepIdx + delta;
    if (newIdx < 0 || newIdx >= analysisData.steps.length) return;
    autoAdvance = false; // manual nav disables auto-advance briefly
    currentStepIdx = newIdx;
    renderCard();

    // Seek audio to step start
    const step = analysisData.steps[currentStepIdx];
    if (step.time_start != null && audioReady) {{
        audio.currentTime = step.time_start;
    }}
    // Re-enable auto-advance after a short delay
    setTimeout(() => {{ autoAdvance = true; }}, 500);
}}

// ── Audio sync ──

audio.addEventListener('timeupdate', function() {{
    const t = audio.currentTime;

    // Update segment highlight
    updateSegmentForTime(t);

    // Auto-advance to correct step
    if (analysisData && autoAdvance) {{
        const stepIdx = findStepForTime(t);
        if (stepIdx !== -1 && stepIdx !== currentStepIdx) {{
            currentStepIdx = stepIdx;
            renderCard();
        }}
    }}
}});

function getAnnotationsForStep(stepNumber) {{
    if (!wordAnnotations) return [];
    const step = wordAnnotations.find(s => s.step_number === stepNumber);
    return step ? (step.phrases || []) : [];
}}

// Cache: step_number -> Map(wordStartTime -> annotation)
let _stepAnnotationCache = {{}};

function buildStepAnnotationMap(stepNumber) {{
    // Build annotation map for an entire step's whisper words (keyed by word start time)
    if (_stepAnnotationCache[stepNumber]) return _stepAnnotationCache[stepNumber];

    const phrases = getAnnotationsForStep(stepNumber);
    const annMap = {{}};
    if (!phrases.length || !whisperWords || !analysisData) {{
        _stepAnnotationCache[stepNumber] = annMap;
        return annMap;
    }}

    // Get all whisper words for this step's full time range
    const step = analysisData.steps.find(s => s.step_number === stepNumber);
    if (!step || !step.segments || !step.segments.length) {{
        _stepAnnotationCache[stepNumber] = annMap;
        return annMap;
    }}

    let stepStart = Infinity, stepEnd = -Infinity;
    for (const seg of step.segments) {{
        if (seg.time_start != null && seg.time_start < stepStart) stepStart = seg.time_start;
        if (seg.time_end != null && seg.time_end > stepEnd) stepEnd = seg.time_end;
    }}

    const stepWords = [];
    for (const w of whisperWords) {{
        if (w.start >= (stepStart - 0.5) && w.end <= (stepEnd + 0.5)) {{
            stepWords.push(w);
        }}
        if (w.start > stepEnd + 1) break;
    }}

    if (!stepWords.length) {{
        _stepAnnotationCache[stepNumber] = annMap;
        return annMap;
    }}

    // Build lowercase word array for matching
    const lcWords = stepWords.map(w => w.word.toLowerCase().replace(/[.,!?;:'"]/g, ''));

    for (const phrase of phrases) {{
        const phraseTokens = phrase.text.toLowerCase().replace(/[.,!?;:'"]/g, '').split(/\s+/).filter(t => t);
        if (!phraseTokens.length) continue;

        // Sliding window search across all step words
        for (let i = 0; i <= lcWords.length - phraseTokens.length; i++) {{
            let match = true;
            for (let j = 0; j < phraseTokens.length; j++) {{
                if (lcWords[i + j] !== phraseTokens[j]) {{
                    match = false;
                    break;
                }}
            }}
            if (match) {{
                for (let j = 0; j < phraseTokens.length; j++) {{
                    // Key by word start time (unique per word)
                    annMap[stepWords[i + j].start] = {{ category: phrase.category, note: phrase.note, text: phrase.text }};
                }}
                break;  // first match wins
            }}
        }}
    }}

    _stepAnnotationCache[stepNumber] = annMap;
    return annMap;
}}

function renderTextWithWordHighlight(text, t, timeStart, timeEnd, stepNumber) {{
    // If no word timestamps, return plain text
    if (!whisperWords || !whisperWords.length) return escHtml(text);

    // Find words in this time range
    const rangeWords = [];
    for (const w of whisperWords) {{
        if (w.start >= (timeStart - 0.5) && w.end <= (timeEnd + 0.5)) {{
            rangeWords.push(w);
        }}
        if (w.start > timeEnd + 1) break;  // past the range, stop searching
    }}

    if (!rangeWords.length) return escHtml(text);

    // Get step-level annotation map (keyed by word start time)
    const annMap = stepNumber != null ? buildStepAnnotationMap(stepNumber) : {{}};

    // Find which annotation phrase (if any) contains the current spoken word
    let activePhrase = null;
    for (let i = 0; i < rangeWords.length; i++) {{
        const w = rangeWords[i];
        if (t >= w.start - 0.05 && t <= w.end + 0.05) {{
            const ann = annMap[w.start];
            if (ann) activePhrase = ann.text;
            break;
        }}
    }}

    // Build HTML with bold on current word + colored annotation spans
    let html = '';
    let currentNote = null;
    for (let i = 0; i < rangeWords.length; i++) {{
        const w = rangeWords[i];
        const isCurrent = (t >= w.start - 0.05 && t <= w.end + 0.05);
        const ann = annMap[w.start];
        if (i > 0) html += ' ';

        let wordHtml = escHtml(w.word);
        if (isCurrent) {{
            wordHtml = '<strong style="color:#1a237e;font-size:1.05em;">' + wordHtml + '</strong>';
            if (ann) currentNote = ann;
        }}
        if (ann) {{
            // Light up the whole phrase when the speaker is inside it
            const isActive = activePhrase && ann.text === activePhrase;
            const cls = 'word-' + ann.category + (isActive ? ' word-active' : '');
            wordHtml = '<span class="' + cls + '">' + wordHtml + '</span>';
        }}
        html += wordHtml;
    }}

    // Update note line
    const noteLine = document.getElementById('annotation-note');
    if (noteLine) {{
        if (currentNote) {{
            noteLine.innerHTML = '<span class="note-category note-cat-' + currentNote.category + '">'
                + currentNote.category + '</span> ' + escHtml(currentNote.note);
        }} else {{
            noteLine.innerHTML = '';
        }}
    }}

    return html;
}}

function updateSegmentForTime(t) {{
    const segBox = document.getElementById('segment-text');
    if (!segBox) return;

    // If analysis data exists, use step-level segments
    if (analysisData) {{
        const step = analysisData.steps[currentStepIdx];
        const stepNum = step ? step.step_number : null;
        if (step && step.segments) {{
            for (const seg of step.segments) {{
                if (seg.time_start != null && t >= seg.time_start && t <= seg.time_end) {{
                    segBox.innerHTML = renderTextWithWordHighlight(
                        seg.text, t, seg.time_start, seg.time_end, stepNum);
                    return;
                }}
            }}
            // If no segment matches, show closest segment
            if (step.segments.length > 0) {{
                let closest = step.segments[0];
                let minDist = Infinity;
                for (const seg of step.segments) {{
                    if (seg.time_start != null) {{
                        const dist = Math.abs(seg.time_start - t);
                        if (dist < minDist) {{ minDist = dist; closest = seg; }}
                    }}
                }}
                segBox.innerHTML = renderTextWithWordHighlight(
                    closest.text || '', t,
                    closest.time_start || 0, closest.time_end || 0, stepNum);
            }}
            return;
        }}
    }}

    // Fallback: use raw whisper segments
    if (whisperSegments) {{
        for (const ws of whisperSegments) {{
            if (t >= ws.start && t <= ws.end) {{
                segBox.innerHTML = renderTextWithWordHighlight(
                    ws.text, t, ws.start, ws.end, null);
                return;
            }}
        }}
    }}
}}

function findStepForTime(t) {{
    if (!analysisData) return -1;
    for (let i = 0; i < analysisData.steps.length; i++) {{
        const s = analysisData.steps[i];
        if (s.time_start != null && s.time_end != null) {{
            if (t >= s.time_start && t <= s.time_end) return i;
        }}
    }}
    return -1;
}}

// ── Keyboard nav ──

document.addEventListener('keydown', function(e) {{
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (e.key === 'ArrowLeft') {{ navigate(-1); e.preventDefault(); }}
    if (e.key === 'ArrowRight') {{ navigate(1); e.preventDefault(); }}
    if (e.key === ' ') {{
        if (audioReady) {{
            if (audio.paused) audio.play(); else audio.pause();
            e.preventDefault();
        }}
    }}
}});

// ── Helpers ──

function fmtTime(seconds) {{
    if (seconds == null) return '';
    const s = Math.floor(seconds);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
    return m + ':' + String(sec).padStart(2, '0');
}}

function escHtml(str) {{
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}}

function showStatus(msg, isError) {{
    const el = document.getElementById('status-area');
    el.innerHTML = '<div class="status-msg' + (isError ? ' error' : '') + '">' + msg + '</div>';
}}

function clearStatus() {{
    document.getElementById('status-area').innerHTML = '';
}}

function resetPlayer() {{
    whisperSegments = null;
    whisperWords = null;
    analysisData = null;
    wordAnnotations = null;
    _stepAnnotationCache = {{}};
    currentStepIdx = 0;
    audioReady = false;
    audio.src = '';
    audioContainer.classList.add('audio-hidden');
    document.getElementById('run-btn').disabled = true;
    document.getElementById('condensed-btn').disabled = true;
    document.getElementById('annotate-btn').disabled = true;
    document.getElementById('annotation-note').innerHTML = '';
    document.getElementById('card-placeholder').style.display = '';
    document.getElementById('card-content').style.display = 'none';
    document.getElementById('progress-bar').textContent = '';
    document.getElementById('condensed-view').style.display = 'none';
    document.getElementById('condensed-setting-card').innerHTML = '';
    document.getElementById('condensed-group-cards').innerHTML = '';
    chatMessages = [];
    document.getElementById('chat-messages').innerHTML = '';
    clearStatus();
}}

// ── Condensed view ──

let condensedLessonText = null;

async function runCondensed() {{
    const btn = document.getElementById('condensed-btn');
    btn.disabled = true;
    btn.textContent = 'Loading...';

    // Hide step view, show condensed
    document.getElementById('card').style.display = 'none';
    document.getElementById('progress-bar').textContent = '';
    document.getElementById('condensed-view').style.display = 'block';

    showStatus('<span class="spinner-inline"></span> Generating condensed lesson cards...');

    try {{
        // Fetch lesson text if not already loaded
        if (!condensedLessonText) {{
            const textRes = await fetch('/api/lesson/' + encodeURIComponent(selectedCategory) + '/' + encodeURIComponent(selectedDocId));
            const textData = await textRes.json();
            if (!textData.text) {{
                showStatus('Could not load lesson text.', true);
                btn.disabled = false;
                btn.textContent = 'Condensed';
                return;
            }}
            condensedLessonText = textData.text;
        }}

        const form = new FormData();
        form.append('lesson_text', condensedLessonText);
        form.append('provider', document.getElementById('provider').value);
        const condensedProv = document.getElementById('condensed-provider').value;
        if (condensedProv) form.append('condensed_provider', condensedProv);
        const hintVal = document.getElementById('condensed-hint').value.trim();
        if (hintVal) form.append('hint', hintVal);
        if (document.getElementById('skip-cache-cb').checked) {{
            form.append('skip_cache', '1');
        }}

        const res = await fetch('/api/pass0b-condensed', {{
            method: 'POST',
            body: form,
        }});
        const data = await res.json();

        if (data.error) {{
            showStatus('Error: ' + data.error, true);
            btn.disabled = false;
            btn.textContent = 'Condensed';
            return;
        }}

        renderCondensedCards(data);
        clearStatus();

        const timing = data.cached ? 'cached' : (data.elapsed || '?') + 's';
        const segs = data.segments || [];
        const totalCards = segs.reduce((n, seg) => n + (seg.group_cards ? seg.group_cards.length : 0), 0);
        const segLabel = segs.length > 1 ? ' | ' + segs.length + ' phases' : '';
        document.getElementById('progress-bar').textContent =
            totalCards + ' movement sequences' + segLabel + ' | ' + timing;

        btn.textContent = 'Condensed';
        btn.disabled = false;
    }} catch (e) {{
        showStatus('Error: ' + e.message, true);
        btn.textContent = 'Condensed';
        btn.disabled = false;
    }}
}}

// ── Word-level annotation ──

let annotateLessonText = null;

async function runAnnotate() {{
    const btn = document.getElementById('annotate-btn');
    btn.disabled = true;
    btn.textContent = 'Loading...';

    showStatus('<span class="spinner-inline"></span> Generating word-level language annotations...');

    try {{
        // Fetch lesson text if not already loaded
        if (!annotateLessonText) {{
            const textRes = await fetch('/api/lesson/' + encodeURIComponent(selectedCategory) + '/' + encodeURIComponent(selectedDocId));
            const textData = await textRes.json();
            if (!textData.text) {{
                showStatus('Could not load lesson text.', true);
                btn.disabled = false;
                btn.textContent = 'Annotate';
                return;
            }}
            annotateLessonText = textData.text;
        }}

        const form = new FormData();
        form.append('lesson_text', annotateLessonText);
        form.append('selected_category', selectedCategory);
        form.append('selected_lesson', selectedDocId);
        form.append('provider', document.getElementById('provider').value);

        const res = await fetch('/api/pass0b-word-lang-json', {{
            method: 'POST',
            body: form,
        }});
        const data = await res.json();

        if (data.error) {{
            showStatus('Error: ' + data.error, true);
            btn.disabled = false;
            btn.textContent = 'Annotate';
            return;
        }}

        wordAnnotations = data.word_annotations || [];
        _stepAnnotationCache = {{}};  // clear match cache
        clearStatus();

        const timing = data.cached ? 'cached' : (data.elapsed || '?') + 's';
        const totalPhrases = wordAnnotations.reduce((n, s) => n + (s.phrases ? s.phrases.length : 0), 0);
        document.getElementById('progress-bar').textContent =
            totalPhrases + ' annotations across ' + wordAnnotations.length + ' steps | ' + timing;

        btn.textContent = 'Annotate';
        btn.disabled = false;
    }} catch (e) {{
        showStatus('Error: ' + e.message, true);
        btn.textContent = 'Annotate';
        btn.disabled = false;
    }}
}}

function renderSettingCard(s, label) {{
    let html = '<div class="condensed-card">';
    html += '<div class="setting-tag">' + escHtml(label) + '</div>';
    html += '<h3>' + escHtml(s.title || '') + '</h3>';
    if (s.somatic_intent) {{
        html += '<p style="font-style:italic;margin:8px 0 12px 0;color:#444;">' + escHtml(s.somatic_intent) + '</p>';
    }}
    const settingAttrs = [];
    if (s.space) settingAttrs.push('<li><strong>Space:</strong> ' + escHtml(s.space) + '</li>');
    if (s.teacher_presence) settingAttrs.push('<li><strong>Teacher Presence:</strong> ' + escHtml(s.teacher_presence) + '</li>');
    if (s.arc) settingAttrs.push('<li><strong>Arc:</strong> ' + escHtml(s.arc) + '</li>');
    if (settingAttrs.length) {{
        html += '<ul style="margin:0;padding-left:20px;color:#444;font-size:0.95em;">' + settingAttrs.join('') + '</ul>';
    }}
    html += '</div>';
    return html;
}}

function getGroupTimeRange(groupNumber) {{
    // Compute time range for a group from analysisData steps
    if (!analysisData || !analysisData.steps) return null;
    let minStart = Infinity, maxEnd = -Infinity;
    for (const s of analysisData.steps) {{
        if (s.group_number === groupNumber && s.time_start != null && s.time_end != null) {{
            if (s.time_start < minStart) minStart = s.time_start;
            if (s.time_end > maxEnd) maxEnd = s.time_end;
        }}
    }}
    if (minStart === Infinity) return null;
    return {{ start: minStart, end: maxEnd }};
}}

function renderGroupCards(cards) {{
    let html = '';
    cards.forEach(card => {{
        html += '<div class="condensed-card group-card">';
        const tr = getGroupTimeRange(card.group_number);
        const timeStr = tr ? ' <span style="font-weight:normal;color:#888;">(' + fmtTime(tr.start) + '\u2013' + fmtTime(tr.end) + ')</span>' : '';
        html += '<div class="group-tag">Group ' + card.group_number + ': ' + escHtml(card.group_label || '') + timeStr + '</div>';
        html += '<h3>' + escHtml(card.title || '') + '</h3>';

        if (card.the_action) {{
            html += '<p style="margin:8px 0 12px 0;color:#444;">' + escHtml(card.the_action) + '</p>';
        }}
        const groupAttrs = [];
        // Look up starting_position from analysisData groups
        const adGroup = analysisData && analysisData.groups ? analysisData.groups.find(g => g.group_number === card.group_number) : null;
        if (adGroup && adGroup.starting_position) groupAttrs.push('<li><strong>Position:</strong> ' + escHtml(adGroup.starting_position) + '</li>');
        if (card.the_body) groupAttrs.push('<li><strong>Body:</strong> ' + escHtml(card.the_body) + '</li>');
        if (card.the_attention) groupAttrs.push('<li><strong>Attention:</strong> ' + escHtml(card.the_attention) + '</li>');
        if (card.the_intention) groupAttrs.push('<li><strong>Intention:</strong> ' + escHtml(card.the_intention) + '</li>');
        if (card.the_voice && card.the_voice.toLowerCase() !== 'none') groupAttrs.push('<li><strong>Voice:</strong> ' + escHtml(card.the_voice) + '</li>');
        if (groupAttrs.length) {{
            html += '<ul style="margin:0;padding-left:20px;color:#444;font-size:0.95em;">' + groupAttrs.join('') + '</ul>';
        }}

        html += '</div>';
    }});
    return html;
}}

function renderCondensedCards(data) {{
    const settingDiv = document.getElementById('condensed-setting-card');
    const groupsDiv = document.getElementById('condensed-group-cards');
    settingDiv.innerHTML = '';
    groupsDiv.innerHTML = '';

    const segments = data.segments || [];

    if (segments.length <= 1) {{
        // Single segment — render exactly as before
        const seg = segments[0] || {{}};
        if (seg.setting) {{
            settingDiv.innerHTML = renderSettingCard(seg.setting, 'Lesson Setting');
        }}
        if (seg.group_cards) {{
            groupsDiv.innerHTML = renderGroupCards(seg.group_cards);
        }}
    }} else {{
        // Depth-first: each phase's setting + groups together
        let allHtml = '';
        segments.forEach(seg => {{
            const segLabel = 'Phase ' + seg.segment_number + ': ' + (seg.theme || '');
            // Phase header with time range
            let segTimeStr = '';
            if (seg.group_cards && seg.group_cards.length && analysisData) {{
                let sMin = Infinity, sMax = -Infinity;
                seg.group_cards.forEach(c => {{
                    const tr = getGroupTimeRange(c.group_number);
                    if (tr) {{ if (tr.start < sMin) sMin = tr.start; if (tr.end > sMax) sMax = tr.end; }}
                }});
                if (sMin !== Infinity) segTimeStr = ' (' + fmtTime(sMin) + '\u2013' + fmtTime(sMax) + ')';
            }}
            allHtml += '<div class="phase-header">'
                + escHtml(segLabel) + '<span class="phase-time">' + segTimeStr + '</span>'
                + (seg.transition_note ? '<span class="phase-note">' + escHtml(seg.transition_note) + '</span>' : '')
                + '</div>';
            // Setting card for this phase
            if (seg.setting) allHtml += renderSettingCard(seg.setting, segLabel + ' Setting');
            // Group cards for this phase
            if (seg.group_cards) allHtml += renderGroupCards(seg.group_cards);
        }});
        groupsDiv.innerHTML = allHtml;
    }}
}}

// Switch back to step view
function showStepView() {{
    document.getElementById('condensed-view').style.display = 'none';
    document.getElementById('card').style.display = '';
    if (analysisData) renderCard();
}}

// ── Lesson Chat ──

let chatMessages = [];

function toggleChat() {{
    const body = document.getElementById('chat-body');
    const toggle = document.getElementById('chat-toggle');
    if (body.classList.contains('open')) {{
        body.classList.remove('open');
        toggle.innerHTML = '&#9654; Open';
    }} else {{
        body.classList.add('open');
        toggle.innerHTML = '&#9660; Close';
        document.getElementById('chat-input').focus();
    }}
}}

function getChatContextConfig() {{
    return {{
        lesson_text: document.getElementById('ctx-lesson-text').checked,
        pass_a: document.getElementById('ctx-pass-a').checked,
        somatic: document.getElementById('ctx-somatic').checked,
        language: document.getElementById('ctx-language').checked,
        somatic_summary: document.getElementById('ctx-somatic-summary').checked,
        language_summary: document.getElementById('ctx-language-summary').checked,
        condensed: document.getElementById('ctx-condensed').checked,
    }};
}}

function renderChatMessages() {{
    const container = document.getElementById('chat-messages');
    container.innerHTML = chatMessages.map(m => {{
        const cls = m.role === 'user' ? 'user' : (m.role === 'error' ? 'error' : 'assistant');
        const content = m.role === 'assistant' ? marked.parse(m.content) : escHtml(m.content);
        return '<div class="chat-msg ' + cls + '">' + content + '</div>';
    }}).join('');
    container.scrollTop = container.scrollHeight;
}}

function askSuggestion(btn) {{
    document.getElementById('chat-input').value = btn.textContent;
    sendChat();
}}

async function sendChat() {{
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;

    // Need lesson text for context
    if (!condensedLessonText) {{
        try {{
            const textRes = await fetch('/api/lesson/' + encodeURIComponent(selectedCategory) + '/' + encodeURIComponent(selectedDocId));
            const textData = await textRes.json();
            if (textData.text) condensedLessonText = textData.text;
        }} catch(e) {{}}
    }}
    if (!condensedLessonText) {{
        chatMessages.push({{role: 'error', content: 'No lesson text available. Select a lesson first.'}});
        renderChatMessages();
        return;
    }}

    chatMessages.push({{role: 'user', content: text}});
    input.value = '';
    renderChatMessages();

    const btn = document.getElementById('chat-send-btn');
    btn.disabled = true;
    btn.textContent = '...';

    try {{
        const apiMessages = chatMessages.filter(m => m.role === 'user' || m.role === 'assistant');
        const res = await fetch('/api/lesson-chat', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
                messages: apiMessages,
                provider: document.getElementById('provider').value || '',
                context_config: getChatContextConfig(),
                lesson_text: condensedLessonText,
            }}),
        }});
        const data = await res.json();
        if (data.error) {{
            chatMessages.push({{role: 'error', content: data.error}});
        }} else {{
            chatMessages.push({{role: 'assistant', content: data.response}});
        }}
    }} catch (e) {{
        chatMessages.push({{role: 'error', content: 'Request failed: ' + e.message}});
    }}

    btn.disabled = false;
    btn.textContent = 'Send';
    renderChatMessages();
}}

// Auto-select lesson from query params (e.g. linked from pass0b analyzer)
(function() {{
    const params = new URLSearchParams(window.location.search);
    const cat = params.get('category');
    const lesson = params.get('lesson');
    if (cat && lesson) {{
        const catSel = document.getElementById('selected_category');
        catSel.value = cat;
        onCategoryChange(catSel);
        const lessonSel = document.getElementById('selected_lesson');
        lessonSel.value = lesson;
        onLessonSelect(lessonSel);
    }}
}})();

</script>
</body>
</html>
"""


def build_pass0b_lang_html(search_mode: bool = False) -> str:
    """Build the Pass 0b-lang language analysis page HTML."""
    categories, display_names = get_available_lessons(public_only=search_mode)

    category_options = ['<option value="">-- Select category --</option>']
    for cat_name in categories.keys():
        cat_display = display_names.get(cat_name, cat_name.replace("_", " ").title())
        category_options.append(f'<option value="{cat_name}">{cat_display}</option>')
    category_select = "\n".join(category_options)

    lessons_json = json.dumps({
        cat: [(t[0], t[1]) for t in lessons]
        for cat, lessons in categories.items()
    })

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Pass 0b-lang: Language Analysis</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; }}
        select {{
            width: 100%;
            padding: 10px;
            font-size: 14px;
            border: 1px solid #ccc;
            border-radius: 4px;
            margin-bottom: 10px;
        }}
        textarea {{
            width: 100%;
            height: 200px;
            font-family: monospace;
            font-size: 14px;
            padding: 10px;
            border: 1px solid #ccc;
            border-radius: 4px;
        }}
        button {{
            background-color: #007bff;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            margin-top: 10px;
        }}
        button:hover {{ background-color: #0056b3; }}
        button:disabled {{ background-color: #ccc; cursor: not-allowed; }}
        .error {{ color: red; padding: 10px; background: #fee; border-radius: 4px; }}
        label {{ font-weight: bold; display: block; margin-bottom: 5px; }}
        .form-group {{ margin-bottom: 15px; }}
        .or-divider {{ text-align: center; color: #666; margin: 10px 0; }}
        .loading-spinner {{
            display: none;
            margin-top: 20px;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 8px;
            text-align: center;
        }}
        .loading-spinner .spinner {{
            display: inline-block;
            width: 24px;
            height: 24px;
            border: 3px solid #ddd;
            border-top-color: #007bff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            vertical-align: middle;
            margin-right: 10px;
        }}
        @keyframes spin {{
            to {{ transform: rotate(360deg); }}
        }}
    </style>
    <script>
        const lessonsData = {lessons_json};

        function onCategoryChange(select) {{
            const category = select.value;
            const lessonSelect = document.getElementById('selected_lesson');
            lessonSelect.innerHTML = '<option value="">-- Select lesson --</option>';
            if (category && lessonsData[category]) {{
                lessonsData[category].forEach(([filename, display]) => {{
                    const option = document.createElement('option');
                    option.value = filename;
                    option.textContent = display;
                    lessonSelect.appendChild(option);
                }});
            }}
            document.getElementById('lesson_text').value = '';
        }}

        async function onLessonSelect(select) {{
            const textarea = document.getElementById('lesson_text');
            const category = document.getElementById('selected_category').value;
            if (select.value && category) {{
                textarea.value = '';
                textarea.disabled = true;
                textarea.placeholder = 'Loading lesson...';
                textarea.style.opacity = '0.5';
                try {{
                    const response = await fetch('/api/lesson/' + encodeURIComponent(category) + '/' + encodeURIComponent(select.value));
                    const data = await response.json();
                    if (data.text) textarea.value = data.text;
                    else textarea.placeholder = 'Error loading lesson';
                }} catch (error) {{
                    textarea.placeholder = 'Error loading lesson';
                }}
                textarea.disabled = false;
                textarea.style.opacity = '1';
            }} else {{
                textarea.value = '';
                textarea.disabled = false;
                textarea.style.opacity = '1';
                textarea.placeholder = 'Paste lesson text here...';
            }}
        }}

        async function submitForm(event) {{
            event.preventDefault();
            const btn = document.getElementById('submit-btn');
            const spinner = document.getElementById('loading-spinner');
            const resultDiv = document.getElementById('result');
            const debugDiv = document.getElementById('debug');
            const form = document.getElementById('pass0b-lang-form');

            btn.disabled = true;
            btn.textContent = 'Running Pass A + Language Analysis...';
            spinner.style.display = 'block';
            resultDiv.innerHTML = '';
            debugDiv.innerHTML = '';
            var startTime = Date.now();
            var timer = setInterval(function() {{
                var elapsed = Math.floor((Date.now() - startTime) / 1000);
                document.getElementById('loading-elapsed').textContent = elapsed + 's';
            }}, 1000);

            try {{
                const formData = new FormData(form);
                const response = await fetch('/api/pass0b-lang', {{
                    method: 'POST',
                    body: formData
                }});
                const data = await response.json();
                clearInterval(timer);

                if (data.error) {{
                    var msg = data.error;
                    if (msg.includes('\\n')) {{
                        resultDiv.innerHTML = '<div class="error"><strong>Error:</strong><pre style="white-space: pre-wrap; word-wrap: break-word; margin-top: 8px;">' + msg.replace(/</g, '&lt;') + '</pre></div>';
                    }} else {{
                        resultDiv.innerHTML = '<div class="error">Error: ' + msg + '</div>';
                    }}
                    if (data.debug_html) debugDiv.innerHTML = data.debug_html;
                    resetForm();
                    return;
                }}

                if (data.result_html) {{
                    var stats = '';
                    if (data.cached) {{
                        stats = 'Loaded from cache';
                    }} else {{
                        stats = 'Completed in ' + (data.elapsed || '?') + 's';
                    }}
                    stats += ' | ' + (data.step_count || '?') + ' steps in ' + (data.group_count || '?') + ' groups, ' + (data.enriched_count || '?') + ' analyzed';
                    resultDiv.innerHTML = '<p style="color: #28a745; font-weight: bold;">' + stats + '</p>' + data.result_html;
                }}
                if (data.debug_html) debugDiv.innerHTML = data.debug_html;
                resetForm();
            }} catch (error) {{
                clearInterval(timer);
                resultDiv.innerHTML = '<div class="error">Error: ' + error.message + '</div>';
                resetForm();
            }}
        }}

        function resetForm() {{
            document.getElementById('submit-btn').disabled = false;
            document.getElementById('submit-btn').textContent = 'Run Language Analysis';
            document.getElementById('loading-spinner').style.display = 'none';
        }}

        async function clearCache() {{
            const btn = document.getElementById('clear-cache-btn');
            btn.disabled = true;
            btn.textContent = 'Clearing...';
            try {{
                const response = await fetch('/api/pass0b-lang/clear-cache', {{ method: 'POST' }});
                const data = await response.json();
                btn.textContent = 'Cleared ' + data.deleted + ' entries';
                setTimeout(() => {{ btn.textContent = 'Clear Cache'; btn.disabled = false; }}, 2000);
            }} catch (error) {{
                btn.textContent = 'Error';
                setTimeout(() => {{ btn.textContent = 'Clear Cache'; btn.disabled = false; }}, 2000);
            }}
        }}
    </script>
</head>
<body>
    <h1>Pass 0b: Language Analysis</h1>
    <p>Analyzes the teacher's word choice, sensory framing, effort language, and pedagogical stance at each step.
       <a href="/pass0b" style="float: right; color: #007bff; text-decoration: none; font-size: 0.9em; margin-left: 15px;">Somatic Enrichment &rarr;</a>
       {'<a href="/analyzer" style="float: right; color: #007bff; text-decoration: none; font-size: 0.9em;">&larr; Main Analyzer</a>' if not search_mode else ''}
    </p>

    <form id="pass0b-lang-form" onsubmit="submitForm(event)">
        <div class="form-group">
            <label for="selected_category">Category:</label>
            <select name="selected_category" id="selected_category" onchange="onCategoryChange(this)">
                {category_select}
            </select>
        </div>

        <div class="form-group">
            <label for="selected_lesson">Lesson:</label>
            <select name="selected_lesson" id="selected_lesson" onchange="onLessonSelect(this)">
                <option value="">-- Select lesson --</option>
            </select>
        </div>

        <div class="or-divider">&mdash; OR &mdash;</div>

        <div class="form-group">
            <label for="lesson_text">Paste Lesson Text:</label>
            <textarea name="lesson_text" id="lesson_text" placeholder="Paste lesson text here..."></textarea>
        </div>

        <div style="margin: 10px 0;">
            <label style="font-size: 0.9em;">
                Provider:
                <select name="provider" style="margin-left: 4px; padding: 2px 6px; width: auto;">
                    <option value="">Default (Gemini)</option>
                    <option value="gemini">Gemini 3 Flash</option>
                    <option value="openai">OpenAI (GPT-5)</option>
                    <option value="anthropic">Sonnet 4.6</option>
                    <option value="anthropic-sonnet-4.5">Sonnet 4.5</option>
                    <option value="anthropic-haiku">Haiku 4.5</option>
                </select>
            </label>
        </div>

        <button type="submit" id="submit-btn">Run Language Analysis</button>
        <button type="button" id="clear-cache-btn" onclick="clearCache()" style="background-color: #6c757d; margin-left: 8px;">Clear Cache</button>
    </form>

    <div id="loading-spinner" class="loading-spinner">
        <span class="spinner"></span>
        <span>Running Pass A + language analysis...</span>
        <div id="loading-elapsed" style="font-size: 0.85em; color: #999; margin-top: 8px;"></div>
    </div>

    <div id="result" style="margin-top: 20px;"></div>
    <div id="debug" style="margin-top: 20px;"></div>
</body>
</html>
"""





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


_auto_analyze_lock = asyncio.Lock()


def _auto_analyze_worker(max_docs: int = 3) -> dict:
    """Find transcribed docs without condensed analysis, run pass 0b + embeddings.

    Runs synchronously (called from thread). Returns summary dict.
    """
    import hashlib
    from llm import get_embeddings

    # Check LLM kill switch
    settings = _db.get_cache("settings:llm_enabled")
    if not settings or not settings.get("enabled", False):
        return {"status": "skipped", "reason": "LLM disabled"}

    # Find candidates: transcribed docs without condensed analysis
    cache_suffix = ":pass_0b_condensed_v4"
    all_keys = _db.get_cache_keys()

    # Get all docs with transcriptions
    tx_hashes = _db.get_all_transcription_hashes()  # {doc_id: hash}
    candidates = []
    for doc_id, text_hash in tx_hashes.items():
        condensed_key = hashlib.sha256(
            (_db.get_document_transcription(doc_id).strip() + cache_suffix).encode()
        ).hexdigest()[:16]
        if condensed_key not in all_keys:
            candidates.append(doc_id)
        if len(candidates) >= max_docs:
            break

    if not candidates:
        return {"status": "complete", "message": "All transcribed docs already analyzed", "processed": 0}

    processed = []
    errors = []

    for doc_id in candidates:
        try:
            transcript = _db.get_document_transcription(doc_id)
            if not transcript or not transcript.strip():
                continue

            doc = _db.get_document(doc_id)
            title = doc.get("title", doc_id) if doc else doc_id

            # Run pass 0b somatic + language
            somatic_result = run_pass_0b(transcript)
            lang_result = run_pass_0b_lang(transcript)

            # Run condensed synthesis
            condensed_result = run_pass_0b_condensed(somatic_result, lang_result, transcript)

            _db.log_event("analysis_complete", title=title, document_id=doc_id,
                          detail={"segments": len(condensed_result.get("segments", []))})

            # Build embeddings
            summary_text = build_summary_from_condensed(condensed_result, title)
            summary_hash = hashlib.sha256(summary_text.encode()).hexdigest()[:16]

            embeddings, _ = get_embeddings([summary_text], task_type="RETRIEVAL_DOCUMENT")
            _db.upsert_embedding(doc_id, embeddings[0], summary_text, summary_hash)

            _db.log_event("embedding_complete", title=title, document_id=doc_id,
                          detail={"summary_length": len(summary_text)})

            processed.append({"doc_id": doc_id, "title": title})

        except Exception as e:
            err_title = title if 'title' in locals() else doc_id
            _db.log_event("analysis_error", title=err_title,
                          document_id=doc_id, detail={"error": str(e)})
            errors.append({"doc_id": doc_id, "error": str(e)})

    result = {
        "status": "complete",
        "processed": len(processed),
        "errors": len(errors),
        "details": processed,
    }
    _db.set_cache("pipeline:auto_analyze_last_result", result)
    return result


@app.post("/api/pipeline/auto-analyze")
async def auto_analyze(request: Request):
    """Run analysis + embeddings for transcribed docs missing condensed analysis."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)

    if _auto_analyze_lock.locked():
        return JSONResponse({"status": "already_running"})

    async def run_in_background():
        async with _auto_analyze_lock:
            try:
                result = await asyncio.to_thread(_auto_analyze_worker)
                return result
            except Exception as e:
                return {"status": "error", "error": str(e)}

    asyncio.create_task(run_in_background())
    return JSONResponse({"status": "started"})




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


# --- Landing page ---

@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Landing page: search home, plus dev tools for admin or local dev."""
    user = _auth_mod.get_current_user(request)
    search_mode = _is_search_site(request)
    show_dev = not IS_LAMBDA or (user and user.get("tier") == "admin")
    if not show_dev:
        return HTMLResponse(build_search_home_html(user=user))
    # Show dev tool links for admin or local dev
    links = []
    if _book_available:
        links.append('<li><a href="/book"><strong>Book</strong></a></li>')
        links.append('<li><a href="/analyzer">Analyzer</a></li>')
        links.append('<li><a href="/explore">Explore</a></li>')
    links.append('<li><a href="/pipeline">Pipeline Dashboard</a></li>')
    links.append('<li><a href="/pass0b">Pass 0b Analysis</a></li>')
    links.append('<li><a href="/pass0b-player">Pass 0b Player</a></li>')
    links.append('<li><a href="/pass0b-lang">Pass 0b Language</a></li>')
    dev_section = f"""<h2 style="margin-top:40px; border-top:1px solid #e0e0e0; padding-top:20px;">Dev Tools</h2>
<ul style="list-style:none; padding:0;">{"".join(links)}</ul>"""
    return HTMLResponse(build_search_home_html(extra_html=dev_section, user=user))


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
