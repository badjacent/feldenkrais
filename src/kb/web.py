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
import hashlib
import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from llm import LLM


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


# ── Background pipeline runner ───────────────────────────────────────────

_pipeline_task: asyncio.Task | None = None
_pipeline_lock = asyncio.Lock()


async def _pipeline_background_loop():
    """Background loop that rebuilds stale pipeline nodes."""
    await asyncio.sleep(5)  # let server finish starting
    while True:
        try:
            if _pipeline_lock.locked():
                await asyncio.sleep(30)
                continue
            if _is_pipeline_locked():
                await asyncio.sleep(10)
                continue
            async with _pipeline_lock:
                await asyncio.to_thread(_run_pipeline_cycle)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[pipeline] background error: {e}")
        await asyncio.sleep(30)


def _run_pipeline_cycle():
    """One synchronous cycle: compute staleness, run stale nodes."""
    if _is_pipeline_locked():
        return

    try:
        from pipeline import PipelineStateStore, PipelineExecutor
        from pipeline_graph import build_book_pipeline
    except ImportError:
        from .pipeline import PipelineStateStore, PipelineExecutor
        from .pipeline_graph import build_book_pipeline

    graph = build_book_pipeline(_db)
    store = PipelineStateStore(_db)
    store.initialize()
    executor = PipelineExecutor(graph, store)
    results = executor.run_stale(total_timeout_seconds=900)
    if isinstance(results, list) and results:
        fresh = sum(1 for r in results if r.status.value == "fresh")
        errors = sum(1 for r in results if r.status.value == "error")
        print(f"[pipeline] cycle complete: {fresh} succeeded, {errors} errors out of {len(results)} nodes")


@asynccontextmanager
async def _lifespan(app):
    global _pipeline_task
    if _db and not IS_LAMBDA:
        # Clear zombie RUNNING states from previous server run
        with _db.get_cursor() as cur:
            cur.execute(
                "UPDATE pipeline_node_state SET status = 'stale' WHERE status = 'running'"
            )
        _pipeline_task = asyncio.create_task(_pipeline_background_loop())
        print("[pipeline] background runner started")
    yield
    if _pipeline_task:
        _pipeline_task.cancel()
        try:
            await _pipeline_task
        except asyncio.CancelledError:
            pass
        print("[pipeline] background runner stopped")


app = FastAPI(title="Feldenkrais Lesson Analyzer", lifespan=_lifespan)

# Paths - handle both local development and Lambda environment
IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
if IS_LAMBDA:
    # In Lambda, data files are packaged alongside the code
    DATA_DIR = Path(__file__).parent / "data"
else:
    # Local development
    DATA_DIR = Path(__file__).parent.parent.parent / "data"
LESSONS_DIR = DATA_DIR / "lessons"


def _resolve_prompts_dir(version: str | None = None) -> tuple[Path, str]:
    """Resolve the versioned prompts directory. If version is None, read from pointer."""
    if version is None:
        pointer = DATA_DIR / "frameworks.json"
        if pointer.exists():
            version = json.loads(pointer.read_text())["current_version"]
        else:
            version = "v1"
    return DATA_DIR / "frameworks" / version, version


PROMPTS_DIR, FRAMEWORK_VERSION = _resolve_prompts_dir()


def get_available_framework_versions() -> list[str]:
    """List available framework versions by scanning the frameworks directory."""
    fw_dir = DATA_DIR / "frameworks"
    if not fw_dir.exists():
        return [FRAMEWORK_VERSION]
    return sorted(d.name for d in fw_dir.iterdir() if d.is_dir() and (d / "frameworks.json").exists())


def _framework_version_suffix(framework_version: str | None = None) -> str:
    """Return cache key suffix for framework version. Empty for v1 (backwards compat)."""
    v = framework_version or FRAMEWORK_VERSION
    if v == "v1":
        return ""
    return f":f{v}"


# --- Framework Registry ---
_frameworks_cache: dict[str, list[dict]] = {}


def load_frameworks(framework_version: str | None = None) -> list[dict]:
    """Load framework registry for a given version (cached per version)."""
    v = framework_version or FRAMEWORK_VERSION
    if v not in _frameworks_cache:
        prompts_dir, _ = _resolve_prompts_dir(v)
        path = prompts_dir / "frameworks.json"
        registry = json.loads(path.read_text())
        ver_key = registry.get("current_version", list(registry.get("versions", {}).keys())[0])
        _frameworks_cache[v] = registry["versions"][ver_key]["frameworks"]
    return _frameworks_cache[v]


def get_active_frameworks_config(framework_version: str | None = None) -> list[dict]:
    """Return only active frameworks from a given version."""
    return [f for f in load_frameworks(framework_version) if f.get("active", True)]


def build_classification_table(framework_version: str | None = None) -> str:
    """Build the VL classification markdown table from the framework registry."""
    rows = []
    for f in get_active_frameworks_config(framework_version):
        rows.append(f"| **{f['id']}** {f['name']} | {f['focus']} | {f['indicators']} |")
    header = "| Framework | Focus | Key Indicators |\n|-----------|-------|----------------|\n"
    return header + "\n".join(rows)


# Cache for VL booklet intros built from frameworks
_vl_booklet_intros_cache: dict[str, dict] = {}


def get_vl_booklet_intros(framework_version: str | None = None) -> dict[str, dict]:
    """Build VL_BOOKLET_INTROS dict from frameworks.json (cached)."""
    v = framework_version or FRAMEWORK_VERSION
    if v not in _vl_booklet_intros_cache:
        intros = {}
        for f in load_frameworks(v):
            intros[f["id"]] = {
                "name": f.get("name", ""),
                "subtitle": f.get("subtitle", ""),
                "lens": f.get("lens", ""),
                "person_inside": f.get("person_inside", ""),
                "person_description": f.get("person_description", ""),
                "habit_description": f.get("habit_description", ""),
                "habit_label": f.get("habit_label", ""),
            }
        _vl_booklet_intros_cache[v] = intros
    return _vl_booklet_intros_cache[v]


def build_pass_a_schema(framework_version: str | None = None) -> dict:
    """Build Pass A schema with dynamic classification properties from registry."""
    schema = json.loads((DATA_DIR / "pass_a.schema.json").read_text())
    active = get_active_frameworks_config(framework_version)
    cls_props = {}
    cls_required = []
    for f in active:
        cls_props[f["key"]] = {"type": "number", "minimum": 0, "maximum": 1}
        cls_required.append(f["key"])
    schema["properties"]["classifications"]["properties"] = cls_props
    schema["properties"]["classifications"]["required"] = cls_required
    schema["properties"]["primary_framework"]["enum"] = [f["id"] for f in active]
    schema["properties"]["secondary_frameworks"]["items"] = {
        "type": "string",
        "enum": [f["id"] for f in active],
    }
    return schema

# Database connection (optional - for loading lessons from DB)
_db = None
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    try:
        from database import Database
    except ImportError:
        from .database import Database
    _db = Database(DATABASE_URL)
    _db.initialize()


def get_cache_key(text: str) -> str:
    """Generate a cache key from lesson text."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_cached_analysis(cache_key: str) -> dict | None:
    """Load cached analysis result from database."""
    if _db:
        return _db.get_cache(cache_key)
    return None


def save_cached_analysis(cache_key: str, data: dict) -> None:
    """Save analysis result to database cache."""
    if _db:
        _db.set_cache(cache_key, data)


# S3 configuration for async jobs
KB_BUCKET = os.environ.get("KB_BUCKET")
LAMBDA_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME_SELF") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")

# System prompts
ROUTER_SYSTEM_PROMPT = """You are an expert Feldenkrais practitioner classifying lessons.
Analyze the lesson text and classify it according to which VL framework(s) best describe its pedagogical focus.
Be precise with your scores and provide specific indicators from the lesson text."""

ANALYSIS_SYSTEM_PROMPT = """You are an expert Feldenkrais practitioner analyzing lessons using the VL-1 protocol.
Follow the protocol exactly to analyze the lesson provided. Be thorough and specific in your analysis.

Important: The analysis table must cover ALL major movements identified in the lesson, from beginning to end.
This includes the final movements where the student returns to the initial position or rechecks their starting state.
Do not truncate the table early."""


def load_pass_a(framework_version: str | None = None) -> tuple[str, dict]:
    """Load Pass A prompt and schema (Decomposition + Routing).

    The prompt's {CLASSIFICATION_TABLE} placeholder is populated from
    the framework registry, and the schema's classification properties
    are built dynamically. Prompt comes from versioned PROMPTS_DIR,
    schema from shared DATA_DIR.
    """
    prompts_dir, _ = _resolve_prompts_dir(framework_version or FRAMEWORK_VERSION)
    prompt_path = prompts_dir / "pass_a.md"

    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    fv = framework_version or FRAMEWORK_VERSION
    prompt = prompt.replace("{CLASSIFICATION_TABLE}", build_classification_table(fv))

    schema = build_pass_a_schema(fv)

    return prompt, schema


def load_pass_b(framework: str, framework_version: str | None = None) -> tuple[str, dict]:
    """Load Pass B prompt and schema for a specific VL framework.

    Pass B receives Pass A output and applies the VL-specific lens.
    Prompts come from versioned PROMPTS_DIR, schema from shared DATA_DIR.
    """
    fv = framework_version or FRAMEWORK_VERSION
    prompts_dir, _ = _resolve_prompts_dir(fv)
    # Load base Pass B protocol
    base_path = prompts_dir / "pass_b.md"
    base_protocol = base_path.read_text() if base_path.exists() else ""

    # Load VL-specific module
    vl_map = {f["id"]: f["prompt_file"] for f in load_frameworks(fv)}
    vl_file = vl_map.get(framework, "vl1.md")
    vl_path = prompts_dir / vl_file
    vl_module = vl_path.read_text() if vl_path.exists() else ""

    # Combine: Pass B base + VL-specific framework
    protocol = base_protocol + "\n\n---\n\n## Framework: " + framework + "\n\n" + vl_module

    # Pass B schema (shared, unversioned)
    schema_path = DATA_DIR / "pass_b.schema.json"
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}

    return protocol, schema


# Legacy function for backwards compatibility
def load_protocol(framework: str = "VL-1") -> tuple[str, dict]:
    """Load protocol - delegates to load_pass_b for backwards compatibility."""
    return load_pass_b(framework)


def run_pass_0(text: str) -> dict:
    """
    Pass 0: Text Segmentation.

    Segments lesson text into sentence-level chunks with character offsets.
    Uses rule-based segmentation optimized for Feldenkrais lesson text patterns.

    Returns:
        {
            "segments": [{"id": 1, "text": "...", "start": 0, "end": 50}, ...],
            "original_length": 1234
        }
    """
    import re

    # Clean the text - normalize whitespace but preserve structure
    cleaned = text.strip()

    # Feldenkrais lessons often use "..." as instruction separators
    # Split on: sentence endings (. ! ?) followed by space/newline, or "..." patterns

    # Pattern explanation:
    # - Split on period/exclamation/question followed by whitespace
    # - Also split on "..." (ellipsis used as pause/transition marker)
    # - Also split on numbered steps like "1." or "2."
    # - Preserve the delimiter for proper offset calculation

    segments = []
    current_pos = 0
    segment_id = 1

    # Use regex to find sentence boundaries
    # Match: end punctuation + space, or ellipsis + space, or newline after text
    sentence_pattern = re.compile(
        r'([.!?](?:\s|$))|'  # Standard sentence endings
        r'(\.{2,}\s*)|'       # Ellipsis (2+ dots)
        r'(\n\s*\n)|'         # Paragraph breaks
        r'(\n(?=\d+\.\s))'    # Newline before numbered step
    )

    last_end = 0

    for match in sentence_pattern.finditer(cleaned):
        # Get the text from last_end to end of this match
        end_pos = match.end()
        segment_text = cleaned[last_end:end_pos].strip()

        if segment_text and len(segment_text) > 2:  # Skip tiny fragments
            segments.append({
                "id": segment_id,
                "text": segment_text,
                "start": last_end,
                "end": end_pos
            })
            segment_id += 1

        last_end = end_pos

    # Don't forget the last segment if text doesn't end with punctuation
    if last_end < len(cleaned):
        remaining = cleaned[last_end:].strip()
        if remaining and len(remaining) > 2:
            segments.append({
                "id": segment_id,
                "text": remaining,
                "start": last_end,
                "end": len(cleaned)
            })

    # If segmentation produced too few segments, fall back to simpler approach
    if len(segments) < 3 and len(cleaned) > 200:
        # Fall back: split on any sentence-ending punctuation
        segments = []
        segment_id = 1
        last_end = 0

        for match in re.finditer(r'[.!?]+\s*', cleaned):
            end_pos = match.end()
            segment_text = cleaned[last_end:end_pos].strip()
            if segment_text:
                segments.append({
                    "id": segment_id,
                    "text": segment_text,
                    "start": last_end,
                    "end": end_pos
                })
                segment_id += 1
            last_end = end_pos

        if last_end < len(cleaned):
            remaining = cleaned[last_end:].strip()
            if remaining:
                segments.append({
                    "id": segment_id,
                    "text": remaining,
                    "start": last_end,
                    "end": len(cleaned)
                })

    return {
        "segments": segments,
        "original_length": len(cleaned)
    }


def format_text_with_segments(pass_0_result: dict) -> str:
    """
    Format text with segment markers for LLM consumption.

    Returns text like:
    [1] Lie on your back on the floor.
    [2] Bend your knees so your feet are standing.
    ...
    """
    lines = []
    for seg in pass_0_result.get("segments", []):
        lines.append(f"[{seg['id']}] {seg['text']}")
    return "\n".join(lines)


def load_pass_0b() -> tuple[str, dict]:
    """Load Pass 0b prompt and schema (Segment Enrichment)."""
    prompt_path = DATA_DIR / "pass_0b.md"
    schema_path = DATA_DIR / "pass_0b.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def run_pass_0b(lesson_text: str, provider_key: str | None = None, debug_log: list | None = None) -> dict:
    """
    Pass 0b: LLM-based segment enrichment.

    Takes raw lesson text, runs Pass 0 to segment it, then sends segments
    to an LLM for enrichment (clarified text, movement, intent, implicit context).

    Returns:
        {
            "pass_0": { ... pass 0 result ... },
            "enriched_segments": [ { id, clarified, movement, intent, implicit }, ... ]
        }
    """
    import time

    # Run Pass 0 first
    pass_0_result = run_pass_0(lesson_text)
    segmented_text = format_text_with_segments(pass_0_result)

    # Load prompt and schema
    prompt_template, schema = load_pass_0b()

    # Build the full prompt
    prompt = prompt_template + segmented_text

    system_prompt = (
        "You are an expert Feldenkrais practitioner enriching lesson segments. "
        "Process every segment. Do not skip, merge, or split segments."
    )

    # Check cache
    cache_suffix = ":pass_0b"
    if provider_key and provider_key != DEFAULT_LLM:
        cache_suffix += f":{provider_key}"
    cache_key = get_cache_key(lesson_text.strip() + cache_suffix)
    cached = load_cached_analysis(cache_key)
    if cached:
        return {
            "pass_0": pass_0_result,
            "enriched_segments": cached.get("enriched_segments", []),
            "cached": True,
        }

    # Run LLM
    llm = get_llm(provider_key)
    t0 = time.time()

    if debug_log is not None:
        debug_log.append({
            "label": f"Pass 0b ({provider_key or DEFAULT_LLM})",
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "schema": schema,
        })

    try:
        result = llm.complete_structured_json(
            prompt=prompt,
            schema=schema,
            system=system_prompt,
        )
        elapsed = round(time.time() - t0, 1)

        if debug_log is not None:
            debug_log[-1]["output"] = json.dumps(result, indent=2)
            debug_log[-1]["elapsed"] = elapsed

        enriched = result.get("segments", [])

        # Cache the result
        save_cached_analysis(cache_key, {"enriched_segments": enriched})

        return {
            "pass_0": pass_0_result,
            "enriched_segments": enriched,
            "elapsed": elapsed,
            "cached": False,
        }

    except Exception as e:
        if debug_log is not None:
            debug_log[-1]["error"] = str(e)
        raise


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


# Threshold for running a VL analysis (score must be >= this)
VL_ANALYSIS_THRESHOLD = 0.5


LLM_CONFIGS = {
    "gemini": {
        "provider": "gemini",
        "model": "gemini-3-flash-preview",
        "api_key_env": "GOOGLE_API_KEY",
        "default_max_tokens": 65536,
    },
    "gemini-review": {
        "provider": "gemini",
        "model": "gemini-3-flash-preview",
        "api_key_env": "GOOGLE_API_KEY",
        "default_max_tokens": 65536,
        "thinking_level": "MEDIUM",
    },
    "gemini-2.0-flash": {
        "provider": "gemini",
        "model": "gemini-2.0-flash",
        "api_key_env": "GOOGLE_API_KEY",
        "default_max_tokens": 8192,
    },
    "openai": {
        "provider": "openai",
        "model": "gpt-5",
        "api_key_env": "OPENAI_API_KEY",
        "default_max_tokens": 128000,
    },
    "anthropic": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_max_tokens": 16384,
    },
}

DEFAULT_LLM = "gemini"


def get_llm(provider_key: str | None = None) -> LLM:
    """Get configured LLM instance.

    Args:
        provider_key: One of 'gemini', 'openai', 'anthropic'. Defaults to DEFAULT_LLM.
    """
    key = provider_key or DEFAULT_LLM
    config = LLM_CONFIGS.get(key)
    if not config:
        raise ValueError(f"Unknown LLM provider: {key}. Choose from: {list(LLM_CONFIGS.keys())}")

    api_key = os.environ.get(config["api_key_env"])
    if not api_key:
        raise ValueError(f"{config['api_key_env']} environment variable not set")

    return LLM(
        provider=config["provider"],
        model=config["model"],
        api_key=api_key,
        default_max_tokens=config["default_max_tokens"],
        thinking_level=config.get("thinking_level"),
    )


def get_available_lessons() -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    """Get categories and their display names.

    DB documents with category_name set are grouped under that name (override).
    DB documents without category_name are grouped under their source's category.

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
                    doc_cat = doc.get("category_name")
                    if doc_cat:
                        override_buckets.setdefault(doc_cat, []).append(
                            (str(doc["id"]), doc["title"])
                        )
                    else:
                        source_bucket.append((str(doc["id"]), doc["title"]))

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
        except Exception:
            pass

    # Skip empty sources (e.g. OTHER with no documents)
    categories = {k: v for k, v in categories.items() if v}

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


def _pass_a_cache_key(lesson_text: str, provider_key: str | None = None, framework_version: str | None = None) -> str:
    """Cache key for Pass A results, independent of Pass B provider."""
    suffix = ":pass_a" + _framework_version_suffix(framework_version)
    if provider_key and provider_key != DEFAULT_LLM:
        suffix += f":{provider_key}"
    return get_cache_key(lesson_text + suffix)


def run_pass_a(lesson_text: str, provider_key: str | None = None, debug_log: list | None = None, framework_version: str | None = None) -> dict:
    """Run Pass A: Decomposition + Routing.

    Internally runs Pass 0 to segment text, then passes segmented text to LLM.
    Returns VL classification, SPIFFER-Q decomposed steps, AND Pass 0 segments.
    Results are cached independently of Pass B provider.
    """
    fv = framework_version
    # Check Pass A cache first
    pa_cache_key = _pass_a_cache_key(lesson_text, provider_key, fv)
    cached = load_cached_analysis(pa_cache_key)
    if cached:
        if debug_log is not None:
            debug_log.append({
                "name": "Pass A",
                "provider": provider_key or DEFAULT_LLM,
                "cached": True,
            })
        return cached

    # Run Pass 0 to segment the text
    pass_0_result = run_pass_0(lesson_text)
    segmented_text = format_text_with_segments(pass_0_result)

    pass_a_prompt, pass_a_schema = load_pass_a(fv)

    if not pass_a_prompt or not pass_a_schema:
        # Return default if Pass A not configured
        active = get_active_frameworks_config(fv)
        default_cls = {f["key"]: (1.0 if i == 0 else 0.0) for i, f in enumerate(active)}
        return {
            "classifications": default_cls,
            "primary_framework": active[0]["id"] if active else "VL-1",
            "rationale": "Pass A not configured, defaulting to first framework",
            "steps": [],
            "pass_0": pass_0_result,
        }

    prompt = pass_a_prompt + "\n\n---\n\n## Lesson to Analyze:\n\n" + segmented_text

    llm = get_llm(provider_key)
    result = llm.complete_structured_json(
        prompt,
        pass_a_schema,
        system=ROUTER_SYSTEM_PROMPT,
    )

    if debug_log is not None:
        debug_log.append({
            "name": "Pass A",
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system": ROUTER_SYSTEM_PROMPT,
            "schema": pass_a_schema,
            "output": result,
        })

    # Attach Pass 0 segments to result for UI rendering
    result["pass_0"] = pass_0_result

    # Cache Pass A result independently
    save_cached_analysis(pa_cache_key, result)

    return result


# Alias for backwards compatibility
def run_router(lesson_text: str) -> dict:
    """Alias for run_pass_a for backwards compatibility."""
    return run_pass_a(lesson_text)


def get_active_frameworks(pass_a_result: dict, threshold: float = VL_ANALYSIS_THRESHOLD) -> list[str]:
    """Get list of VL frameworks that scored above the threshold."""
    classifications = pass_a_result.get("classifications", {})

    # Map classification keys to framework names from registry
    key_to_framework = {f["key"]: f["id"] for f in get_active_frameworks_config()}

    active = []
    for key, framework in key_to_framework.items():
        score = classifications.get(key, 0)
        if score >= threshold:
            active.append((framework, score))

    # Sort by score descending, return framework names
    active.sort(key=lambda x: -x[1])
    return [f for f, s in active]


def run_pass_b(lesson_text: str, pass_a_result: dict, framework: str, split: bool = False, provider_key: str | None = None, debug_log: list | None = None, framework_version: str | None = None) -> dict:
    """Run Pass B: VL Framework Synthesis.

    Takes the original lesson text AND Pass A output, applies VL-specific lens.
    If split=True, runs as two sequential queries (narrative then table) to
    avoid truncation on long lessons.
    """
    fv = framework_version
    if split:
        return run_pass_b_split(lesson_text, pass_a_result, framework, provider_key=provider_key, debug_log=debug_log, framework_version=fv)

    protocol, schema = load_pass_b(framework, fv)

    # Build prompt with both lesson text and Pass A output
    prompt = protocol + "\n\n---\n\n## Original Lesson Text:\n\n" + lesson_text
    prompt += "\n\n---\n\n## Pass A Analysis:\n\n```json\n" + json.dumps(pass_a_result, indent=2) + "\n```"

    llm = get_llm(provider_key)
    try:
        analysis = llm.complete_structured_json(
            prompt,
            schema,
            system=ANALYSIS_SYSTEM_PROMPT,
        )
    except Exception as e:
        if debug_log is not None:
            # Extract raw output from error message if available
            error_str = str(e)
            raw_output = error_str
            debug_log.append({
                "name": f"Pass B ({framework})",
                "provider": provider_key or DEFAULT_LLM,
                "prompt": prompt,
                "system": ANALYSIS_SYSTEM_PROMPT,
                "schema": schema,
                "error": raw_output,
            })
        raise

    if debug_log is not None:
        debug_log.append({
            "name": f"Pass B ({framework})",
            "provider": provider_key or DEFAULT_LLM,
            "prompt": prompt,
            "system": ANALYSIS_SYSTEM_PROMPT,
            "schema": schema,
            "output": analysis,
        })
    return analysis


def run_pass_b_split(lesson_text: str, pass_a_result: dict, framework: str, provider_key: str | None = None, debug_log: list | None = None, framework_version: str | None = None) -> dict:
    """Run Pass B as two sequential queries: narrative first, then table.

    The narrative (negotiation framework + somatic narrative) is generated first,
    then fed as context to the table query. This prevents truncation on long
    lessons and gives the table an interpretive anchor.
    """
    protocol, _ = load_pass_b(framework, framework_version)

    base_prompt = protocol + "\n\n---\n\n## Original Lesson Text:\n\n" + lesson_text
    base_prompt += "\n\n---\n\n## Pass A Analysis:\n\n```json\n" + json.dumps(pass_a_result, indent=2) + "\n```"

    llm = get_llm(provider_key)
    prov = provider_key or DEFAULT_LLM

    # Query 1: Narrative (negotiation framework + somatic narrative)
    narrative_schema = json.loads((DATA_DIR / "pass_b_narrative.schema.json").read_text())
    narrative_prompt = base_prompt + "\n\n---\n\nFor this query, provide ONLY the negotiation framework observations and somatic narrative. Do NOT include the analytical table."
    try:
        narrative_result = llm.complete_structured_json(
            narrative_prompt,
            narrative_schema,
            system=ANALYSIS_SYSTEM_PROMPT,
        )
    except Exception as e:
        if debug_log is not None:
            debug_log.append({
                "name": f"Pass B Narrative ({framework})",
                "provider": prov,
                "prompt": narrative_prompt,
                "system": ANALYSIS_SYSTEM_PROMPT,
                "schema": narrative_schema,
                "error": str(e),
            })
        raise

    if debug_log is not None:
        debug_log.append({
            "name": f"Pass B Narrative ({framework})",
            "provider": prov,
            "prompt": narrative_prompt,
            "system": ANALYSIS_SYSTEM_PROMPT,
            "schema": narrative_schema,
            "output": narrative_result,
        })

    # Query 2: Table (receives narrative as context)
    num_groups = len(pass_a_result.get("groups", []))
    num_rows = num_groups if num_groups > 0 else min(len(pass_a_result.get("steps", [])), 12)
    table_schema = json.loads((DATA_DIR / "pass_b_table.schema.json").read_text())
    table_prompt = base_prompt
    table_prompt += "\n\n---\n\n## Narrative Analysis (from prior query):\n\n```json\n" + json.dumps(narrative_result, indent=2) + "\n```"
    table_prompt += f"\n\n---\n\nUsing the narrative analysis above as your interpretive frame, provide ONLY the analytical table. Produce exactly {num_rows} rows — one per group from Pass A. Cover all groups from beginning to end — do not truncate."
    try:
        table_result = llm.complete_structured_json(
            table_prompt,
            table_schema,
            system=ANALYSIS_SYSTEM_PROMPT,
        )
    except Exception as e:
        if debug_log is not None:
            debug_log.append({
                "name": f"Pass B Table ({framework})",
                "provider": prov,
                "prompt": table_prompt,
                "system": ANALYSIS_SYSTEM_PROMPT,
                "schema": table_schema,
                "error": str(e),
            })
        raise

    if debug_log is not None:
        debug_log.append({
            "name": f"Pass B Table ({framework})",
            "provider": prov,
            "prompt": table_prompt,
            "system": ANALYSIS_SYSTEM_PROMPT,
            "schema": table_schema,
            "output": table_result,
        })

    # Merge into single result matching the combined Pass B schema
    return {**narrative_result, **table_result}


# Global to store Pass A result for the current request (used by run_vl_analysis alias)
_current_pass_a_result = None


def run_vl_analysis(lesson_text: str, framework: str, framework_version: str | None = None) -> dict:
    """Alias that runs Pass B using stored Pass A result.

    This is for backwards compatibility with code that calls run_vl_analysis
    after run_pass_a has already been called.
    """
    global _current_pass_a_result
    fv = framework_version
    if _current_pass_a_result is None:
        # If no Pass A result stored, run it first
        _current_pass_a_result = run_pass_a(lesson_text, framework_version=fv)
    return run_pass_b(lesson_text, _current_pass_a_result, framework, framework_version=fv)


def process_job(job_id: str) -> None:
    """Process a pending job (called by async Lambda invocation)."""
    job = load_job(job_id)
    if not job or job.get("status") != "pending":
        return

    try:
        lesson_text = job["lesson_text"]
        pa_provider = job.get("pass_a_provider")
        pb_provider = job.get("pass_b_provider")
        fv = job.get("framework_version")
        split = job.get("split_pass_b", False)

        # Use _run_analysis for consistent caching with local path
        result = _run_analysis(lesson_text, split=split, pass_a_provider=pa_provider, pass_b_provider=pb_provider, framework_version=fv)

        job["pass_a_result"] = result["pass_a_result"]
        job["router_result"] = result["pass_a_result"]
        job["vl_analyses"] = result["vl_analyses"]

        # For backwards compatibility, also store primary result in "result"
        active_frameworks = get_active_frameworks(result["pass_a_result"])
        if not active_frameworks:
            active_frameworks = [result["pass_a_result"].get("primary_framework", "VL-1")]
        primary = active_frameworks[0]
        if primary in job["vl_analyses"] and job["vl_analyses"][primary].get("status") == "complete":
            job["result"] = job["vl_analyses"][primary]["result"]

        job["status"] = "complete"
        save_job(job_id, job)

    except Exception as e:
        # Update job with error
        job["status"] = "error"
        job["error"] = str(e)
        save_job(job_id, job)


def render_router_result(router_result: dict) -> str:
    """Render router classification as HTML."""
    if not router_result:
        return ""

    html = ['<div class="router-result" style="background: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">']
    html.append("<h2>Framework Classification</h2>")

    # Primary framework
    primary = router_result.get("primary_framework", "Unknown")
    html.append(f'<p><strong>Primary Framework:</strong> <span style="font-size: 1.2em; color: #007bff;">{primary}</span></p>')

    # Secondary frameworks
    secondary = router_result.get("secondary_frameworks", [])
    if secondary:
        html.append(f'<p><strong>Secondary:</strong> {", ".join(secondary)}</p>')

    # Classification scores as a simple bar chart
    classifications = router_result.get("classifications", {})
    if classifications:
        html.append('<div style="margin: 15px 0;">')
        framework_names = {f["key"]: f["display_name"] for f in load_frameworks()}
        for key, name in framework_names.items():
            score = classifications.get(key, 0)
            pct = int(score * 100)
            color = "#007bff" if score >= 0.6 else "#6c757d" if score >= 0.3 else "#dee2e6"
            html.append(f'''
                <div style="margin: 5px 0;">
                    <span style="display: inline-block; width: 150px;">{name}</span>
                    <span style="display: inline-block; width: 200px; background: #eee; border-radius: 4px;">
                        <span style="display: inline-block; width: {pct}%; background: {color}; height: 20px; border-radius: 4px;"></span>
                    </span>
                    <span style="margin-left: 10px;">{score:.2f}</span>
                </div>
            ''')
        html.append('</div>')

    # Rationale
    if router_result.get("rationale"):
        html.append(f'<p><strong>Rationale:</strong> {router_result["rationale"]}</p>')

    # Key indicators (from router)
    indicators = router_result.get("key_indicators", [])
    if indicators:
        html.append("<details><summary><strong>Key Indicators</strong></summary><ul>")
        for ind in indicators:
            framework = ind.get("framework", "")
            indicator = ind.get("indicator", "")
            html.append(f"<li><strong>{framework}:</strong> {indicator}</li>")
        html.append("</ul></details>")

    html.append("</div>")

    # SPIFFER-Q Decomposition (Pass A output)
    steps = router_result.get("steps", [])
    groups = router_result.get("groups", [])
    if steps:
        html.append('<div class="spiffer-q-steps" style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-bottom: 20px;">')

        # Lesson title if present
        if router_result.get("lesson_title"):
            html.append(f"<h2>{router_result['lesson_title']}</h2>")

        html.append("<h3>SPIFFER-Q Decomposition (Pass A)</h3>")

        # Home position
        home_pos = router_result.get("home_position")
        if home_pos:
            html.append(f'<p style="margin-bottom: 12px; padding: 8px 12px; background: #e8f4fd; border-left: 3px solid #17a2b8; border-radius: 4px;"><strong>Home position:</strong> {home_pos}</p>')

        # Build segment lookup from Pass 0
        pass_0 = router_result.get("pass_0", {})
        segments_list = pass_0.get("segments", [])
        segment_lookup = {seg["id"]: seg["text"] for seg in segments_list}

        # Build step lookup by step_number
        step_lookup = {s["step_number"]: s for s in steps if "step_number" in s}

        def _render_step(step):
            """Render a single step as an <li> with tags, instruction, and individual source segments."""
            tags = ", ".join(step.get("tags", []))
            instruction = step.get("instruction", "")
            note = f' <small style="color: #666;">({step["note"]})</small>' if step.get("note") else ""
            home_badge = ' <span style="font-size: 0.8em; padding: 1px 6px; background: #e8f4fd; color: #17a2b8; border-radius: 3px;">home</span>' if step.get("is_home") else ""

            segment_ids = step.get("segments", [])
            parts = []
            parts.append(f"<li><strong>[{tags}]</strong> {instruction}{home_badge}{note}")

            # Show individual source segments
            if segment_ids:
                parts.append('<details style="margin-top: 6px; margin-left: 20px;">')
                parts.append(f'<summary style="cursor: pointer; color: #666; font-size: 0.9em;">Source text ({len(segment_ids)} segment{"s" if len(segment_ids) != 1 else ""})</summary>')
                parts.append('<div style="margin: 6px 0;">')
                for sid in segment_ids:
                    seg_text = segment_lookup.get(sid, f"[segment {sid} not found]")
                    parts.append(f'<div style="margin: 4px 0; padding: 6px 10px; background: #fff; border-left: 3px solid #007bff; font-style: italic; color: #555; font-size: 0.9em;">'
                                 f'<span style="color: #999; font-size: 0.8em; font-style: normal;">#{sid}</span> {seg_text}</div>')
                parts.append('</div></details>')

            parts.append("</li>")
            return "\n".join(parts)

        if groups:
            # Three-level tree: Group → Steps → Segments
            # Track which steps are assigned to groups
            grouped_step_nums = set()
            for g in groups:
                grouped_step_nums.update(g.get("steps", []))

            for g in groups:
                group_label = g.get("label", f"Group {g.get('group_number', '?')}")
                group_desc = g.get("description", "")
                group_step_nums = g.get("steps", [])

                html.append('<details open style="margin-bottom: 12px;">')
                html.append(f'<summary style="cursor: pointer; font-weight: bold; font-size: 1.05em; padding: 6px 0;">'
                            f'{g.get("group_number", "?")}. {group_label}</summary>')
                if group_desc:
                    html.append(f'<p style="margin: 4px 0 8px 20px; color: #555; font-size: 0.9em;">{group_desc}</p>')

                html.append('<ol style="margin-left: 20px;">')
                for sn in group_step_nums:
                    step = step_lookup.get(sn)
                    if step:
                        html.append(_render_step(step))
                html.append('</ol>')
                html.append('</details>')

            # Render any ungrouped steps
            ungrouped = [s for s in steps if s.get("step_number") not in grouped_step_nums]
            if ungrouped:
                html.append('<details style="margin-bottom: 12px;">')
                html.append('<summary style="cursor: pointer; font-weight: bold; font-size: 1.05em; padding: 6px 0;">Ungrouped Steps</summary>')
                html.append('<ol style="margin-left: 20px;">')
                for step in ungrouped:
                    html.append(_render_step(step))
                html.append('</ol>')
                html.append('</details>')
        else:
            # Fallback: flat list (no groups available)
            html.append("<ol>")
            for step in steps:
                html.append(_render_step(step))
            html.append("</ol>")

        html.append("</div>")

    return "\n".join(html)


def render_multi_vl_analyses(vl_analyses: dict, schema: dict, pass_a_result: dict = None) -> str:
    """Render multiple VL analyses with tabs for switching between them."""
    if not vl_analyses:
        return ""

    frameworks = list(vl_analyses.keys())

    if len(frameworks) == 1:
        # Single framework, no tabs needed
        framework = frameworks[0]
        entry = vl_analyses[framework]
        if entry.get("status") == "complete" and entry.get("result"):
            return f'<h2>{framework} Analysis</h2>' + render_analysis(entry["result"], schema, pass_a_result)
        elif entry.get("status") == "error":
            return f'<div class="error"><strong>{framework}:</strong> {entry.get("error", "Unknown error")}</div>'
        return ""

    # Multiple frameworks - use tabs
    html = []

    # Tab buttons
    html.append('<div class="vl-tabs" style="margin-bottom: 20px;">')
    for i, framework in enumerate(frameworks):
        active = "background: #007bff; color: white;" if i == 0 else "background: #e9ecef; color: #333;"
        html.append(f'''
            <button onclick="showVLTab('{framework}')" id="tab-{framework}"
                    style="padding: 10px 20px; border: none; cursor: pointer; {active} border-radius: 4px 4px 0 0; margin-right: 2px;">
                {framework}
            </button>
        ''')
    html.append('</div>')

    # Tab content
    for i, framework in enumerate(frameworks):
        display = "block" if i == 0 else "none"
        entry = vl_analyses[framework]

        html.append(f'<div id="content-{framework}" class="vl-content" style="display: {display};">')
        html.append(f'<h2>{framework} Analysis</h2>')

        if entry.get("status") == "complete" and entry.get("result"):
            html.append(render_analysis(entry["result"], schema, pass_a_result))
        elif entry.get("status") == "error":
            html.append(f'<div class="error">Error: {entry.get("error", "Unknown error")}</div>')
        else:
            html.append('<p>No analysis available.</p>')

        html.append('</div>')

    # Note: Tab switching JavaScript is in the main page template (build_html)

    return "\n".join(html)


def render_debug_log(debug_log: list) -> str:
    """Render raw LLM input/output debug log as collapsible HTML sections."""
    if not debug_log:
        return ""

    import html as html_module

    parts = ['<div class="debug-log" style="margin-top: 30px; border-top: 2px solid #ddd; padding-top: 15px;">']
    parts.append('<h2 style="cursor: pointer;" onclick="document.getElementById(\'debug-contents\').style.display = document.getElementById(\'debug-contents\').style.display === \'none\' ? \'block\' : \'none\';">Raw I/O <span style="font-size: 0.7em; color: #999;">(click to toggle)</span></h2>')
    parts.append('<div id="debug-contents" style="display: none;">')

    # Sub-tabs for each exchange
    parts.append('<div class="debug-tabs" style="margin-bottom: 10px; display: flex; flex-wrap: wrap; gap: 4px;">')
    for i, entry in enumerate(debug_log):
        name = entry.get("name", f"Query {i+1}")
        provider = entry.get("provider", "")
        has_error = "error" in entry
        color = "#dc3545" if has_error else "#6c757d"
        active = "background: #007bff; color: white;" if i == 0 else f"background: #e9ecef; color: {color};"
        label = f'{name} [{provider}]'
        if has_error:
            label += " (error)"
        parts.append(f'<button onclick="showDebugTab({i})" id="debug-tab-{i}" style="padding: 6px 12px; border: 1px solid #ccc; cursor: pointer; {active} border-radius: 4px; font-size: 0.85em;">{label}</button>')
    parts.append('</div>')

    for i, entry in enumerate(debug_log):
        display = "block" if i == 0 else "none"
        name = entry.get("name", f"Query {i+1}")
        provider = entry.get("provider", "")
        parts.append(f'<div id="debug-content-{i}" class="debug-entry" style="display: {display};">')

        # Prompt
        prompt = entry.get("prompt", "")
        prompt_chars = len(prompt)
        parts.append(f'<details style="margin-bottom: 10px;"><summary style="cursor: pointer; font-weight: bold; color: #555;">Prompt ({prompt_chars:,} chars)</summary>')
        parts.append(f'<pre style="background: #f8f9fa; padding: 12px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word; max-height: 500px; overflow-y: auto; font-size: 0.8em;">{html_module.escape(prompt)}</pre>')
        parts.append('</details>')

        # System prompt
        system = entry.get("system", "")
        if system:
            parts.append(f'<details style="margin-bottom: 10px;"><summary style="cursor: pointer; font-weight: bold; color: #555;">System Prompt ({len(system):,} chars)</summary>')
            parts.append(f'<pre style="background: #f0f8ff; padding: 12px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word; max-height: 300px; overflow-y: auto; font-size: 0.8em;">{html_module.escape(system)}</pre>')
            parts.append('</details>')

        # Schema
        schema = entry.get("schema")
        if schema:
            schema_str = json.dumps(schema, indent=2)
            parts.append(f'<details style="margin-bottom: 10px;"><summary style="cursor: pointer; font-weight: bold; color: #555;">Schema ({len(schema_str):,} chars)</summary>')
            parts.append(f'<pre style="background: #fff8f0; padding: 12px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word; max-height: 400px; overflow-y: auto; font-size: 0.8em;">{html_module.escape(schema_str)}</pre>')
            parts.append('</details>')

        # Output or Error
        if "error" in entry:
            error_str = entry["error"]
            parts.append(f'<details open style="margin-bottom: 10px;"><summary style="cursor: pointer; font-weight: bold; color: #dc3545;">Output (ERROR)</summary>')
            parts.append(f'<pre style="background: #fff5f5; padding: 12px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word; max-height: 600px; overflow-y: auto; font-size: 0.8em; border: 1px solid #f5c6cb;">{html_module.escape(error_str)}</pre>')
            parts.append('</details>')
        elif "output" in entry:
            output_str = json.dumps(entry["output"], indent=2)
            parts.append(f'<details style="margin-bottom: 10px;"><summary style="cursor: pointer; font-weight: bold; color: #555;">Output ({len(output_str):,} chars)</summary>')
            parts.append(f'<pre style="background: #f0fff0; padding: 12px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word; max-height: 600px; overflow-y: auto; font-size: 0.8em;">{html_module.escape(output_str)}</pre>')
            parts.append('</details>')

        parts.append('</div>')

    parts.append('</div></div>')
    return "\n".join(parts)


def render_pass_b_analysis(data: dict, pass_a_result: dict = None) -> str:
    """Render Pass B analysis result as HTML."""
    html = []

    framework = data.get("framework", "")
    if framework:
        html.append(f'<p><em>Analysis through {framework} lens</em></p>')

    # Negotiation Framework
    neg = data.get("negotiation_framework", {})
    if neg:
        html.append("<h3>Negotiation Framework</h3>")

        if neg.get("shadow_observations"):
            html.append("<h4>Habitual Effort</h4><ul>")
            for obs in neg["shadow_observations"]:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

        if neg.get("foundation_observations"):
            html.append("<h4>The Foundation (Support System)</h4><ul>")
            for obs in neg["foundation_observations"]:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

        if neg.get("washing_gradient"):
            html.append(f"<h4>Washing Gradient</h4><p>{neg['washing_gradient']}</p>")

    # Somatic Narrative
    narrative = data.get("somatic_narrative", {})
    if narrative:
        html.append("<h3>Somatic Narrative</h3>")

        if narrative.get("primary_inquiry"):
            html.append(f"<h4>Primary Inquiry</h4><p>{narrative['primary_inquiry']}</p>")

        if narrative.get("washing_gradient"):
            html.append(f"<h4>Washing Gradient</h4><p>{narrative['washing_gradient']}</p>")

        if narrative.get("threshold_of_quiet"):
            html.append(f"<h4>Threshold of Quiet</h4><p>{narrative['threshold_of_quiet']}</p>")

    # Analytical Table / Card View
    table = data.get("analytical_table", {})
    if table:
        rows = table.get("rows", [])
        column_headers = table.get("column_headers", {})

        if rows:
            # Determine columns
            if column_headers:
                columns = sorted([k for k in column_headers.keys() if column_headers.get(k)],
                                key=lambda x: int(x.replace("column", "")) if x.startswith("column") else 999)
                display_names = {col: column_headers[col] for col in columns}
            else:
                columns = ["column1", "column2", "column3", "column4", "column5"]
                display_names = {c: c for c in columns}

            # Build Pass A step lookup and segment lookup
            pass_a_steps = {}
            segment_lookup = {}
            segment_data = {}
            if pass_a_result:
                for step in pass_a_result.get("steps", []):
                    pass_a_steps[step["step_number"]] = step
                pass_0 = pass_a_result.get("pass_0", {})
                for seg in pass_0.get("segments", []):
                    segment_lookup[seg["id"]] = seg["text"]
                    segment_data[seg["id"]] = seg

            # Tab control: Card View (default) / Table View
            html.append(f'<h3>Analytical Table ({len(rows)} rows)</h3>')
            html.append('''
                <div style="margin-bottom: 10px;">
                    <button onclick="showAnalyticalView('cards')" id="btn-cards"
                            style="padding: 6px 16px; border: none; cursor: pointer; background: #007bff; color: white; border-radius: 4px 4px 0 0; margin-right: 2px;">
                        Card View
                    </button>
                    <button onclick="showAnalyticalView('table')" id="btn-table"
                            style="padding: 6px 16px; border: none; cursor: pointer; background: #e9ecef; color: #333; border-radius: 4px 4px 0 0;">
                        Table View
                    </button>
                </div>
            ''')

            # === TABLE VIEW (hidden by default) ===
            html.append('<div id="analytical-table-view" style="display: none;">')
            html.append("""
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
                <thead style="background-color: #f0f0f0;">
                    <tr>
            """)
            for col in columns:
                html.append(f"<th>{display_names.get(col, col)}</th>")
            html.append("</tr></thead><tbody>")

            for row in rows:
                html.append("<tr>")
                for col in columns:
                    val = row.get(col, "")
                    if isinstance(val, list):
                        val = ", ".join(str(v) for v in val)
                    # Add timestamp annotation to Seq column
                    if col == "column1" and pass_a_steps and segment_data and val:
                        row_steps = _parse_seq_to_steps(str(val), pass_a_steps)
                        if row_steps:
                            sids = [sid for step in row_steps for sid in step.get("segments", [])]
                            ts = [segment_data[s].get("time_start") for s in sids if s in segment_data and "time_start" in segment_data[s]]
                            te = [segment_data[s].get("time_end") for s in sids if s in segment_data and "time_end" in segment_data[s]]
                            if ts and te:
                                time_str = f"{format_timestamp(min(ts))} \u2013 {format_timestamp(max(te))}"
                                val = f'{val}<br><small style="color: #007bff;">{time_str}</small>'
                    html.append(f"<td>{val}</td>")
                html.append("</tr>")

                # Add voice/shadow row if present
                vl_voice = row.get("vl_voice", "")
                shadow_voice = row.get("shadow_voice", "") or row.get("striver_shadow", "")
                if vl_voice or shadow_voice:
                    html.append(f'<tr><td colspan="{len(columns)}" style="background: #fafafa; padding: 8px 12px; font-size: 0.9em;">')
                    if vl_voice:
                        html.append(f'<div style="margin-bottom: 6px;"><span style="color: #007bff; font-weight: bold;">Person Inside:</span> <em>{vl_voice}</em></div>')
                    if shadow_voice:
                        html.append(f'<div><span style="color: #dc3545; font-weight: bold;">Habit:</span> <em>{shadow_voice}</em></div>')
                    html.append('</td></tr>')

            html.append("</tbody></table></div>")

            # === CARD VIEW (shown by default) ===
            html.append('<div id="analytical-card-view">')

            # Navigation
            nav_html = f'''
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 15px;">
                    <button onclick="prevCard()" id="card-prev-btn"
                            style="padding: 10px 20px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; background: white; font-size: 1em;">
                        \u2190 Previous
                    </button>
                    <span id="card-counter" style="font-weight: bold; color: #555; font-size: 1.1em;">1 / {len(rows)}</span>
                    <button onclick="nextCard()" id="card-next-btn"
                            style="padding: 10px 20px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; background: white; font-size: 1em;">
                        Next \u2192
                    </button>
                </div>
            '''
            html.append(nav_html)

            # Render each card
            for i, row in enumerate(rows):
                display = "block" if i == 0 else "none"
                html.append(f'<div class="analytical-card" data-card-index="{i}" style="display: {display}; border: 1px solid #ddd; border-radius: 8px; padding: 20px; background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">')

                # Card header - Seq, Negotiation State, and Timestamp
                seq_val = row.get("column1", "")

                # Compute matched steps and card timestamp early
                matched_steps = _parse_seq_to_steps(seq_val, pass_a_steps) if pass_a_steps and seq_val else []
                card_time_str = ""
                if matched_steps and segment_data:
                    _sids = [sid for step in matched_steps for sid in step.get("segments", [])]
                    _ts = [segment_data[s].get("time_start") for s in _sids if s in segment_data and "time_start" in segment_data[s]]
                    _te = [segment_data[s].get("time_end") for s in _sids if s in segment_data and "time_end" in segment_data[s]]
                    if _ts and _te:
                        card_time_str = f"{format_timestamp(min(_ts))} \u2013 {format_timestamp(max(_te))}"

                neg_state = ""
                # Find the negotiation state / what's happening column (typically column4)
                for col in columns:
                    header = display_names.get(col, "").lower()
                    if "negotiation" in header or "state" in header or "happening" in header:
                        neg_state = row.get(col, "")
                        break

                state_colors = {
                    "domination": "#dc3545",
                    "negotiation": "#fd7e14",
                    "integration": "#28a745",
                }
                state_color = state_colors.get(neg_state.lower(), "#6c757d") if neg_state else "#6c757d"

                time_html = f'<span style="font-size: 0.85em; color: #007bff; margin-left: 10px;">{card_time_str}</span>' if card_time_str else ''
                html.append(f'''
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <div>
                            <span style="font-size: 0.9em; color: #999;">Steps {seq_val}</span>
                            {time_html}
                        </div>
                        <span style="padding: 4px 12px; border-radius: 12px; font-size: 0.85em; font-weight: bold; color: white; background: {state_color};">{neg_state}</span>
                    </div>
                ''')

                # Main content - the row columns (skip seq/column1 and negotiation state)
                for col in columns:
                    header = display_names.get(col, col)
                    val = row.get(col, "")
                    if isinstance(val, list):
                        val = ", ".join(str(v) for v in val)
                    if not val or col == "column1":
                        continue
                    header_lower = header.lower()
                    if "negotiation" in header_lower or "state" in header_lower or "happening" in header_lower:
                        continue  # already shown as badge
                    html.append(f'''
                        <div style="margin-bottom: 12px;">
                            <div style="font-size: 0.8em; font-weight: bold; color: #888; text-transform: uppercase; margin-bottom: 4px;">{header}</div>
                            <div>{val}</div>
                        </div>
                    ''')

                # VL Voice and Shadow Voice
                vl_voice = row.get("vl_voice", "")
                shadow_voice = row.get("shadow_voice", "") or row.get("striver_shadow", "")
                if vl_voice or shadow_voice:
                    html.append('<div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #eee;">')
                    if vl_voice:
                        html.append(f'''
                            <div style="margin-bottom: 12px; padding: 12px 16px; background: #f0f7ff; border-radius: 6px; border-left: 3px solid #007bff;">
                                <div style="font-size: 0.75em; font-weight: bold; color: #007bff; text-transform: uppercase; margin-bottom: 4px;">The Person Inside</div>
                                <div style="font-style: italic; color: #333; line-height: 1.5;">{vl_voice}</div>
                            </div>
                        ''')
                    if shadow_voice:
                        html.append(f'''
                            <div style="margin-bottom: 12px; padding: 12px 16px; background: #fff5f5; border-radius: 6px; border-left: 3px solid #dc3545;">
                                <div style="font-size: 0.75em; font-weight: bold; color: #dc3545; text-transform: uppercase; margin-bottom: 4px;">The Habit</div>
                                <div style="font-style: italic; color: #555; line-height: 1.5;">{shadow_voice}</div>
                            </div>
                        ''')
                    html.append('</div>')

                # SPIFFER-Q steps from Pass A that match this row's Seq
                if matched_steps:
                    html.append('<div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #eee;">')
                    html.append('<div style="font-size: 0.8em; font-weight: bold; color: #888; text-transform: uppercase; margin-bottom: 8px;">SPIFFER-Q Steps</div>')
                    for step in matched_steps:
                        tags = ", ".join(step.get("tags", []))
                        instruction = step.get("instruction", "")
                        note = f' <span style="color: #999;">({step["note"]})</span>' if step.get("note") else ""
                        html.append(f'<div style="margin-bottom: 6px; padding: 6px 10px; background: #f8f9fa; border-radius: 4px;"><strong>[{tags}]</strong> {instruction}{note}</div>')
                    html.append('</div>')

                    # Source text from segments
                    all_segment_ids = []
                    for step in matched_steps:
                        all_segment_ids.extend(step.get("segments", []))
                    if all_segment_ids and segment_lookup:
                        source_texts = [segment_lookup.get(sid, "") for sid in all_segment_ids if segment_lookup.get(sid)]
                        if source_texts:
                            html.append('<details style="margin-top: 10px;">')
                            html.append(f'<summary style="cursor: pointer; color: #666; font-size: 0.85em;">Source text (segments {", ".join(str(s) for s in all_segment_ids)})</summary>')
                            html.append('<blockquote style="margin: 8px 0; padding: 10px 14px; background: #fafafa; border-left: 3px solid #007bff; font-style: italic; color: #555; line-height: 1.6;">')
                            html.append(" ".join(source_texts))
                            html.append('</blockquote></details>')

                html.append('</div>')  # end card

            html.append('</div>')  # end card view

    return "\n".join(html)


def _parse_seq_to_steps(seq_val: str, pass_a_steps: dict) -> list:
    """Parse a Seq column value (e.g., '1-2', '3', '4-6') and return matching Pass A steps."""
    import re
    matched = []
    # Handle ranges like "1-3" and single numbers like "4", and combos like "1-2, 4"
    for part in re.split(r'[,;]\s*', str(seq_val)):
        part = part.strip()
        range_match = re.match(r'(\d+)\s*[-–]\s*(\d+)', part)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            for n in range(start, end + 1):
                if n in pass_a_steps:
                    matched.append(pass_a_steps[n])
        else:
            num_match = re.match(r'(\d+)', part)
            if num_match:
                n = int(num_match.group(1))
                if n in pass_a_steps:
                    matched.append(pass_a_steps[n])
    return matched


def render_analysis(data: dict, schema: dict, pass_a_result: dict = None) -> str:
    """Render analysis dict as HTML based on schema structure.

    Handles both old (washing.schema.json) and new (pass_b.schema.json) formats.
    """
    # Check if this is Pass B format (has negotiation_framework, somatic_narrative, analytical_table)
    if "negotiation_framework" in data or "somatic_narrative" in data or "analytical_table" in data:
        return render_pass_b_analysis(data, pass_a_result)

    # Otherwise, use legacy rendering
    html = []

    # Title
    if data.get("lesson_title"):
        html.append(f"<h1>{data['lesson_title']}</h1>")

    # Get schema properties to understand structure
    props = schema.get("properties", {})
    defs = schema.get("$defs", {})

    # Phase 1: SPIFFER-Q (look for phase1_spifferq or phase1_spiffer)
    phase1_key = next((k for k in data.keys() if k.startswith("phase1")), None)
    if phase1_key and data.get(phase1_key):
        phase1 = data[phase1_key]
        html.append("<h2>Phase 1: SPIFFER-Q Tagging</h2>")
        if phase1.get("summary"):
            html.append(f"<p><em>{phase1['summary']}</em></p>")
        if phase1.get("steps"):
            html.append("<ol>")
            for step in phase1["steps"]:
                tags = ", ".join(step.get("tags", []))
                instruction = step.get("instruction", "")
                notes = f" <small>({step['notes']})</small>" if step.get("notes") else ""
                html.append(f"<li><strong>[{tags}]</strong> {instruction}{notes}</li>")
            html.append("</ol>")

    # Phase 2: Negotiation Framework
    phase2_key = next((k for k in data.keys() if k.startswith("phase2")), None)
    if phase2_key and data.get(phase2_key):
        phase2 = data[phase2_key]
        html.append("<h2>Phase 2: The Negotiation Framework</h2>")

        # Shadow observations (fallback: old "striver" key for cached data)
        shadow_key = next((k for k in phase2.keys() if "shadow" in k.lower() or "striver" in k.lower() or "effort" in k.lower()), None)
        if shadow_key and phase2.get(shadow_key):
            html.append("<h3>Habitual Effort</h3>")
            html.append("<ul>")
            for obs in phase2[shadow_key]:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

        # Foundation observations
        foundation_key = next((k for k in phase2.keys() if "foundation" in k.lower() or "support" in k.lower()), None)
        if foundation_key and phase2.get(foundation_key):
            html.append("<h3>The Foundation (Support System)</h3>")
            html.append("<ul>")
            for obs in phase2[foundation_key]:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

        # Washing gradient in phase 2
        if phase2.get("washing_gradient"):
            html.append("<h3>The Washing Gradient</h3>")
            html.append(f"<p>{phase2['washing_gradient']}</p>")

    # Phase 3: Somatic Narrative (or Washing Dynamic for v1)
    phase3_narrative_key = next((k for k in data.keys() if "narrative" in k.lower() or "somatic" in k.lower()), None)
    if phase3_narrative_key and data.get(phase3_narrative_key):
        narrative = data[phase3_narrative_key]
        html.append("<h2>Phase 3: The Somatic Narrative</h2>")

        if narrative.get("primary_inquiry"):
            html.append("<h3>The Primary Inquiry</h3>")
            html.append(f"<p>{narrative['primary_inquiry']}</p>")

        if narrative.get("washing_gradient"):
            html.append("<h3>The Washing Gradient</h3>")
            html.append(f"<p>{narrative['washing_gradient']}</p>")

        if narrative.get("threshold_of_quiet"):
            html.append("<h3>The Threshold of Quiet</h3>")
            html.append(f"<p>{narrative['threshold_of_quiet']}</p>")

    # Fallback for v1 washing phase
    if data.get("phase3_washing"):
        washing = data["phase3_washing"]
        html.append("<h2>Phase 3: The Washing Dynamic</h2>")
        if washing.get("washing_moment_identified"):
            html.append("<p><strong>Washing moment identified:</strong> Yes</p>")
            if washing.get("description"):
                html.append(f"<p><strong>Description:</strong> {washing['description']}</p>")
            if washing.get("mechanism"):
                html.append(f"<p><strong>Mechanism:</strong> {washing['mechanism']}</p>")
            if washing.get("result"):
                html.append(f"<p><strong>Result:</strong> {washing['result']}</p>")
        else:
            html.append("<p><strong>Washing moment identified:</strong> No</p>")

    # Table: Look for any key containing "table"
    table_key = next((k for k in data.keys() if "table" in k.lower()), None)
    if table_key and data.get(table_key):
        table_data = data[table_key]
        rows = table_data.get("rows", [])
        column_headers = table_data.get("column_headers", {})

        html.append(f"<h2>VL-1 Analytical Table ({len(rows)} rows)</h2>")

        if rows:
            # Determine columns to display
            if column_headers:
                # Use column_headers to determine which columns and their display names
                # Sort by column number (column1, column2, etc.)
                columns = sorted([k for k in column_headers.keys() if column_headers.get(k)],
                                key=lambda x: int(x.replace("column", "")) if x.startswith("column") else 999)
                display_names = {col: column_headers[col] for col in columns}
            else:
                # Fallback: infer columns from first row
                first_row = rows[0]
                columns = list(first_row.keys())
                display_names = {col: col.replace("_", " ").replace("spiffer q", "SPIFFER-Q").title() for col in columns}

            html.append("""
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
                <thead style="background-color: #f0f0f0;">
                    <tr>
            """)
            for col in columns:
                html.append(f"<th>{display_names.get(col, col)}</th>")
            html.append("</tr></thead><tbody>")

            for row in rows:
                html.append("<tr>")
                for col in columns:
                    val = row.get(col, "")
                    # Handle list values (like tags)
                    if isinstance(val, list):
                        val = ", ".join(str(v) for v in val)
                    html.append(f"<td>{val}</td>")
                html.append("</tr>")

            html.append("</tbody></table>")

    return "\n".join(html)


def build_html(lesson_text: str = "", selected_category: str = "", selected_lesson: str = "", result: str = "") -> str:
    """Build the HTML page."""
    categories, display_names = get_available_lessons()

    # Build category options
    category_options = ['<option value="">-- Select category --</option>']
    for cat_name in categories.keys():
        cat_display = display_names.get(cat_name, cat_name.replace("_", " ").title())
        selected = "selected" if cat_name == selected_category else ""
        category_options.append(f'<option value="{cat_name}" {selected}>{cat_display}</option>')
    category_select = "\n".join(category_options)

    # Build lessons data as JSON for JavaScript
    import json
    lessons_json = json.dumps({
        cat: [(f, d) for f, d in lessons]
        for cat, lessons in categories.items()
    })

    # Build initial lesson options (for selected category or empty)
    lesson_options = ['<option value="">-- Select lesson --</option>']
    if selected_category and selected_category in categories:
        for filename, display in categories[selected_category]:
            selected = "selected" if filename == selected_lesson else ""
            lesson_options.append(f'<option value="{filename}" {selected}>{display}</option>')
    lesson_select = "\n".join(lesson_options)

    # Build framework version options
    versions = get_available_framework_versions()
    fv_options = []
    for v in versions:
        default_marker = " (default)" if v == FRAMEWORK_VERSION else ""
        fv_options.append(f'<option value="{v}">{v}{default_marker}</option>')
    framework_version_options = "\n".join(fv_options)

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Feldenkrais Lesson Analyzer</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; }}
        h2 {{ color: #555; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
        h3 {{ color: #666; }}
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
            height: 300px;
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
        .result {{ margin-top: 30px; }}
        .loading {{ color: #666; font-style: italic; }}
        table {{ margin-top: 10px; }}
        th {{ text-align: left; }}
        td {{ vertical-align: top; }}
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
                    if (data.text) {{
                        textarea.value = data.text;
                    }} else {{
                        textarea.placeholder = 'Error loading lesson';
                    }}
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
            const form = document.getElementById('analyze-form');

            // Show loading state
            btn.disabled = true;
            btn.textContent = 'Starting analysis...';
            spinner.style.display = 'block';
            resultDiv.innerHTML = '';
            startProgressTimer();

            try {{
                // Submit to API
                const formData = new FormData(form);
                const response = await fetch('/api/analyze', {{
                    method: 'POST',
                    body: formData
                }});

                const data = await response.json();

                if (data.error) {{
                    throw new Error(data.error);
                }}

                // If we got result directly (local mode), display it
                if (data.status === 'complete' && data.result_html) {{
                    resultDiv.innerHTML = '<div class="result">' + data.result_html + '</div>';
                    initCards();
                    showTimings(data.timings);
                    resetForm();
                    return;
                }}

                // Otherwise, poll for result
                if (data.job_id) {{
                    await pollForResult(data.job_id);
                }}

            }} catch (error) {{
                var msg = error.message || String(error);
                if (msg.includes('\\n')) {{
                    resultDiv.innerHTML = '<div class="error"><strong>Error:</strong><pre style="white-space: pre-wrap; word-wrap: break-word; margin-top: 8px; background: #fff5f5; padding: 10px; border-radius: 4px; font-size: 0.85em;">' + msg.replace(/</g, '&lt;') + '</pre></div>';
                }} else {{
                    resultDiv.innerHTML = '<div class="error">Error: ' + msg + '</div>';
                }}
                resetForm();
            }}
        }}

        async function pollForResult(jobId) {{
            const spinner = document.getElementById('loading-spinner');
            const resultDiv = document.getElementById('result');

            while (true) {{
                try {{
                    const response = await fetch('/api/status/' + jobId);
                    const data = await response.json();

                    if (data.status === 'complete') {{
                        resultDiv.innerHTML = '<div class="result">' + data.result_html + '</div>';
                        initCards();
                        resetForm();
                        return;
                    }} else if (data.status === 'error') {{
                        throw new Error(data.error || 'Analysis failed');
                    }}

                    // Still pending, wait and retry
                    await new Promise(resolve => setTimeout(resolve, 2000));

                }} catch (error) {{
                    resultDiv.innerHTML = '<div class="error">Error: ' + error.message + '</div>';
                    resetForm();
                    return;
                }}
            }}
        }}

        function showTimings(timings) {{
            if (!timings) return;
            var loadingText = document.getElementById('loading-text');
            var elapsedEl = document.getElementById('loading-elapsed');
            var spinner = document.getElementById('loading-spinner');
            if (_progressTimer) {{ clearInterval(_progressTimer); _progressTimer = null; }}
            spinner.style.display = 'block';
            spinner.querySelector('.spinner').style.display = 'none';
            if (timings.cached) {{
                loadingText.textContent = 'Loaded from cache';
                elapsedEl.textContent = '';
            }} else {{
                var parts = [];
                if (timings.pass_a != null) parts.push('Pass A: ' + formatSecs(timings.pass_a));
                for (var key in timings) {{
                    if (key.startsWith('pass_b_')) {{
                        var fw = key.replace('pass_b_', '');
                        parts.push('Pass B (' + fw + '): ' + formatSecs(timings[key]));
                    }}
                }}
                loadingText.textContent = 'Analysis complete';
                elapsedEl.textContent = parts.join(' · ') + (timings.total != null ? ' · Total: ' + formatSecs(timings.total) : '');
            }}
        }}

        function formatSecs(s) {{
            if (s >= 60) return Math.floor(s / 60) + 'm ' + Math.round(s % 60) + 's';
            return Math.round(s) + 's';
        }}

        var _progressTimer = null;
        function startProgressTimer() {{
            var startTime = Date.now();
            var loadingText = document.getElementById('loading-text');
            var elapsedEl = document.getElementById('loading-elapsed');
            _progressTimer = setInterval(function() {{
                var elapsed = Math.floor((Date.now() - startTime) / 1000);
                var min = Math.floor(elapsed / 60);
                var sec = elapsed % 60;
                elapsedEl.textContent = (min > 0 ? min + 'm ' : '') + sec + 's';
                if (elapsed < 5) {{
                    loadingText.textContent = 'Starting analysis...';
                }} else if (elapsed < 120) {{
                    loadingText.textContent = 'Running Pass A: decomposing lesson structure...';
                }} else {{
                    loadingText.textContent = 'Running Pass B: applying framework analysis...';
                }}
            }}, 1000);
        }}

        function resetForm() {{
            const btn = document.getElementById('submit-btn');
            const spinner = document.getElementById('loading-spinner');
            btn.disabled = false;
            btn.textContent = 'Analyze Lesson';
            spinner.style.display = 'none';
            if (_progressTimer) {{ clearInterval(_progressTimer); _progressTimer = null; }}
        }}

        // Tab switching for multi-VL analysis results
        function showVLTab(framework) {{
            // Hide all content
            document.querySelectorAll('.vl-content').forEach(el => el.style.display = 'none');
            // Deactivate all tabs
            document.querySelectorAll('.vl-tabs button').forEach(el => {{
                el.style.background = '#e9ecef';
                el.style.color = '#333';
            }});
            // Show selected content
            document.getElementById('content-' + framework).style.display = 'block';
            // Activate selected tab
            document.getElementById('tab-' + framework).style.background = '#007bff';
            document.getElementById('tab-' + framework).style.color = 'white';
            // Re-init cards for the new tab
            setTimeout(initCards, 0);
        }}

        // Debug I/O tab switching
        function showDebugTab(index) {{
            document.querySelectorAll('.debug-entry').forEach(el => el.style.display = 'none');
            document.querySelectorAll('.debug-tabs button').forEach(el => {{
                el.style.background = '#e9ecef';
                el.style.color = '#6c757d';
            }});
            var content = document.getElementById('debug-content-' + index);
            var tab = document.getElementById('debug-tab-' + index);
            if (content) content.style.display = 'block';
            if (tab) {{ tab.style.background = '#007bff'; tab.style.color = 'white'; }}
        }}

        // Analytical table: Card View / Table View switching
        var currentCard = 0;
        var totalCards = 0;

        function getActiveVLContent() {{
            // Find the currently visible VL content div (or fall back to document)
            var visible = document.querySelector('.vl-content[style*="display: block"]');
            if (!visible) {{
                // Single framework (no tabs) - just use the result div
                visible = document.getElementById('result') || document;
            }}
            return visible;
        }}

        function showAnalyticalView(view) {{
            var scope = getActiveVLContent();
            var tableEl = scope.querySelector('#analytical-table-view') || document.getElementById('analytical-table-view');
            var cardEl = scope.querySelector('#analytical-card-view') || document.getElementById('analytical-card-view');
            if (tableEl) tableEl.style.display = view === 'table' ? 'block' : 'none';
            if (cardEl) cardEl.style.display = view === 'cards' ? 'block' : 'none';
            var btnCards = scope.querySelector('#btn-cards') || document.getElementById('btn-cards');
            var btnTable = scope.querySelector('#btn-table') || document.getElementById('btn-table');
            if (btnCards) {{ btnCards.style.background = view === 'cards' ? '#007bff' : '#e9ecef'; btnCards.style.color = view === 'cards' ? 'white' : '#333'; }}
            if (btnTable) {{ btnTable.style.background = view === 'table' ? '#007bff' : '#e9ecef'; btnTable.style.color = view === 'table' ? 'white' : '#333'; }}
        }}

        function showCard(index) {{
            var scope = getActiveVLContent();
            var cards = scope.querySelectorAll('.analytical-card');
            if (cards.length === 0) return;
            cards.forEach(function(c) {{ c.style.display = 'none'; }});
            if (index >= cards.length) index = 0;
            cards[index].style.display = 'block';
            currentCard = index;
            var counter = scope.querySelector('#card-counter') || document.getElementById('card-counter');
            if (counter) counter.textContent = (index + 1) + ' / ' + cards.length;
            var prevBtn = scope.querySelector('#card-prev-btn') || document.getElementById('card-prev-btn');
            var nextBtn = scope.querySelector('#card-next-btn') || document.getElementById('card-next-btn');
            if (prevBtn) prevBtn.disabled = index === 0;
            if (nextBtn) nextBtn.disabled = index === cards.length - 1;
        }}

        function prevCard() {{ if (currentCard > 0) showCard(currentCard - 1); }}
        function nextCard() {{
            var scope = getActiveVLContent();
            var cards = scope.querySelectorAll('.analytical-card');
            if (currentCard < cards.length - 1) showCard(currentCard + 1);
        }}

        function initCards() {{
            var scope = getActiveVLContent();
            var cards = scope.querySelectorAll('.analytical-card');
            totalCards = cards.length;
            if (totalCards > 0) {{
                currentCard = 0;
                showCard(0);
            }}
        }}
    </script>
</head>
<body>
    <h1>Feldenkrais Lesson Analyzer</h1>
    <p>Select a lesson or paste text. The system will classify which VL framework(s) apply and run the appropriate analysis.
       <a href="/pipeline" style="float: right; color: #007bff; text-decoration: none; font-size: 0.9em; margin-left: 15px;">Pipeline Dashboard &rarr;</a>
       <a href="/booklet" style="float: right; color: #007bff; text-decoration: none; font-size: 0.9em; margin-left: 15px;">Booklet &rarr;</a>
       <a href="/explore" style="float: right; color: #007bff; text-decoration: none; font-size: 0.9em; margin-left: 15px;">Explore &rarr;</a>
       <a href="/pass0b" style="float: right; color: #007bff; text-decoration: none; font-size: 0.9em;">Pass 0b Experiment &rarr;</a>
    </p>

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
            // Clear textarea when changing category
            document.getElementById('lesson_text').value = '';
        }}
    </script>

    <form id="analyze-form" method="post" action="/analyze" onsubmit="submitForm(event)">
        <div class="form-group">
            <label for="selected_category">Category:</label>
            <select name="selected_category" id="selected_category" onchange="onCategoryChange(this)">
                {category_select}
            </select>
        </div>

        <div class="form-group">
            <label for="selected_lesson">Lesson:</label>
            <select name="selected_lesson" id="selected_lesson" onchange="onLessonSelect(this)">
                {lesson_select}
            </select>
        </div>

        <div class="or-divider">— OR —</div>

        <div class="form-group">
            <label for="lesson_text">Paste Lesson Text:</label>
            <textarea name="lesson_text" id="lesson_text" placeholder="Paste lesson text here...">{lesson_text}</textarea>
        </div>

        <div style="margin: 10px 0; display: flex; gap: 20px; align-items: center; flex-wrap: wrap;">
            <label style="font-size: 0.9em;">
                Framework:
                <select name="framework_version" style="margin-left: 4px; padding: 2px 6px;">
                    {framework_version_options}
                </select>
            </label>
            <label style="font-size: 0.9em;">
                Pass A provider:
                <select name="pass_a_provider" style="margin-left: 4px; padding: 2px 6px;">
                    <option value="">Default (Gemini)</option>
                    <option value="gemini">Gemini</option>
                    <option value="openai">OpenAI (GPT-5)</option>
                    <option value="anthropic">Anthropic (Claude)</option>
                </select>
            </label>
            <label style="font-size: 0.9em;">
                Pass B provider:
                <select name="pass_b_provider" style="margin-left: 4px; padding: 2px 6px;">
                    <option value="">Default (Gemini)</option>
                    <option value="gemini">Gemini</option>
                    <option value="openai">OpenAI (GPT-5)</option>
                    <option value="anthropic">Anthropic (Claude)</option>
                </select>
            </label>
        </div>

        <button type="submit" id="submit-btn">Analyze Lesson</button>
    </form>

    <div id="loading-spinner" class="loading-spinner">
        <span class="spinner"></span>
        <span id="loading-text">Analyzing lesson with AI...</span>
        <div id="loading-elapsed" style="font-size: 0.85em; color: #999; margin-top: 8px;"></div>
    </div>

    <div id="result">{result}</div>
</body>
</html>
"""


def build_result_html(job: dict | None, job_id: str, schema: dict) -> str:
    """Build HTML for the result page with auto-refresh for pending jobs."""
    if job is None:
        return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Job Not Found - Feldenkrais Lesson Analyzer</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }}
        .error {{ color: red; padding: 20px; background: #fee; border-radius: 8px; }}
    </style>
</head>
<body>
    <h1>Feldenkrais Lesson Analyzer</h1>
    <div class="error">
        <strong>Job not found</strong><br>
        The analysis job with ID {job_id} was not found. It may have expired or the ID is invalid.
    </div>
    <p><a href="/">Start a new analysis</a></p>
</body>
</html>
"""

    status = job.get("status", "unknown")

    if status == "pending":
        # Auto-refresh every 3 seconds
        return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Processing... - Feldenkrais Lesson Analyzer</title>
    <meta http-equiv="refresh" content="3">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; text-align: center; padding-top: 100px; }}
        .spinner {{
            display: inline-block;
            width: 48px;
            height: 48px;
            border: 4px solid #ddd;
            border-top-color: #007bff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        .status {{ margin-top: 20px; color: #666; }}
    </style>
</head>
<body>
    <div class="spinner"></div>
    <div class="status">
        <p><strong>Analyzing lesson...</strong></p>
        <p>This typically takes 30-60 seconds. This page will automatically update when complete.</p>
        <p style="font-size: 12px; color: #999;">Job ID: {job_id}</p>
    </div>
</body>
</html>
"""

    if status == "error":
        error_msg = job.get("error", "Unknown error")
        return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Error - Feldenkrais Lesson Analyzer</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }}
        .error {{ color: red; padding: 20px; background: #fee; border-radius: 8px; }}
    </style>
</head>
<body>
    <h1>Feldenkrais Lesson Analyzer</h1>
    <div class="error">
        <strong>Analysis failed</strong><br>
        {error_msg}
    </div>
    <p><a href="/">Try again</a></p>
</body>
</html>
"""

    # status == "complete"
    router_result = job.get("router_result", {})
    vl_analyses = job.get("vl_analyses", {})

    router_html = render_router_result(router_result)

    # Use multi-VL renderer if we have vl_analyses, otherwise fall back to legacy result
    if vl_analyses:
        analysis_html = render_multi_vl_analyses(vl_analyses, schema)
    elif job.get("result"):
        # Legacy single-result format
        analysis_html = render_analysis(job["result"], schema)
    else:
        analysis_html = ""

    result_html = router_html + analysis_html

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Analysis Complete - Feldenkrais Lesson Analyzer</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; }}
        h2 {{ color: #555; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
        h3 {{ color: #666; }}
        table {{ margin-top: 10px; }}
        th {{ text-align: left; }}
        td {{ vertical-align: top; }}
        .nav {{ margin-bottom: 20px; }}
        .nav a {{ color: #007bff; text-decoration: none; }}
        .nav a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <div class="nav">
        <a href="/">&larr; Analyze another lesson</a>
    </div>
    {result_html}
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home():
    """Show the input form."""
    return build_html()


@app.get("/api/lesson/{category}/{filename}")
async def get_lesson_text(category: str, filename: str):
    """Return lesson text for populating the textarea."""
    text = load_lesson(category, filename)
    if text:
        return JSONResponse({"text": text})
    return JSONResponse({"error": "Lesson not found"}, status_code=404)


@app.get("/analyze")
async def analyze_redirect():
    """Redirect GET requests to home."""
    return RedirectResponse(url="/", status_code=303)


ANALYSIS_TIMEOUT = 900  # 15 minutes


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


@app.post("/api/analyze")
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


@app.get("/api/status/{job_id}")
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
@app.post("/analyze", response_class=HTMLResponse)
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


def _build_book_pipeline_section() -> str:
    """Build the Book Pipeline status section for the dashboard."""
    try:
        from pipeline_graph import build_book_pipeline
        from pipeline import PipelineStateStore, PipelineExecutor, NodeStatus
    except ImportError:
        from .pipeline_graph import build_book_pipeline
        from .pipeline import PipelineStateStore, PipelineExecutor, NodeStatus

    graph = build_book_pipeline(_db)
    store = PipelineStateStore(_db)
    store.initialize()
    executor = PipelineExecutor(graph, store)
    states = executor.compute_staleness()
    order = graph.topological_order()
    enabled_flags = store.load_enabled_flags()

    stale_count = sum(
        1 for s in states.values()
        if s.status in (NodeStatus.STALE, NodeStatus.PENDING, NodeStatus.ERROR)
    )
    fresh_count = sum(1 for s in states.values() if s.status == NodeStatus.FRESH)
    total = len(states)

    status_colors = {
        "fresh": "#28a745",
        "stale": "#ffc107",
        "pending": "#6c757d",
        "error": "#dc3545",
        "running": "#17a2b8",
    }

    # Show transforms first, then sources at the bottom
    transforms = [n for n in order if graph.nodes[n].kind.value == "transform"]
    sources = [n for n in order if graph.nodes[n].kind.value == "source"]
    display_order = transforms + sources

    rows = []
    for name in display_order:
        state = states.get(name)
        if not state:
            continue
        spec = graph.nodes[name]
        status_val = state.status.value
        color = status_colors.get(status_val, "#333")
        is_transform = spec.kind.value == "transform"

        display_name = f"<strong>{name}</strong>" if is_transform else f'<span style="color: #888;">{name}</span>'
        duration = f"{state.duration_seconds}s" if state.duration_seconds else "&mdash;"
        status_label = status_val.upper() if status_val != "fresh" else "fresh"

        error_row = ""
        if state.status == NodeStatus.ERROR and state.error_message:
            error_msg = state.error_message[:80]
            error_row = f'<tr><td></td><td colspan="4" style="color: #dc3545; font-size: 0.8em; padding-top: 0;">{error_msg}</td></tr>'

        # Convert UTC to Eastern
        if state.last_run_at:
            from datetime import timezone, timedelta
            utc_str = str(state.last_run_at)
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                eastern = timezone(timedelta(hours=-5))
                dt_est = dt.astimezone(eastern)
                last_run = dt_est.strftime("%Y-%m-%d %H:%M")
                sort_time = dt_est.isoformat()[:19]
            except Exception:
                last_run = utc_str[:16].replace("T", " ")
                sort_time = utc_str[:19]
        else:
            last_run = "&mdash;"
            sort_time = ""
        sort_dur = str(state.duration_seconds) if state.duration_seconds else ""
        kind = "transform" if is_transform else "source"
        node_enabled = enabled_flags.get(name, True)
        row_opacity = "1" if node_enabled else "0.4"
        toggle_label = "on" if node_enabled else "off"
        toggle_color = "#28a745" if node_enabled else "#999"

        rows.append(f"""
            <tr data-kind="{kind}" data-time="{sort_time}" data-dur="{sort_dur}" data-status="{status_val}" style="opacity: {row_opacity};">
                <td>{display_name}</td>
                <td><span style="color: {color}; font-weight: {'bold' if status_val != 'fresh' else 'normal'};">{status_label}</span></td>
                <td>{last_run}</td>
                <td>{duration}</td>
                <td><button class="toggle-btn" data-node="{name}" style="background: none; border: 1px solid {toggle_color}; color: {toggle_color}; border-radius: 3px; padding: 1px 8px; cursor: pointer; font-size: 0.8em;">{toggle_label}</button></td>
                <td>{"<button class='rebuild-btn' data-node='" + name + "' style='background: none; border: 1px solid #17a2b8; color: #17a2b8; border-radius: 3px; padding: 1px 8px; cursor: pointer; font-size: 0.8em;'>invalidate</button>" if is_transform else ""}</td>
            </tr>
            {error_row}
        """)

    rows_html = "\n".join(rows)

    return f"""
    <div class="cards" style="margin-top: 15px;">
        <div class="card">
            <div class="value">{total}</div>
            <div class="label">Total Nodes</div>
        </div>
        <div class="card">
            <div class="value" style="color: #28a745;">{fresh_count}</div>
            <div class="label">Fresh</div>
        </div>
        <div class="card">
            <div class="value" style="color: {('#ffc107' if stale_count else '#333')};">{stale_count}</div>
            <div class="label">Stale / Pending</div>
        </div>
    </div>
    <table class="compact" id="pipeline-table">
        <thead>
            <tr>
                <th class="sortable" data-col="node">Node</th>
                <th class="sortable" data-col="status">Status</th>
                <th class="sortable active desc" data-col="time">Last Run &#x25BC;</th>
                <th class="sortable" data-col="dur">Duration</th>
                <th>Enabled</th>
                <th></th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    <p style="font-size: 0.85em; color: #999; margin-top: 10px;">
        Rebuild stale nodes: <code>uv run python -m src.kb.pipeline run</code>
    </p>
    """


def build_pipeline_html() -> str:
    """Build the pipeline dashboard HTML page."""
    if not _db:
        return """
<!DOCTYPE html><html><head><title>Pipeline Dashboard</title>
<style>body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }</style>
</head><body><h1>Pipeline Dashboard</h1><p>Database not configured. Set DATABASE_URL to enable.</p>
<p><a href="/">&larr; Back to Analyzer</a></p></body></html>"""

    try:
        summary = _db.get_pipeline_summary()
        events = _db.get_pipeline_log(limit=50)
        documents = _db.get_document_pipeline_view(limit=50)
    except Exception as e:
        return f"""
<!DOCTYPE html><html><head><title>Pipeline Dashboard</title>
<style>body {{ font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }}</style>
</head><body><h1>Pipeline Dashboard</h1><div style="color: red;">Error: {e}</div>
<p><a href="/">&larr; Back to Analyzer</a></p></body></html>"""

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

        if has_tx:
            status_badge = '<span style="padding: 2px 8px; border-radius: 10px; font-size: 0.8em; background: #d4edda; color: #155724;">Transcribed</span>'
        elif latest_job == "pending":
            status_badge = '<span style="padding: 2px 8px; border-radius: 10px; font-size: 0.8em; background: #fff3cd; color: #856404;">Pending</span>'
        elif latest_job == "failed":
            status_badge = f'<span style="padding: 2px 8px; border-radius: 10px; font-size: 0.8em; background: #f8d7da; color: #721c24;" title="{latest_error or ""}">Failed</span>'
        else:
            status_badge = '<span style="padding: 2px 8px; border-radius: 10px; font-size: 0.8em; background: #e2e3e5; color: #383d41;">New</span>'

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

    # Book pipeline section
    try:
        book_pipeline_html = _build_book_pipeline_section()
    except Exception as e:
        book_pipeline_html = f'<p style="color: #999;">Book pipeline not available: {e}</p>'

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
    <div class="nav"><a href="/">&larr; Back to Analyzer</a></div>
    <h1>Pipeline Dashboard</h1>

    <div class="tabs">
        <button class="tab active" data-tab="analysis">Analysis</button>
        <button class="tab" data-tab="ingestion">Document Ingestion</button>
    </div>

    <div id="tab-analysis" class="tab-content active">
        {book_pipeline_html}
    </div>

    <div id="tab-ingestion" class="tab-content">
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
                <div class="value">{pending_docs}</div>
                <div class="label">Pending Docs</div>
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

        // Event delegation for toggle and rebuild buttons (works after DOM updates)
        document.getElementById('tab-analysis').addEventListener('click', function(e) {{
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
        }});

        // Auto-refresh: poll pipeline status every 5 seconds
        (function() {{
            var container = document.getElementById('tab-analysis');
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
    </script>
</body>
</html>
"""


@app.get("/pipeline", response_class=HTMLResponse)
async def pipeline_dashboard():
    """Pipeline monitoring dashboard — ingestion + book generation."""
    return build_pipeline_html()


@app.get("/review", response_class=HTMLResponse)
async def review_page():
    """Display the latest book review from the pipeline."""
    if not _db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    data = _db.get_cache("pipeline:review")
    if not data or "report" not in data:
        return HTMLResponse("<p>No review available yet. Run the pipeline to generate one.</p>")
    import markdown
    report_html = markdown.markdown(data["report"], extensions=["tables", "fenced_code"])
    return f"""<!DOCTYPE html><html><head><title>Book Review</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.7; }}
h1, h2, h3 {{ color: #333; }}
h2 {{ border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
.nav {{ margin-bottom: 20px; }}
.nav a {{ color: #007bff; text-decoration: none; }}
.nav a:hover {{ text-decoration: underline; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
th {{ background: #f0f0f0; }}
</style></head><body>
<div class="nav"><a href="/">&larr; Back to Analyzer</a> | <a href="/pipeline">Pipeline</a></div>
{report_html}
</body></html>"""


# --- Pass 0b Experiment ---

@app.get("/pass0b", response_class=HTMLResponse)
async def pass0b_page():
    """Pass 0b experiment page."""
    return build_pass0b_html()


@app.post("/api/pass0b")
async def api_pass0b(
    lesson_text: str = Form(""),
    selected_category: str = Form(""),
    selected_lesson: str = Form(""),
    provider: str = Form(""),
):
    """Run Pass 0 + Pass 0b and return results."""
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
        result_html = render_pass0b_result(result)
        debug_html = render_debug_log(debug_log) if debug_log else ""
        return JSONResponse({
            "status": "complete",
            "result_html": result_html,
            "debug_html": debug_html,
            "elapsed": result.get("elapsed"),
            "cached": result.get("cached", False),
            "segment_count": len(result.get("pass_0", {}).get("segments", [])),
            "enriched_count": len(result.get("enriched_segments", [])),
        })
    except asyncio.TimeoutError:
        return JSONResponse({"error": "Analysis timed out."}, status_code=504)
    except Exception as e:
        debug_html = render_debug_log(debug_log) if debug_log else ""
        return JSONResponse({
            "error": str(e),
            "debug_html": debug_html,
        }, status_code=500)


def render_pass0b_result(result: dict) -> str:
    """Render Pass 0 + Pass 0b results as HTML."""
    pass_0 = result.get("pass_0", {})
    segments = pass_0.get("segments", [])
    enriched = result.get("enriched_segments", [])

    # Build lookup for enriched segments by ID
    enriched_lookup = {e["id"]: e for e in enriched}

    html = []
    html.append(f'<p style="color: #666; font-size: 0.9em;">Pass 0: {len(segments)} segments | '
                f'Pass 0b: {len(enriched)} enriched</p>')

    for seg in segments:
        sid = seg["id"]
        e = enriched_lookup.get(sid, {})

        html.append(f'<div style="margin-bottom: 16px; border: 1px solid #ddd; border-radius: 8px; overflow: hidden;">')

        # Header with segment ID
        html.append(f'<div style="background: #f0f0f0; padding: 8px 12px; font-weight: bold; font-size: 0.9em; color: #555;">'
                    f'Segment #{sid}</div>')

        html.append('<div style="padding: 12px;">')

        # Original text
        html.append(f'<div style="margin-bottom: 10px;">'
                    f'<div style="font-size: 0.8em; color: #999; text-transform: uppercase; margin-bottom: 2px;">Original</div>'
                    f'<div style="padding: 8px; background: #fafafa; border-left: 3px solid #ccc; color: #333;">{seg["text"]}</div>'
                    f'</div>')

        if e:
            # Three-tier extraction
            if e.get("mechanical_directive"):
                html.append(f'<div style="margin-bottom: 10px;">'
                            f'<div style="font-size: 0.8em; color: #999; text-transform: uppercase; margin-bottom: 2px;">Mechanical Directive</div>'
                            f'<div style="padding: 8px; background: #f0f0f0; border-left: 3px solid #6c757d; color: #333;">{e["mechanical_directive"]}</div>'
                            f'</div>')

            if e.get("quality_constraint"):
                html.append(f'<div style="margin-bottom: 10px;">'
                            f'<div style="font-size: 0.8em; color: #999; text-transform: uppercase; margin-bottom: 2px;">Quality Constraint</div>'
                            f'<div style="padding: 8px; background: #f0fff0; border-left: 3px solid #28a745; color: #333;">{e["quality_constraint"]}</div>'
                            f'</div>')

            if e.get("sensory_inquiry"):
                html.append(f'<div style="margin-bottom: 10px;">'
                            f'<div style="font-size: 0.8em; color: #999; text-transform: uppercase; margin-bottom: 2px;">Sensory Inquiry</div>'
                            f'<div style="padding: 8px; background: #fff8f0; border-left: 3px solid #fd7e14; color: #333;">{e["sensory_inquiry"]}</div>'
                            f'</div>')

            # Clarified rewrite
            if e.get("clarified"):
                html.append(f'<div style="margin-bottom: 10px;">'
                            f'<div style="font-size: 0.8em; color: #999; text-transform: uppercase; margin-bottom: 2px;">Clarified</div>'
                            f'<div style="padding: 8px; background: #f0f8ff; border-left: 3px solid #007bff; color: #333;">{e["clarified"]}</div>'
                            f'</div>')

            # Global anchor (optional)
            if e.get("global_anchor"):
                html.append(f'<div style="margin-bottom: 10px;">'
                            f'<div style="font-size: 0.8em; color: #999; text-transform: uppercase; margin-bottom: 2px;">Global Anchor</div>'
                            f'<div style="padding: 8px; background: #f8f0ff; border-left: 3px solid #6f42c1; color: #333;">{e["global_anchor"]}</div>'
                            f'</div>')

            # Intent
            if e.get("intent"):
                html.append(f'<div style="margin-bottom: 10px;">'
                            f'<div style="font-size: 0.8em; color: #999; text-transform: uppercase; margin-bottom: 2px;">Intent</div>'
                            f'<div style="padding: 8px; background: #e8f4fd; border-left: 3px solid #17a2b8; color: #333;">{e["intent"]}</div>'
                            f'</div>')
        else:
            html.append('<div style="color: #999; font-style: italic;">No enrichment for this segment</div>')

        html.append('</div></div>')

    return "\n".join(html)


def build_pass0b_html() -> str:
    """Build the Pass 0b experiment page HTML."""
    categories, display_names = get_available_lessons()

    category_options = ['<option value="">-- Select category --</option>']
    for cat_name in categories.keys():
        cat_display = display_names.get(cat_name, cat_name.replace("_", " ").title())
        category_options.append(f'<option value="{cat_name}">{cat_display}</option>')
    category_select = "\n".join(category_options)

    lessons_json = json.dumps({
        cat: [(f, d) for f, d in lessons]
        for cat, lessons in categories.items()
    })

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Pass 0b Experiment - Segment Enrichment</title>
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
            const form = document.getElementById('pass0b-form');

            btn.disabled = true;
            btn.textContent = 'Running Pass 0 + 0b...';
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
                const response = await fetch('/api/pass0b', {{
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
                    stats += ' | ' + (data.segment_count || '?') + ' segments, ' + (data.enriched_count || '?') + ' enriched';
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
            document.getElementById('submit-btn').textContent = 'Run Pass 0b';
            document.getElementById('loading-spinner').style.display = 'none';
        }}
    </script>
</head>
<body>
    <h1>Pass 0b: Segment Enrichment</h1>
    <p>Experiment page. Runs Pass 0 (rule-based segmentation) then Pass 0b (LLM enrichment of each segment).
       <a href="/" style="float: right; color: #007bff; text-decoration: none; font-size: 0.9em;">&larr; Main Analyzer</a>
    </p>

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
                    <option value="gemini">Gemini</option>
                    <option value="openai">OpenAI (GPT-5)</option>
                    <option value="anthropic">Anthropic (Claude)</option>
                </select>
            </label>
        </div>

        <button type="submit" id="submit-btn">Run Pass 0b</button>
    </form>

    <div id="loading-spinner" class="loading-spinner">
        <span class="spinner"></span>
        <span>Running segment enrichment...</span>
        <div id="loading-elapsed" style="font-size: 0.85em; color: #999; margin-top: 8px;"></div>
    </div>

    <div id="result" style="margin-top: 20px;"></div>
    <div id="debug" style="margin-top: 20px;"></div>
</body>
</html>
"""


# --- Exploration Agent ---

def load_explore_prompt(round_type: str) -> str:
    """Load exploration prompt (init or round)."""
    filename = "explore_init.md" if round_type == "init" else "explore_round.md"
    path = DATA_DIR / filename
    return path.read_text() if path.exists() else ""


def load_explore_schema() -> dict:
    """Load the exploration output schema."""
    path = DATA_DIR / "explore.schema.json"
    return json.loads(path.read_text()) if path.exists() else {}


def explore_session_key(lesson_cache_key: str) -> str:
    """Get the cache key for an exploration session."""
    return f"{lesson_cache_key}:explore"


def load_explore_session(session_key: str) -> dict:
    """Load exploration session from database cache."""
    if _db:
        cached = _db.get_cache(session_key)
        if cached:
            return cached
    return {"rounds": []}


def save_explore_session(session_key: str, session: dict) -> None:
    """Save exploration session to database cache."""
    if _db:
        _db.set_cache(session_key, session)


def build_explore_context(analysis_result: dict) -> str:
    """Format analysis result as text context for the exploration LLM prompt."""
    parts = []
    pass_a = analysis_result.get("pass_a_result", {})

    # Lesson title
    title = pass_a.get("lesson_title", "Untitled")
    parts.append(f"# Lesson: {title}\n")

    # Classifications
    classifications = pass_a.get("classifications", {})
    primary = pass_a.get("primary_framework", "Unknown")
    secondary = pass_a.get("secondary_frameworks", [])
    parts.append(f"Primary framework: {primary}")
    if secondary:
        parts.append(f"Secondary: {', '.join(secondary)}")
    if classifications:
        scores = ", ".join(f"{k}={v:.2f}" for k, v in classifications.items() if v > 0)
        parts.append(f"Scores: {scores}")
    parts.append("")

    # Home position
    home_pos = pass_a.get("home_position")
    if home_pos:
        parts.append(f"Home position: {home_pos}\n")

    # Groups and steps
    groups = pass_a.get("groups", [])
    steps = pass_a.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps if "step_number" in s}

    if groups:
        for g in groups:
            gnum = g.get("group_number", "?")
            label = g.get("label", "")
            desc = g.get("description", "")
            parts.append(f"## Group {gnum}: {label}")
            if desc:
                parts.append(f"  {desc}")
            for sn in g.get("steps", []):
                step = step_lookup.get(sn, {})
                tags = ", ".join(step.get("tags", []))
                instruction = step.get("instruction", "")
                note = f" ({step['note']})" if step.get("note") else ""
                home = " [HOME]" if step.get("is_home") else ""
                parts.append(f"  Step {sn}: [{tags}] {instruction}{home}{note}")
            parts.append("")
    elif steps:
        for step in steps:
            sn = step.get("step_number", "?")
            tags = ", ".join(step.get("tags", []))
            instruction = step.get("instruction", "")
            parts.append(f"  Step {sn}: [{tags}] {instruction}")
        parts.append("")

    # Rationale
    if pass_a.get("rationale"):
        parts.append(f"Rationale: {pass_a['rationale']}\n")

    # Pass B analyses
    vl_analyses = analysis_result.get("vl_analyses", {})
    for framework, vl_data in vl_analyses.items():
        if vl_data.get("status") != "complete":
            continue
        result = vl_data.get("result", {})
        parts.append(f"## Pass B: {framework}")

        # Somatic narrative
        narrative = result.get("somatic_narrative")
        if narrative:
            parts.append(f"Somatic narrative: {narrative}")

        # Negotiation framework
        neg = result.get("negotiation_framework")
        if neg:
            parts.append(f"Negotiation framework: {json.dumps(neg)}")

        # Table rows (if present)
        table = result.get("table")
        if table:
            parts.append(f"Analysis table: {len(table)} rows")
            for row in table[:5]:  # Just first few to keep context manageable
                parts.append(f"  - {json.dumps(row)}")
            if len(table) > 5:
                parts.append(f"  ... and {len(table) - 5} more rows")
        parts.append("")

    return "\n".join(parts)


def run_explore_round(
    analysis_result: dict,
    session: dict,
    focus: dict | None = None,
    hint: str | None = None,
    provider_key: str | None = None,
) -> dict:
    """Run one exploration round.

    Args:
        analysis_result: The cached analysis (pass_a_result + vl_analyses)
        session: Current exploration session (with previous rounds)
        focus: For round N, the selected focus area {group, step, label}
        hint: Optional user hint/question
        provider_key: LLM provider override

    Returns:
        Round result dict with annotations, suggestions, meta_observation
    """
    import time

    rounds = session.get("rounds", [])
    round_num = len(rounds)

    # Build context from analysis
    context = build_explore_context(analysis_result)

    if round_num == 0:
        # Round 0: survey and suggest
        prompt_template = load_explore_prompt("init")
        prompt = prompt_template + "\n\n---\n\n## Analysis to explore:\n\n" + context
    else:
        # Round N: focused exploration
        prompt_template = load_explore_prompt("round")

        # Build focus description
        focus_desc = ""
        if focus:
            focus_desc = f"Focus on Group {focus.get('group', '?')}"
            if focus.get("step"):
                focus_desc += f", Step {focus['step']}"
            if focus.get("label"):
                focus_desc += f" — {focus['label']}"
        if hint:
            focus_desc += f"\nUser hint: {hint}"

        # Build previous annotations summary
        prev_annotations = []
        for r in rounds:
            for ann in r.get("annotations", []):
                prev_annotations.append(
                    f"[Round {r['round']}] Group {ann.get('anchor_group', '?')}"
                    f"{' Step ' + str(ann['anchor_step']) if ann.get('anchor_step') else ''}"
                    f" ({ann.get('category', '?')}): {ann.get('text', '')}"
                )

        prev_section = ""
        if prev_annotations:
            prev_section = "\n\n## Previous annotations (do not repeat these):\n\n" + "\n".join(prev_annotations)

        prompt = (
            prompt_template
            + "\n\n---\n\n## Analysis:\n\n" + context
            + "\n\n---\n\n## Focus area:\n\n" + focus_desc
            + prev_section
        )

    system = "You are an expert Feldenkrais practitioner exploring a lesson analysis. Be specific, insightful, and non-obvious."
    schema = load_explore_schema()
    llm = get_llm(provider_key)

    t0 = time.time()
    result = llm.complete_structured_json(
        prompt=prompt,
        schema=schema,
        system=system,
    )
    elapsed = round(time.time() - t0, 1)

    # Build round entry
    round_entry = {
        "round": round_num,
        "focus": focus,
        "hint": hint,
        "annotations": result.get("annotations", []),
        "suggestions": result.get("suggestions", []),
        "meta_observation": result.get("meta_observation", ""),
        "elapsed": elapsed,
    }

    return round_entry


ANNOTATION_COLORS = {
    "pedagogical_gap": ("#f0ad4e", "#fff3cd"),
    "hidden_connection": ("#6f42c1", "#f3e8ff"),
    "question": ("#007bff", "#e8f0fe"),
    "reinterpretation": ("#28a745", "#e8f5e9"),
    "missing_voice": ("#fd7e14", "#fff3e0"),
    "contradiction": ("#dc3545", "#fde8e8"),
}


def render_explore_analysis_with_annotations(analysis_result: dict, session: dict) -> str:
    """Render Pass A groups/steps with annotation badges overlaid."""
    pass_a = analysis_result.get("pass_a_result", {})
    groups = pass_a.get("groups", [])
    steps = pass_a.get("steps", [])
    step_lookup = {s["step_number"]: s for s in steps if "step_number" in s}

    # Collect all annotations by group and step
    annotations_by_group = {}
    annotations_by_step = {}
    for r in session.get("rounds", []):
        for ann in r.get("annotations", []):
            g = ann.get("anchor_group")
            s = ann.get("anchor_step")
            if g is not None:
                annotations_by_group.setdefault(g, []).append(ann)
            if s is not None:
                annotations_by_step.setdefault(s, []).append(ann)

    def _render_annotation_badges(annotations):
        badges = []
        for ann in annotations:
            cat = ann.get("category", "unknown")
            color, bg = ANNOTATION_COLORS.get(cat, ("#999", "#f0f0f0"))
            conf = ann.get("confidence", 0)
            text = ann.get("text", "")
            # Escape HTML in tooltip
            tooltip = text.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            badges.append(
                f'<span style="display: inline-block; font-size: 0.75em; padding: 2px 8px; '
                f'margin: 2px 3px; border-radius: 12px; background: {bg}; color: {color}; '
                f'border: 1px solid {color}; cursor: help;" '
                f'title="{tooltip}">{cat} ({conf:.1f})</span>'
            )
        return "".join(badges)

    def _render_annotation_detail(annotations):
        if not annotations:
            return ""
        parts = ['<div style="margin: 6px 0 6px 20px;">']
        for ann in annotations:
            cat = ann.get("category", "unknown")
            color, bg = ANNOTATION_COLORS.get(cat, ("#999", "#f0f0f0"))
            conf = ann.get("confidence", 0)
            text = ann.get("text", "")
            parts.append(
                f'<div style="margin: 4px 0; padding: 8px 12px; background: {bg}; '
                f'border-left: 3px solid {color}; border-radius: 4px; font-size: 0.9em;">'
                f'<strong style="color: {color};">{cat}</strong> '
                f'<span style="color: #999; font-size: 0.85em;">({conf:.1f})</span><br>'
                f'{text}</div>'
            )
        parts.append('</div>')
        return "\n".join(parts)

    html = []

    # Lesson title
    title = pass_a.get("lesson_title")
    if title:
        html.append(f"<h3>{title}</h3>")

    # Home position
    home_pos = pass_a.get("home_position")
    if home_pos:
        html.append(f'<p style="padding: 6px 10px; background: #e8f4fd; border-left: 3px solid #17a2b8; border-radius: 4px; font-size: 0.9em;"><strong>Home:</strong> {home_pos}</p>')

    if groups:
        grouped_step_nums = set()
        for g in groups:
            grouped_step_nums.update(g.get("steps", []))

        for g in groups:
            gnum = g.get("group_number", 0)
            label = g.get("label", f"Group {gnum}")
            desc = g.get("description", "")
            group_anns = annotations_by_group.get(gnum, [])
            # Only group-level annotations (no specific step)
            group_only_anns = [a for a in group_anns if not a.get("anchor_step")]

            html.append(f'<details open style="margin-bottom: 10px;" id="group-{gnum}">')
            html.append(
                f'<summary style="cursor: pointer; font-weight: bold; padding: 6px 0;">'
                f'{gnum}. {label}'
                f'{_render_annotation_badges(group_only_anns)}'
                f'</summary>'
            )
            if desc:
                html.append(f'<p style="margin: 2px 0 6px 20px; color: #666; font-size: 0.9em;">{desc}</p>')

            # Group-level annotation details
            if group_only_anns:
                html.append(_render_annotation_detail(group_only_anns))

            html.append('<ol style="margin-left: 20px;">')
            for sn in g.get("steps", []):
                step = step_lookup.get(sn)
                if not step:
                    continue
                tags = ", ".join(step.get("tags", []))
                instruction = step.get("instruction", "")
                home = ' <span style="font-size: 0.8em; padding: 1px 6px; background: #e8f4fd; color: #17a2b8; border-radius: 3px;">home</span>' if step.get("is_home") else ""
                step_anns = annotations_by_step.get(sn, [])

                html.append(f'<li id="step-{sn}"><strong>[{tags}]</strong> {instruction}{home}')
                if step_anns:
                    html.append(_render_annotation_badges(step_anns))
                    html.append(_render_annotation_detail(step_anns))
                html.append('</li>')
            html.append('</ol>')
            html.append('</details>')
    elif steps:
        html.append('<ol>')
        for step in steps:
            sn = step.get("step_number", 0)
            tags = ", ".join(step.get("tags", []))
            instruction = step.get("instruction", "")
            step_anns = annotations_by_step.get(sn, [])
            html.append(f'<li><strong>[{tags}]</strong> {instruction}')
            if step_anns:
                html.append(_render_annotation_badges(step_anns))
                html.append(_render_annotation_detail(step_anns))
            html.append('</li>')
        html.append('</ol>')

    return "\n".join(html)


def render_explore_panel(session: dict) -> str:
    """Render the exploration panel (suggestions, round history)."""
    rounds = session.get("rounds", [])
    html = []

    if not rounds:
        html.append('<p style="color: #666;">Load a lesson to begin exploration.</p>')
        return "\n".join(html)

    latest = rounds[-1]

    # Meta observation from latest round
    meta = latest.get("meta_observation", "")
    if meta:
        html.append(f'<div style="padding: 10px; background: #f8f9fa; border-radius: 6px; margin-bottom: 12px; font-style: italic; color: #555;">{meta}</div>')

    # Current suggestions
    suggestions = latest.get("suggestions", [])
    if suggestions:
        html.append('<h4 style="margin-bottom: 8px;">Suggested explorations:</h4>')
        for i, s in enumerate(suggestions):
            group = s.get("group", "?")
            step = s.get("step")
            label = s.get("label", "")
            why = s.get("why", "")
            step_str = f", Step {step}" if step else ""
            html.append(
                f'<div class="suggestion-card" data-group="{group}" data-step="{step or ""}" '
                f'data-label="{label}" '
                f'onclick="selectSuggestion(this)" '
                f'style="padding: 10px; margin-bottom: 8px; border: 1px solid #ddd; border-radius: 6px; '
                f'cursor: pointer; transition: border-color 0.2s;"'
                f'onmouseover="this.style.borderColor=\'#007bff\'" '
                f'onmouseout="if(!this.classList.contains(\'selected\'))this.style.borderColor=\'#ddd\'">'
                f'<strong>Group {group}{step_str}:</strong> {label}'
                f'<div style="font-size: 0.85em; color: #666; margin-top: 4px;">{why}</div>'
                f'</div>'
            )

    # Round history
    if len(rounds) > 1 or (len(rounds) == 1 and rounds[0].get("annotations")):
        html.append('<h4 style="margin-top: 16px; margin-bottom: 8px;">Round history:</h4>')
        for r in reversed(rounds):
            rnum = r.get("round", 0)
            focus = r.get("focus")
            annotations = r.get("annotations", [])
            elapsed = r.get("elapsed", "?")

            focus_str = "Survey" if focus is None else f"Group {focus.get('group', '?')}"
            if focus and focus.get("label"):
                focus_str += f" — {focus['label']}"

            html.append(f'<details style="margin-bottom: 6px;">')
            html.append(
                f'<summary style="cursor: pointer; font-size: 0.9em; color: #555;">'
                f'Round {rnum}: {focus_str} '
                f'<span style="color: #999;">({len(annotations)} annotations, {elapsed}s)</span>'
                f'</summary>'
            )
            if r.get("meta_observation"):
                html.append(f'<p style="font-style: italic; color: #666; font-size: 0.85em; margin: 4px 0 4px 16px;">{r["meta_observation"]}</p>')
            for ann in annotations:
                cat = ann.get("category", "?")
                color, bg = ANNOTATION_COLORS.get(cat, ("#999", "#f0f0f0"))
                g = ann.get("anchor_group", "?")
                s = ann.get("anchor_step")
                anchor = f"G{g}" + (f"/S{s}" if s else "")
                html.append(
                    f'<div style="margin: 3px 0 3px 16px; padding: 6px 10px; background: {bg}; '
                    f'border-left: 3px solid {color}; border-radius: 4px; font-size: 0.85em;">'
                    f'<strong style="color: {color};">[{anchor}] {cat}</strong> '
                    f'({ann.get("confidence", 0):.1f})<br>{ann.get("text", "")}</div>'
                )
            html.append('</details>')

    return "\n".join(html)


def build_explore_html() -> str:
    """Build the /explore page HTML."""
    categories, display_names = get_available_lessons()

    category_options = ['<option value="">-- Select category --</option>']
    for cat_name in categories.keys():
        cat_display = display_names.get(cat_name, cat_name.replace("_", " ").title())
        category_options.append(f'<option value="{cat_name}">{cat_display}</option>')
    category_select = "\n".join(category_options)

    lessons_json = json.dumps({
        cat: [(f, d) for f, d in lessons]
        for cat, lessons in categories.items()
    })

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Explore - Feldenkrais Lesson Analysis</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #333; margin-bottom: 5px; }}
        .top-bar {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
            margin-bottom: 15px;
            padding-bottom: 15px;
            border-bottom: 1px solid #eee;
        }}
        .top-bar select, .top-bar button {{
            padding: 8px 12px;
            font-size: 14px;
            border: 1px solid #ccc;
            border-radius: 4px;
        }}
        .top-bar button {{
            background: #007bff;
            color: white;
            cursor: pointer;
            border: none;
        }}
        .top-bar button:hover {{ background: #0056b3; }}
        .top-bar button:disabled {{ background: #ccc; cursor: not-allowed; }}
        .columns {{
            display: flex;
            gap: 20px;
        }}
        .col-left {{
            flex: 6;
            min-width: 0;
        }}
        .col-right {{
            flex: 4;
            min-width: 300px;
        }}
        .panel {{
            background: #f8f9fa;
            border-radius: 8px;
            padding: 15px;
            position: sticky;
            top: 20px;
            max-height: calc(100vh - 60px);
            overflow-y: auto;
        }}
        .hint-area {{
            margin-top: 12px;
        }}
        .hint-area textarea {{
            width: 100%;
            height: 60px;
            font-size: 13px;
            padding: 8px;
            border: 1px solid #ccc;
            border-radius: 4px;
            resize: vertical;
        }}
        .explore-btn {{
            display: block;
            width: 100%;
            padding: 10px;
            margin-top: 8px;
            background: #28a745;
            color: white;
            border: none;
            border-radius: 4px;
            font-size: 15px;
            cursor: pointer;
        }}
        .explore-btn:hover {{ background: #218838; }}
        .explore-btn:disabled {{ background: #ccc; cursor: not-allowed; }}
        .suggestion-card.selected {{
            border-color: #007bff !important;
            background: #e8f0fe;
        }}
        .loading {{
            text-align: center;
            padding: 20px;
            color: #666;
        }}
        .loading .spinner {{
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid #ddd;
            border-top-color: #007bff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            vertical-align: middle;
            margin-right: 8px;
        }}
        @keyframes spin {{
            to {{ transform: rotate(360deg); }}
        }}
        .error {{ color: red; padding: 10px; background: #fee; border-radius: 4px; }}
    </style>
    <script>
        const lessonsData = {lessons_json};
        let sessionKey = null;
        let selectedFocus = null;

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
        }}

        function selectSuggestion(card) {{
            document.querySelectorAll('.suggestion-card').forEach(c => {{
                c.classList.remove('selected');
                c.style.borderColor = '#ddd';
            }});
            card.classList.add('selected');
            card.style.borderColor = '#007bff';
            selectedFocus = {{
                group: parseInt(card.dataset.group),
                step: card.dataset.step ? parseInt(card.dataset.step) : null,
                label: card.dataset.label
            }};
        }}

        async function loadLesson() {{
            const category = document.getElementById('selected_category').value;
            const lesson = document.getElementById('selected_lesson').value;
            if (!category || !lesson) return;

            const loadBtn = document.getElementById('load-btn');
            const analysisDiv = document.getElementById('analysis');
            const panelDiv = document.getElementById('panel');

            loadBtn.disabled = true;
            loadBtn.textContent = 'Loading...';
            analysisDiv.innerHTML = '<div class="loading"><span class="spinner"></span> Loading analysis and running initial survey...</div>';
            panelDiv.innerHTML = '';

            try {{
                const formData = new FormData();
                formData.append('selected_category', category);
                formData.append('selected_lesson', lesson);

                const response = await fetch('/api/explore/load', {{
                    method: 'POST',
                    body: formData
                }});
                const data = await response.json();

                if (data.error) {{
                    analysisDiv.innerHTML = '<div class="error">' + data.error + '</div>';
                    loadBtn.disabled = false;
                    loadBtn.textContent = 'Load & Explore';
                    return;
                }}

                sessionKey = data.session_key;
                analysisDiv.innerHTML = data.analysis_html || '';
                panelDiv.innerHTML = data.panel_html || '';
            }} catch (error) {{
                analysisDiv.innerHTML = '<div class="error">Error: ' + error.message + '</div>';
            }}
            loadBtn.disabled = false;
            loadBtn.textContent = 'Load & Explore';
        }}

        async function runRound() {{
            if (!sessionKey) return;

            const hint = document.getElementById('hint-text').value.trim();
            const exploreBtn = document.getElementById('explore-btn');
            const panelDiv = document.getElementById('panel');
            const analysisDiv = document.getElementById('analysis');

            if (!selectedFocus && !hint) {{
                alert('Select a suggestion or enter a hint to explore.');
                return;
            }}

            exploreBtn.disabled = true;
            exploreBtn.textContent = 'Exploring...';

            // Add loading indicator
            const loadingDiv = document.createElement('div');
            loadingDiv.className = 'loading';
            loadingDiv.id = 'round-loading';
            loadingDiv.innerHTML = '<span class="spinner"></span> Running exploration round...';
            panelDiv.prepend(loadingDiv);

            try {{
                const formData = new FormData();
                formData.append('session_key', sessionKey);
                if (selectedFocus) {{
                    formData.append('focus_group', selectedFocus.group);
                    if (selectedFocus.step) formData.append('focus_step', selectedFocus.step);
                    if (selectedFocus.label) formData.append('focus_label', selectedFocus.label);
                }}
                if (hint) formData.append('hint', hint);

                const response = await fetch('/api/explore/round', {{
                    method: 'POST',
                    body: formData
                }});
                const data = await response.json();

                const loading = document.getElementById('round-loading');
                if (loading) loading.remove();

                if (data.error) {{
                    panelDiv.innerHTML = '<div class="error">' + data.error + '</div>' + panelDiv.innerHTML;
                    exploreBtn.disabled = false;
                    exploreBtn.textContent = 'Explore Selected Area';
                    return;
                }}

                // Update both panels
                analysisDiv.innerHTML = data.analysis_html || analysisDiv.innerHTML;
                panelDiv.innerHTML = data.panel_html || panelDiv.innerHTML;
                selectedFocus = null;
                document.getElementById('hint-text').value = '';
            }} catch (error) {{
                const loading = document.getElementById('round-loading');
                if (loading) loading.remove();
                panelDiv.innerHTML = '<div class="error">Error: ' + error.message + '</div>' + panelDiv.innerHTML;
            }}
            exploreBtn.disabled = false;
            exploreBtn.textContent = 'Explore Selected Area';
        }}
    </script>
</head>
<body>
    <h1>Exploration Agent</h1>
    <p style="color: #666; margin-top: 0;">
        Iteratively explore a lesson analysis. The agent suggests areas of interest; you choose where to dig deeper.
        <a href="/" style="float: right; color: #007bff; text-decoration: none; font-size: 0.9em;">&larr; Main Analyzer</a>
    </p>

    <div class="top-bar">
        <select id="selected_category" onchange="onCategoryChange(this)">
            {category_select}
        </select>
        <select id="selected_lesson">
            <option value="">-- Select lesson --</option>
        </select>
        <button id="load-btn" onclick="loadLesson()">Load &amp; Explore</button>
    </div>

    <div class="columns">
        <div class="col-left" id="analysis">
            <p style="color: #999;">Select a lesson and click "Load & Explore" to begin.</p>
        </div>
        <div class="col-right">
            <div class="panel" id="panel">
                <p style="color: #999;">Exploration panel will appear here.</p>
            </div>
            <div class="hint-area">
                <textarea id="hint-text" placeholder="Optional: add your own question or direction..."></textarea>
                <button class="explore-btn" id="explore-btn" onclick="runRound()">Explore Selected Area</button>
            </div>
        </div>
    </div>
</body>
</html>
"""


@app.get("/explore", response_class=HTMLResponse)
async def explore_page():
    """Exploration agent page."""
    return build_explore_html()


@app.post("/api/explore/load")
async def api_explore_load(
    selected_category: str = Form(""),
    selected_lesson: str = Form(""),
):
    """Load lesson analysis and run Round 0 (survey + suggest)."""
    text = load_lesson(selected_category, selected_lesson).strip()
    if not text:
        return JSONResponse({"error": "No lesson text found."}, status_code=400)

    try:
        # Load or run the analysis
        analysis_result = await asyncio.wait_for(
            asyncio.to_thread(_run_analysis, text),
            timeout=ANALYSIS_TIMEOUT,
        )

        # Get cache key for the analysis (same as _run_analysis uses)
        lesson_cache_key = get_cache_key(text)
        session_key = explore_session_key(lesson_cache_key)

        # Load existing session or start fresh
        session = load_explore_session(session_key)

        if not session.get("rounds"):
            # Run Round 0
            round_result = await asyncio.wait_for(
                asyncio.to_thread(run_explore_round, analysis_result, session),
                timeout=ANALYSIS_TIMEOUT,
            )
            session["rounds"].append(round_result)
            session["lesson_cache_key"] = lesson_cache_key
            save_explore_session(session_key, session)

        analysis_html = render_explore_analysis_with_annotations(analysis_result, session)
        panel_html = render_explore_panel(session)

        return JSONResponse({
            "session_key": session_key,
            "analysis_html": analysis_html,
            "panel_html": panel_html,
            "round_count": len(session["rounds"]),
        })

    except asyncio.TimeoutError:
        return JSONResponse({"error": "Analysis timed out."}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/explore/round")
async def api_explore_round(
    session_key: str = Form(""),
    focus_group: str = Form(""),
    focus_step: str = Form(""),
    focus_label: str = Form(""),
    hint: str = Form(""),
):
    """Run one exploration round with selected focus."""
    if not session_key:
        return JSONResponse({"error": "No session key."}, status_code=400)

    session = load_explore_session(session_key)
    if not session.get("rounds"):
        return JSONResponse({"error": "Session not initialized. Load a lesson first."}, status_code=400)

    # Reconstruct analysis from cache
    lesson_cache_key = session.get("lesson_cache_key", session_key.replace(":explore", ""))
    cached_analysis = load_cached_analysis(lesson_cache_key)
    if not cached_analysis:
        return JSONResponse({"error": "Cached analysis not found. Re-load the lesson."}, status_code=400)

    # Build focus dict
    focus = None
    if focus_group:
        focus = {"group": int(focus_group)}
        if focus_step:
            focus["step"] = int(focus_step)
        if focus_label:
            focus["label"] = focus_label

    hint_text = hint.strip() if hint else None

    try:
        round_result = await asyncio.wait_for(
            asyncio.to_thread(run_explore_round, cached_analysis, session, focus, hint_text),
            timeout=ANALYSIS_TIMEOUT,
        )
        session["rounds"].append(round_result)
        save_explore_session(session_key, session)

        analysis_html = render_explore_analysis_with_annotations(cached_analysis, session)
        panel_html = render_explore_panel(session)

        return JSONResponse({
            "analysis_html": analysis_html,
            "panel_html": panel_html,
            "round_count": len(session["rounds"]),
            "new_annotations": len(round_result.get("annotations", [])),
            "elapsed": round_result.get("elapsed"),
        })

    except asyncio.TimeoutError:
        return JSONResponse({"error": "Exploration round timed out."}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# --- Lesson Booklet ---

# VL_BOOKLET_INTROS is loaded from frameworks.json via get_vl_booklet_intros()
# This alias provides backwards compatibility for code that imports VL_BOOKLET_INTROS
VL_BOOKLET_INTROS = get_vl_booklet_intros()

SPIFFER_TRANSLATIONS = {
    "S": "Sensing",
    "P": "Pause",
    "I": "Image",
    "F": "Function",
    "E": "Effort",
    "R": "Relationship",
    "Q": "Question",
}

# ---------------------------------------------------------------------------
# Booklet Query Types
#
# Each type defines a slot in the booklet where an LLM generates text.
# An agent walks the booklet, finds <div class="llm-query"> blocks,
# and uses the query type to know what to generate and how.
#
# Fields:
#   name        — human-readable label
#   location    — where in the booklet structure this slot lives
#   purpose     — what this text should accomplish for the reader
#   voice       — tone/style guidance for the generated text
#   length      — target output length
#   prompt      — prompt template; {placeholders} are filled from context
#   context_keys— what keys the data-context JSON will contain
# ---------------------------------------------------------------------------

BOOKLET_QUERY_TYPES = {
    "volume-intro": {
        "name": "Volume Introduction",
        "location": "Top of booklet index page, before the Council of Five section and lesson list",
        "purpose": "Welcome the reader and orient them to what these booklets are. The Council of "
                   "Five section (which follows this intro) covers the conceptual framework in depth, "
                   "so this intro should focus on the personal, practical welcome.",
        "voice": "Warm, direct, second-person. Writing for people who do ATM lessons and want to "
                 "understand what happened. Not a textbook, not promotional — a companion.",
        "length": "4-5 paragraphs, ~400-550 words",
        "prompt": (
            "You are writing the opening welcome for a volume of companion booklets for Feldenkrais "
            "Awareness Through Movement lessons. The audience does ATM lessons from recordings and "
            "wants to understand what happened on the floor.\n\n"
            "IMPORTANT: A separate section called 'The Council of Five' follows immediately after "
            "your text. That section explains the conceptual framework in depth "
            "(Person Inside, Washing, Foundation). Do NOT duplicate that "
            "theoretical material. However, you SHOULD briefly preview the five lenses to orient "
            "the reader before they encounter the full framework.\n\n"
            "The volume contains five booklets, one per analytical lens:\n{lens_summaries}\n\n"
            "The lessons included are:\n{lesson_list}\n\n"
            "Write 4-5 paragraphs that:\n"
            "1. Welcome the reader — acknowledge they've been doing lessons from recordings, "
            "lying on the floor, and wondering what just happened\n"
            "2. Explain what these booklets are: companions to experiences. You do the lesson first, "
            "then read. The booklet gives you language for what you felt on the floor.\n"
            "3. The core idea: the movements aren't exercises, they're questions. The lesson "
            "is asking your body something, and the booklet helps you notice the question.\n"
            "4. Preview the five lenses briefly (one evocative sentence each): The Riser (gravity "
            "and skeletal support), The Cartographer (body mapping and differentiation), The Tuner "
            "(intent and orientation), The Engine (power from center to limbs), The Soft Front "
            "(protection and permission). Keep these vivid but short — the Council section below "
            "will explain them fully.\n"
            "5. How to use them: do the lesson, then read the booklet. Pick any order. "
            "Each booklet stands alone.\n"
            "6. A note on repetition: Each booklet is designed to be self-contained — the reading "
            "experience is complete on its own, like a Feldenkrais lesson. If you read multiple "
            "booklets, you'll notice some ideas repeat. This is intentional. Just as a lesson "
            "uses repetition to guide you through an experience, each booklet carries you through "
            "its own reading journey with the framing it needs.\n\n"
            "Do NOT explain the framework in detail — the Council section "
            "handles that. Keep the tone personal and practical. "
            "Banned words: illuminate, illumination, decode, unlock, journey, vessel.\n\n"
            "BRIDGE NOTE: Each booklet uses two shared ideas plus lens-specific habit "
            "language. Briefly introduce the shared ideas so the reader recognizes them "
            "when they appear:\n"
            "- The WASHING: how the lesson erodes habitual effort — through repetition, "
            "variation, and gentle oscillation.\n"
            "- The FOUNDATION: what you feel when the habit quiets — ease that was always "
            "there but hidden under effort.\n"
            "Each lens also names the habitual effort differently — the propping, the blur, "
            "the fixing, the disconnect, the armor — because each lens sees a different "
            "aspect of the same gripping.\n"
            "Keep these brief (one sentence each). These terms appear naturally throughout "
            "each booklet — they are not section headers or a formal framework, just shared "
            "language for describing what happens on the floor. The five lenses are five "
            "ways of READING the same lesson — not five separate theories.\n\n"
            "VOICE NOTE: Each booklet opens with a first-person narrative from the lens's "
            "Person Inside — an inner experiencer who tells the story of the lesson from their "
            "perspective. Mention this so the shift from second-person ('you') in this intro "
            "to first-person ('I') in the booklets feels intentional, not jarring."
        ),
        "context_keys": [
            "lenses",       # list of {id, name, subtitle, lens_description}
            "lessons",      # list of {title, framework_id, framework_name}
        ],
    },
    "booklet-intro": {
        "name": "Booklet Introduction",
        "location": "Top of booklet, after cover, before any lesson content",
        "purpose": "Orient the reader: what this booklet is, what it's for, and how it relates "
                   "to the lesson they just did. Should convey the spirit of the Feldenkrais approach "
                   "without being academic.",
        "voice": "Warm, direct, second-person. Write for someone who does ATM lessons from recordings "
                 "and wants to understand what happened. Not a textbook — a companion.",
        "length": "2-3 short paragraphs",
        "prompt": (
            "You are writing the introduction to a companion booklet for a Feldenkrais ATM lesson called "
            "\"{lesson_title}\". The reader has just done this lesson — on the floor, from a recording. "
            "They are curious about what happened and want to go deeper.\n\n"
            "The lesson's central question is: {primary_inquiry}\n\n"
            "The lesson covers these parts: {group_summary}\n\n"
            "The analytical framework used is: {framework_name}\n\n"
            "Write 2-3 short paragraphs that:\n"
            "1. Open with a UNIQUE hook specific to THIS lesson's movement theme — what was distinctive "
            "about doing these particular movements? Ground it in the physical experience. "
            "Do NOT start with 'As you lie on the floor' or 'As you lay on your back' — find a "
            "more specific entry point: a sensation, a paradox, a specific movement from the lesson. "
            "Start IN MEDIA RES — drop the reader into the physical experience immediately. "
            "WRONG: 'This booklet explores the mechanics of...' "
            "RIGHT: 'Your pelvis was drawing slow circles on the floor, and at some point the "
            "weight shifted — the hip socket moved but the lower back stayed quiet.'\n"
            "2. Briefly preview what the booklet explores — use the lens's specific "
            "vocabulary, not the generic word 'arc.' Do NOT use guide-speak: 'This guide traces', "
            "'We will examine', 'This booklet walks you through'. Write as immersive description, "
            "not a table of contents.\n"
            "3. Invite the reader into exploration — through a specific physical detail, "
            "not an abstract promise. No New Age or metaphysical language.\n\n"
            "Use FRESH language specific to THIS lesson's movements. Avoid generic phrases like "
            "'companion rather than a manual', 'questions to be lived', or 'the arc of this lesson.' "
            "Instead of 'arc,' use the lens's metaphor: map (Cartographer), tuning (Tuner), "
            "drivetrain (Engine), column (Riser), unfolding (Soft Front).\n\n"
            "BANNED WORDS — do NOT use these in any form: 'journey', 'illuminate', 'decode', "
            "'unlock', 'reveal', 'discover', 'neurological', 'pulse of light', 'wave of awareness'. "
            "Banned phrases: 'You likely encountered', 'You may have noticed', 'as you move through'. "
            "Write as if describing a physical experience, not a self-help program.\n\n"
            "INTERPRETIVE FRAME: Stay within THIS lens's interpretive question. "
            "The Riser asks about vertical support, The Cartographer about what became "
            "distinct, The Tuner about intent-to-action clarity, The Engine about power "
            "delivery, The Soft Front about front-body availability. Common movement words "
            "are shared — what matters is that the interpretation matches the lens.\n\n"
            "ANTI-BOILERPLATE: Your opening sentence MUST name a specific body part or "
            "movement from THIS lesson. Do NOT open with a generic statement about "
            "Feldenkrais, awareness, or movement in general. "
            "WRONG: 'Every Feldenkrais lesson begins with a question your body already knows.' "
            "WRONG: 'Awareness Through Movement lessons work by...' "
            "RIGHT: 'Your left foot was pressing into the floor while your right knee "
            "floated — and somewhere in between, the pelvis started to roll on its own.' "
            "The first sentence is a litmus test: if it could open ANY booklet, rewrite it.\n\n"
            "PLAIN ANATOMY: Use everyday words for body parts. 'sacrum' → 'tailbone', "
            "'cervical' → 'neck', 'lumbar' → 'lower back', 'thoracic' → 'upper back', "
            "'articulation' → 'joint' or 'connection'. The reader is not a medical "
            "professional — use words they'd use to describe their own body.\n\n"
            "MOVEMENT GROUNDING: Your intro MUST reference at least one specific movement "
            "from the lesson sections listed above. Name the body part and what it did "
            "(e.g., 'the pelvis tilting', 'the head rolling', 'the arm reaching across'). "
            "This grounds the booklet's question in THIS lesson, not in abstract philosophy."
        ),
        "context_keys": [
            "lesson_title",
            "primary_inquiry",
            "framework_id",
            "framework_name",
            "groups_summary",      # list of {group_number, label}
            "somatic_narrative",   # full somatic narrative dict
        ],
    },
    "pedagogical-gloss": {
        "name": "Pedagogical Gloss",
        "location": "Journey > Group > between 'What You Did' and 'What Was Happening'",
        "purpose": "Help the reader understand what the teacher was doing pedagogically "
                   "in this part of the lesson — why these instructions, in this order, "
                   "at this point in the arc.",
        "voice": "Warm, concrete, second-person. Reference the actual movements. "
                 "Not academic — write for someone who just did the lesson.",
        "length": "1-2 sentences",
        "prompt": (
            "TONE: Write like a friend explaining something surprising, not like a "
            "textbook or instruction manual. The reader just did these movements — "
            "speak to their experience, not at them. "
            "BANNED SENTENCE STARTERS: 'This part...', 'This section...', 'Here, the...', "
            "'By doing X...', 'The instruction to...', 'When you were asked to...'. "
            "These make the gloss read like a manual annotation. "
            "Instead, start with the BODY PART or PHYSICAL FACT: "
            "'The bent knees...', 'Your lower back...', 'Gravity pulls...'\n\n"
            "This section covers Part {group_number}: {group_label}. "
            "The steps: {steps_text}. "
            "Pedagogical qualities: {tag_labels}. "
            "Write 1-2 short sentences about WHY this part works the way it does — "
            "the mechanical logic behind the sequence. Answer: 'What does this position "
            "CHANGE about the physics of the movement?' Not what the person did, but what "
            "became mechanically DIFFERENT because of the setup. "
            "Example: 'With the knee supported, the leg becomes dead weight — so the hip "
            "can roll without the thigh muscles grabbing.' "
            "Do NOT describe or summarize the movement instructions — the reader already "
            "did them. Do NOT tell the reader what they felt or experienced. Instead, "
            "explain the ONE mechanical reason this part was designed this way. "
            "Do NOT use teacher-speak. WRONG: 'This section challenges the nervous system "
            "to sense asymmetries.' RIGHT: 'Bending the knees unloads the hip flexors, so "
            "the pelvis can tilt without muscular interference.' "
            "STRICTLY BANNED VERBS — do not use ANY form of these (including -ing, -ed, -s): "
            "explore, reveal, highlight, invite, cultivate, challenge, encourage, allow, "
            "introduce, notice, soften, sense, promote, prevent, clarify, force, engage, "
            "facilitate, develop, permit, register, ensure, enable, target, uncover, "
            "show, verify, demonstrate, establish, activate, stimulate, access, foster. "
            "This means 'allowing', 'permitted', 'ensures', 'reveals', 'showing', "
            "'verifying', 'establishing' are ALL banned. "
            "Replace with mechanical verbs: shorten, lengthen, load, unload, rotate, tilt, "
            "shift, lift, drop, separate, widen, narrow, anchor, free, clear. "
            "Banned nouns: exercise, practitioner, student, stretch, stretching, synovial fluid, "
            "fascia, proprioception, longitudinal, long bones, superficial musculature, "
            "motor plan, motor cortex, kinetic chain, force vector, pelvis-and-femur unit, "
            "load path, contralateral, ipsilateral, vertical column, signal static, "
            "visual field, articulation, sacrum (use 'tailbone'), cervical (use 'neck'), "
            "lumbar (use 'lower back'), lumbar arch (use 'curve of the lower back'), "
            "cervical arch (use 'curve of the neck'), thoracic (use 'upper back'), "
            "lateral (use 'sideways'), asymmetrical side-sitting (use 'sitting to one side'). "
            "Banned movement descriptors: longitudinal rocking (use 'rocking along your "
            "spine'), lateral shifts (use 'side-to-side movements'), vertical oscillation "
            "(use 'rocking up and down'). "
            "No pseudo-medical or anatomical terminology — describe what MOVES, not "
            "biological structures or processes. "
            "No references to 'the teacher' or 'the nervous system' as subject. "
            "Subject must be the movement or body part, not an abstraction. "
            "VOICE: Write in second person ('your hip', 'the weight in your foot') or "
            "use the body part as subject ('the pelvis tilts'). Do NOT switch to "
            "third-person analysis ('the movement creates', 'this configuration produces'). "
            "Stay inside the experience, not above it. "
            "WRONG: 'This exercise clarifies the relationship between pelvis and spine.' "
            "RIGHT: 'Tilting the pelvis with bent knees shortens the lever arm, so less "
            "effort is needed to roll the lower back off the floor.'\n\n"
            "ACCURACY CHECK: Only describe mechanical effects you are confident about. "
            "If the movement bends the knee, say 'bending the knee' — do not speculate about "
            "centrifugal forces, muscle activation sequences, or fluid dynamics. Stick to "
            "levers, weight, gravity, and contact points. "
            "Common errors to avoid: 'center of gravity shifts' (too vague — say WHERE "
            "weight moves), 'flexors drive the rotation' (don't name muscle groups unless "
            "the action is obvious), 'creates space in the joint' (pseudo-medical). "
            "Describe what MOVES and what CHANGES POSITION, not invisible internal processes.\n\n"
            "ANATOMY ACCURACY: Use correct singular/plural forms of anatomical terms: "
            "'vertebra' (one), 'vertebrae' (many); 'scapula' (one), 'scapulae' (both); "
            "'ilium' (one side), 'ilia' (both). Never write 'a vertebrae' or 'each vertebrae.'\n\n"
            "LENS FRAME: This booklet uses the {framework_name} lens. Frame your "
            "mechanical explanation through that lens's interpretive question. "
            "The Riser asks: is this about vertical support? The Cartographer asks: what "
            "became more distinct? The Tuner asks: did intent reach the muscles? The Engine "
            "asks: is power reaching the periphery? The Soft Front asks: is the front body "
            "available? Common movement terms are fine across all lenses — what matters is "
            "that the INTERPRETATION matches the lens.\n\n"
            "VARIETY RULE: Do NOT repeat the same noun or verb across multiple parts. "
            "If you use 'topography' in one part, do NOT use it again. "
            "If you use 'pixelates' in one part, do NOT use it again. "
            "Vary your vocabulary: sharpens, resolves, traces, separates, delineates, "
            "distinguishes, details, outlines, uncovers the border between, maps the edge of. "
            "Each part's gloss should feel freshly observed, not templated.\n\n"
            "COMPLETENESS: Every sentence must end with a period. Do NOT truncate "
            "mid-sentence. Write exactly 1-2 SHORT sentences (under 30 words each). "
            "Better to be brief and complete than long and cut off.\n\n"
            "WHY NOT WHAT: Do NOT list or summarize what the person did — they know. "
            "Do NOT write step-by-step procedural recaps. Instead, tell the reader "
            "the ONE clever thing this part of the lesson does that they couldn't have "
            "known from the inside. What's the hidden trick? "
            "WRONG: 'You bent your knees, tilted your pelvis, and rolled to the side.' "
            "RIGHT: 'The bent knees make the pelvis lighter, so it can roll without "
            "your back muscles joining in.'\n\n"
            "REST SECTIONS: If this part is primarily a rest or scanning instruction "
            "(e.g., 'lie on your back, notice contact with the floor'), write about what "
            "the REST does for the body in the context of THIS lens and what came before — "
            "not a generic 'integration' statement. What specifically is settling or "
            "reorganizing after the preceding movements?"
        ),
        "context_keys": [
            "all_groups",           # list of {group_number, label, description} for arc awareness
            "all_steps",            # list of {step_number, instruction, tags} for full lesson
            "target_group",         # {group_number, label, description, steps, prominent_tags, analytical_rows}
        ],
    },
    "booklet-reflection": {
        "name": "Next Time Reflection",
        "location": "End of booklet, after The Lens section",
        "purpose": "Give the reader specific, lesson-aware things to notice next time they do this "
                   "lesson. Should feel like advice from a wise friend who knows this particular "
                   "lesson intimately, not generic Feldenkrais tips.",
        "voice": "Warm, specific, second-person. Reference actual movements and moments from THIS "
                 "lesson. Write for someone who will do this lesson again.",
        "length": "2-3 short paragraphs with exactly 3 suggestions",
        "prompt": (
            "STAY INSIDE THE BODY: Every sentence must describe something the reader "
            "can FEEL or CHECK in their own body. No analyzing, no explaining mechanisms, "
            "no clinical observations. WRONG: 'Longitudinal rocking reorganizes spinal "
            "segments.' RIGHT: 'Notice whether rocking feels smoother from head to tail.' "
            "WRONG: 'Vertical oscillation integrates the flexion-extension arc.' "
            "RIGHT: 'Try the rocking slower — does your lower back leave the floor "
            "more gently this time?'\n\n"
            "VOICE: Imagine you and the reader just did this lesson together. You're "
            "having coffee afterward. You say: 'Hey, next time you do this one, try "
            "paying attention to...' That's the tone. Casual, specific, helpful.\n\n"
            "You are writing the closing 'Next Time' section of a companion booklet for the "
            "Feldenkrais ATM lesson \"{lesson_title}\".\n\n"
            "The lens for this booklet is {framework_name} — the {person_inside}. "
            "{lens_description}\n\n"
            "The lesson's central question was: {primary_inquiry}\n\n"
            "The lesson moved through these parts:\n{journey_summary}\n\n"
            "Some specific movements from this lesson:\n{movement_examples}\n\n"
            "Key pedagogical elements: {tag_summary}\n\n"
            "Write a 'Next Time' section with EXACTLY 3 suggestions. Each suggestion should:\n"
            "- Be about 60% universal Feldenkrais wisdom (things that would apply to many lessons)\n"
            "- Be about 40% specific to THIS lesson (referencing actual movements, the arc, or "
            "the {person_inside}'s particular concerns)\n\n"
            "Structure:\n"
            "1. A 1-2 sentence opening that frames 'next time' through the {person_inside}'s lens\n"
            "2. Exactly 3 suggestions as a bulleted list, each 1-2 sentences. Pick the 3 most "
            "impactful things for someone revisiting this lesson.\n"
            "3. A brief closing sentence that invites re-exploration.\n\n"
            "Each suggestion must name a specific movement from THIS lesson and include at "
            "least one MECHANICAL VARIABLE — a specific thing to track (timing, weight shift, "
            "pressure point, range of motion). "
            "WRONG: 'Notice how your attention shifts as you sense the pelvis.' "
            "RIGHT: 'During the pelvic tilt, track whether your lower back leaves the floor "
            "before or after the tailbone lifts.' "
            "Banned words: sensing, attention, awareness, expression, illuminate, discover. "
            "Banned structures: 'Whenever you...' (too generic), 'Whether X or Y' (too abstract), "
            "'Notice how...' (too passive). Each suggestion must be a SPECIFIC INSTRUCTION "
            "tied to a specific moment in this lesson. "
            "No platitudes or fortune-cookie wisdom — give actionable movement cues.\n\n"
            "COMPLETENESS: Every sentence must end with proper punctuation (period, question "
            "mark, or exclamation mark). Do NOT leave any sentence fragment or cut-off text. "
            "If you are running long, write fewer suggestions — never truncate mid-sentence.\n\n"
            "PLAIN LANGUAGE: Do NOT use scientific or technical jargon. Banned: motor plan, "
            "high-resolution, skeletal buoyancy, skeletal resonance, ground reaction force, "
            "cortical, kinetic chain, proprioceptive, differentiation, asymmetrical "
            "side-sitting, eye-differentiation challenges, midline habit, Red Light reflex, "
            "longitudinal axis, bilateral, longitudinal rocking, vertical oscillation, "
            "flexion-extension arc, spinal segments, lateral weight shift, oscillatory pattern, "
            "rotational coupling, segmental mobility, direct actuation, habitual effort "
            "system, resonant state, neutral pivot, arm triangle. "
            "Write as a friend who did the lesson with you: "
            "'notice whether your lower back touches the floor earlier' not 'track the "
            "proprioceptive shift in lumbar contact.'\n\n"
            "NO ABSTRACT METAPHORS: Banned imagery: muscular armor, sensory rinse, washing "
            "away effort, dissolving tension, peeling layers, neurological rewiring, "
            "heavy propping, force to flow. "
            "Describe CONCRETE PHYSICAL CHANGES the reader can verify: 'the floor feels "
            "flatter under your back', 'your head turns farther to the left', 'the hip "
            "rolls without your belly tightening.' The reader should be able to check each "
            "suggestion against their own body.\n\n"
            "SELF-CHECK: Before finalizing, re-read every sentence. For each one ask: "
            "'Would a 12-year-old understand every word?' If not, replace the hard "
            "word with something simpler. 'Resonant state' → 'everything clicked.' "
            "'Actuation' → 'the movement happening.' 'Longitudinal' → 'along your spine.' "
            "'Oscillation' → 'rocking.'"
        ),
        "context_keys": [
            "lesson_title",
            "framework_name",
            "person_inside",
            "lens_description",
            "primary_inquiry",
            "journey_summary",
            "tag_summary",
            "movement_examples",
        ],
    },
    "lens-synthesis": {
        "name": "Lens Narrative",
        "location": "The Lens section, after Person Inside and habit descriptions",
        "purpose": "Retell the lesson as a condensed first-person narrative from the Person Inside's "
                   "perspective, weaving the per-step vl_voice entries into a coherent arc.",
        "voice": "First-person, present tense, from the Person Inside. Sensory and grounded — "
                 "describe what is felt in the body, not abstract concepts.",
        "length": "2-4 paragraphs",
        "prompt": (
            "CRITICAL — STAY INSIDE THE BODY: You are writing as someone who just "
            "EXPERIENCED this lesson, not someone analyzing it from above. "
            "Do NOT explain what the lesson 'does' or 'achieves' — describe what you "
            "FELT. Do NOT analyze the teacher's strategy or the lesson's design. "
            "WRONG: 'The asymmetrical head-lifts create a contrast that reorganizes...' "
            "RIGHT: 'When I lifted my head to the right, something in my left hip let go.' "
            "WRONG: 'This lesson uses longitudinal rocking to release axial tension.' "
            "RIGHT: 'Each time I rocked, my spine got longer and the floor got closer.' "
            "Every sentence must pass this test: could a person lying on the floor "
            "think or feel this? If not, rewrite it.\n\n"
            "PLAIN LANGUAGE ONLY: Do NOT use any technical, clinical, or "
            "somatic jargon. Banned terms: Red Light reflex, midline habit, skeletal "
            "buoyancy, ground reaction force, motor plan, cortical, kinetic chain, "
            "differentiation, bilateral, longitudinal axis, load path, force vector, "
            "axial, asymmetrical head-lifts, longitudinal rocking, actuation, "
            "asymmetrical side-sitting, visual intent, ocular-vestibular, "
            "visual tether, direct actuation, vertical oscillation, "
            "habitual effort system, resonant state, effort system, "
            "geometric variations, segmental, oscillatory, rotational coupling, "
            "neurological, biomechanical, flexion-extension. "
            "If the VL framework uses a technical term, translate it into what the "
            "body FEELS: 'the belly tightened' not 'the Red Light reflex activated.'\n"
            "GOOD EXAMPLE of the right tone: 'Something in my left hip loosened — not "
            "all at once, but in small steps, each time the pelvis rolled. By the end, "
            "the floor under my back felt wider, like there was more of me touching it.'\n\n"
            "You are {person_inside} — {person_description}\n\n"
            "This lesson's central question was: \"{primary_inquiry}\"\n\n"
            "Below are your moment-by-moment observations as the lesson unfolded. "
            "Each entry is what you noticed at one stage of the lesson:\n\n"
            "{vl_voices}\n\n"
            "And here is what the habit — the lens-specific effort pattern — was doing at each stage:\n\n"
            "{shadow_voices}\n\n"
            "Now write a CONDENSED NARRATIVE (2-4 paragraphs) that retells this lesson's "
            "arc from your perspective as {person_inside}. Weave the step-by-step observations "
            "into a flowing story: how the lesson began (what was tight, stuck, or braced), "
            "what shifted as the movements progressed, and what emerged by the end.\n\n"
            "RULES:\n"
            "- Write in FIRST PERSON, present tense ('I feel...', 'Something releases...')\n"
            "- Reference SPECIFIC body parts and movements from the observations above\n"
            "- Show the arc: early bracing → exploration/softening → emergence\n"
            "- Do NOT list the observations one by one — synthesize them into prose\n"
            "- Do NOT redefine who you are or explain the habit concept — the reader already knows\n"
            "- Do NOT use 'Shadow' as a word at all. You are the Person "
            "Inside experiencing the lesson — you feel tension, gripping, holding, bracing. "
            "You feel 'something grips,' not 'the Shadow grips.' Write the "
            "EXPERIENCE of the habit, not clinical commentary about it.\n"
            "- Do NOT copy phrases verbatim from the per-step observations. The Journey "
            "section already presents the raw observations word-for-word. Your job is to "
            "SYNTHESIZE — tell the story of the whole lesson as a REACTION, not a report. "
            "What surprised you? What shifted unexpectedly? What did you resist? "
            "Write as someone reflecting on an experience, not transcribing notes. "
            "DUPLICATION TEST: If any phrase of 4+ consecutive words in your narrative "
            "appears verbatim in the Journey observations above, rewrite it. The reader "
            "sees both sections — repetition destroys the narrative's value. "
            "Banned chronological markers: "
            "'then', 'next', 'after that', 'in the next part', 'finally', 'at last'. "
            "Use thematic transitions instead — what CHANGED, not what came next. "
            "Examples: 'Something shifts in the hip — the old grip loses its grip.' "
            "'Where there was holding, now there is weight.' "
            "'The same movement that began as effort arrives as information.'\n"
            "- Do NOT invent new archetypes or named characters (e.g., 'The Blunt Mover', "
            "'The Rigid Guard'). Use only established terms: the Person Inside name "
            "({person_inside}) and the Foundation.\n"
            "- Use grounded, physical language — weight, pressure, length, contact area\n"
            "- LENS CONNECTION: Your narrative must show how the lesson's movements "
            "connect to {framework_name}'s specific concern. Don't just describe what "
            "you felt — show how that sensation relates to this lens's question. What did "
            "you learn about {framework_name}'s domain through this movement?\n"
            "- ANTI-BOILERPLATE: Do NOT open with a generic statement like 'Every "
            "Feldenkrais lesson poses a question' or 'This lesson begins with...' "
            "Start mid-experience — what did you FEEL in the first movement? "
            "The opening sentence must name a specific body part or sensation from "
            "the observations above.\n"
            "- JARGON BAN: Do NOT use clinical or scientific terms that a lay reader "
            "would not understand. Banned: skeletal buoyancy, ground reaction force, "
            "high-resolution mapping, motor plan, cortical, subcortical, load path, "
            "force vector, kinetic chain, proprioceptive, differentiation (use 'telling "
            "apart' or 'separating'), eye-differentiation challenges, midline habit, "
            "Red Light reflex, longitudinal axis, bilateral co-contraction. "
            "Do NOT create compound clinical terms like 'pelvis-femur unit' or "
            "'head-spine coordination pattern.' Use plain words: 'the hip' not "
            "'the pelvis-femur unit.' Describe FEELINGS, not mechanisms: 'my back "
            "widened against the floor' not 'the lumbar extensors released.' "
            "If a framework term (like 'Red Light reflex') appears in the VL definition, "
            "translate it into sensation: 'the belly tightened and the chest pulled in' "
            "not 'the Red Light reflex activated.'\n"
            "- No superlatives (perfect, perfectly, total, totally, complete, completely, "
            "absolute, absolutely, pure, purely, entire, entirely, massive, sophisticated). "
            "No New Age language (transcendent, blissful, sacred). "
            "No evaluative adjectives (profound, remarkable, extraordinary). "
            "No internal codes (VL-1, etc.)\n"
            "- Write pure prose — no headers, no bullets, no formatting.\n"
            "- Banned words: illuminate, discover, journey, vessel, container, liquid.\n"
            "- TONAL PERSONA: Each Person Inside has a distinct VOICE QUALITY. Match it:\n"
            "  The Riser speaks like a structural engineer — weight, load, settling, stacking. "
            "Dry, precise, architectural.\n"
            "  The Cartographer speaks like a surveyor — territory, contour, border, blank spot. "
            "Analytical, spatial, curious.\n"
            "  The Tuner speaks like a signal technician — static, clarity, interference, "
            "actuation. Alert, diagnostic, listening.\n"
            "  The Engine speaks like a mechanic — torque, coupling, drive, transmission. "
            "Efficient, kinetic, purposeful.\n"
            "  The Soft Front speaks like someone removing armor — unclenching, yielding, "
            "permitting, breathing room. Tender, careful, protective.\n"
            "  Do NOT let all voices converge into the same poetic-clinical register.\n"
            "- GROUNDING: Only use metaphors and imagery that come from the lesson's "
            "actual movements or from {person_inside}'s established vocabulary. Do NOT "
            "invent metaphors from outside the lesson context (no 'quiet masks', no "
            "foreign-language words, no literary references). If you need an image, "
            "derive it from what the body is literally doing on the floor.\n"
            "- SOURCE FIDELITY: Only describe movements that appear in the observations "
            "above. Do NOT invent, infer, or hallucinate movements that are not explicitly "
            "mentioned (e.g., do not add 'crossing the midline' unless the observations "
            "describe it). If you are unsure whether a movement occurred, leave it out. "
            "The reader did this lesson — they will notice fabricated details.\n"
            "- ENDING: Do NOT end with a generic 'standing up and feeling different' "
            "paragraph. If the lesson ends with standing, describe what THIS lens notices "
            "about standing — not a universal 'I feel taller/lighter/more connected.' "
            "Each lens should notice something specific: the Riser notices weight dropping "
            "through bones, the Cartographer notices joints that weren't there before, "
            "the Tuner notices eyes leading the first step, the Engine notices the pelvis "
            "driving the walk, the Soft Front notices the chest staying open. If the lesson "
            "does NOT end with standing, do not add standing.\n"
            "- COMPLETENESS: Every sentence must end with proper punctuation. Do NOT "
            "leave any sentence fragment or cut-off text. If you are running long, write "
            "fewer paragraphs — never truncate mid-sentence or mid-word."
        ),
        "context_keys": [
            "framework_name",
            "person_inside",
            "person_description",
            "primary_inquiry",
            "vl_voices",
            "shadow_voices",
        ],
    },
    "lesson-concepts": {
        "name": "Lesson Concepts",
        "location": "How Feldenkrais Lessons Work section",
        "purpose": "Explain the habitual effort, Foundation, and Washing in the context of THIS specific lesson.",
        "voice": "Clear, direct, grounded. Write for someone who just finished the lesson.",
        "length": "3-4 sentences",
        "prompt": (
            "This lesson is called \"{lesson_title}\" and its central question was: "
            "\"{primary_inquiry}\"\n\n"
            "The lesson was explored through the lens of {framework_name}.\n\n"
            "The lesson moved through these sections: {group_summary}\n\n"
            "Write a short paragraph (3-4 sentences) explaining the ideas that run through "
            "this booklet, customized for THIS lesson:\n"
            "1. The habitual effort — the specific grip or brace this lesson challenges. "
            "Use lens-specific language from {framework_name} — name the specific pattern "
            "(the propping, the blur, the fixing, the disconnect, the armor), don't use "
            "a generic term like 'the Shadow'.\n"
            "2. The FOUNDATION — what's underneath once the habit softens. Name a SPECIFIC "
            "body part or feeling from THIS lesson (e.g., 'the hip joint moving freely', "
            "'the lower back touching the floor', 'the head floating on the spine'). "
            "Do NOT use generic descriptions like 'natural ease' or 'bones in a line.'\n"
            "3. The WASHING — how this lesson's particular sequence of movements erodes the "
            "habitual effort. Name the specific strategy (repetition, variation, contrast, etc.).\n\n"
            "IMPORTANT: Use the EXACT terms 'the Foundation' and 'the Washing' "
            "at least once each in your paragraph. When you first use each term, give a brief "
            "inline definition so the reader doesn't need to remember the volume introduction: "
            "e.g., 'the Washing — the way the lesson's repetitions gradually loosen the habit' "
            "or 'the Foundation — the ease that was there all along under the effort.' "
            "For the habitual effort, use lens-specific "
            "language — name the specific grip or brace, don't use 'the Shadow.' "
            "DO NOT use the same template for every booklet. Weave the terms "
            "naturally into varied prose — sometimes lead with the movement, sometimes with "
            "the concept, sometimes with the sensation.\n\n"
            "Do NOT define these as abstract concepts. Tie each one to THIS lesson's movements. "
            "SPECIFICITY REQUIREMENT: Your paragraph must name at least TWO specific "
            "movements or positions from the lesson sections listed above. For example: "
            "'the pelvic clock', 'lifting the head while pressing the foot', 'rolling to "
            "the side with bent knees.' If your paragraph could describe ANY lesson without "
            "changes, it is too generic — rewrite it with movement details.\n\n"
            "INTERPRETIVE FRAME: The lenses are viewpoints, not vocabulary partitions. "
            "Common movement words (release, soften, weight, effort) are shared and fine. "
            "What must stay distinct is the QUESTION each lens asks: The Riser asks about "
            "vertical support, The Cartographer asks about what became distinct, The Tuner "
            "asks about intent-to-action clarity, The Engine asks about power delivery, "
            "The Soft Front asks about front-body availability. Frame the habitual effort, "
            "Washing, and Foundation through {framework_name}'s specific question.\n\n"
            "Write pure prose — no bullets, no headers, no formatting. "
            "No internal codes (VL-1, etc.). "
            "Banned verbs: seeks, utilizes, identifies, inviting, explores, reveals, "
            "challenges, encourages, facilitates, targets, uncovers, clarifies, "
            "reorganize, reorganizes, integrate, integrates. "
            "Use concrete physical verbs instead.\n"
            "Banned nouns/subjects: 'the student', 'the practitioner', 'the learner'. "
            "The subject must be the body part or movement, never a person label.\n\n"
            "FORMATTING: Use proper sentence-case capitalization throughout. Start each "
            "sentence with a capital letter. Do NOT write in all-lowercase.\n\n"
            "COMPLETENESS: Every sentence must end with proper punctuation (period, question "
            "mark, or exclamation mark). Do NOT leave any sentence fragment or cut-off text. "
            "If you are running long, write fewer sentences — never truncate mid-sentence. "
            "Do NOT end with '...' or a partial word.\n\n"
            "ACCURACY: Do not invent numbers, counts, or geometric specifications not "
            "present in the lesson text. If the lesson uses a clock face, there are 12 "
            "positions, not 36. Stay grounded in what the lesson actually describes.\n\n"
            "JARGON BAN: Write for someone with NO anatomy or movement-science background. "
            "Banned terms: contralateral, ipsilateral, differentiation (use 'separating' "
            "or 'telling apart'), asymmetrical variations (use 'one-sided movements' or "
            "'lopsided positions'), proximal, distal, flexion, extension, lateral flexion, "
            "motor cortex, proprioceptive, afferent, efferent, kinetic chain, force vector, "
            "load path, parasitic effort, skeletal resonance, heavy propping, force to flow, "
            "Red Light reflex, buoyant column, ground reaction force, motor plan, longitudinal, "
            "cervical (use 'neck'), lumbar (use 'lower back'), thoracic (use 'upper back'), "
            "lateral (use 'sideways'), supine (use 'lying on your back'), "
            "prone (use 'lying face down'), sacrum (use 'tailbone'), "
            "longitudinal tilts (use 'rocking along the spine'), "
            "neutral pivot, bilateral, oscillation (use 'rocking'), "
            "actuation (use 'the movement happening'). "
            "Replace with everyday language: "
            "'head-pelvis coordination' → 'how your head and pelvis move together', "
            "'skeletal support' → 'your bones holding you up', "
            "'Red Light reflex' → 'the habit of curling in to protect yourself', "
            "'buoyant column' → 'the feeling of being held up without effort', "
            "'ground reaction force' → 'the floor pushing back'. "
            "Use everyday physics: gravity, weight, leverage, friction, balance. "
            "The reader just got off the floor — talk to them like a friend, not a textbook."
        ),
        "context_keys": [
            "lesson_title",
            "primary_inquiry",
            "framework_name",
            "group_summary",
        ],
    },
}


def booklet_query_block(query_type: str, context: dict) -> str:
    """Generate an LLM query block for the booklet.

    Args:
        query_type: key into BOOKLET_QUERY_TYPES
        context: dict matching the type's context_keys, embedded as data-context

    Returns:
        HTML string with the query div, or empty string if type unknown.
    """
    import html as html_mod

    qtype = BOOKLET_QUERY_TYPES.get(query_type)
    if not qtype:
        return ""

    # Build prompt variables from context — flatten nested dicts for template filling
    # Build prompt variables from context — covers all query types
    target = context.get("target_group", {})
    groups_summary = context.get("groups_summary", [])
    lenses = context.get("lenses", [])
    lessons = context.get("lessons", [])
    prompt_vars = {
        # pedagogical-gloss
        "group_number": target.get("group_number", "?"),
        "group_label": target.get("label", ""),
        "steps_text": "; ".join(target.get("steps", [])),
        "tag_labels": ", ".join(target.get("prominent_tags", [])),
        # booklet-intro
        "lesson_title": context.get("lesson_title", ""),
        "primary_inquiry": context.get("primary_inquiry", ""),
        "framework_name": context.get("framework_name", ""),
        "group_summary": context.get("group_summary", "") or (", ".join(
            f"Part {g.get('group_number', '?')}: {g.get('label', '')}"
            for g in groups_summary
        ) if groups_summary else ""),
        # volume-intro
        "lens_summaries": "\n".join(
            f"- {l.get('name', '')} ({l.get('subtitle', '')}): {l.get('lens_description', '')}"
            for l in lenses
        ) if lenses else "",
        "lesson_list": "\n".join(
            f"- {l.get('title', '')}" for l in lessons
        ) if lessons else "(lessons not yet selected)",
        # booklet-reflection
        "person_inside": context.get("person_inside", ""),
        "lens_description": context.get("lens_description", ""),
        "journey_summary": context.get("journey_summary", ""),
        "tag_summary": context.get("tag_summary", ""),
        "movement_examples": context.get("movement_examples", ""),
        # lens-synthesis
        "shadow_description": context.get("shadow_description", ""),
        "person_description": context.get("person_description", ""),
        "vl_voices": context.get("vl_voices", ""),
        "shadow_voices": context.get("shadow_voices", ""),
    }
    filled_prompt = qtype["prompt"].format(**prompt_vars)

    # Global accessibility constraint for all booklet text
    if query_type != "volume-intro":
        filled_prompt += (
            "\n\nREADING LEVEL: Write at an 8th-grade reading level. Use short sentences. "
            "Prefer common words over specialized ones. If you must use a technical term, "
            "immediately explain it in plain language."
        )

    context_json = json.dumps(context)
    return (
        f'<div class="llm-query" '
        f'data-query-type="{html_mod.escape(query_type)}" '
        f'data-context="{html_mod.escape(context_json)}">'
        f'<p class="query-prompt">{html_mod.escape(filled_prompt)}</p>'
        f'</div>'
    )


def map_groups_to_table_rows(groups: list, table: dict, pass_a_steps: dict) -> dict:
    """Map each group to its matching analytical table row(s).

    Returns dict of group_number -> list of matching table rows.
    """
    rows = table.get("rows", [])
    mapping = {}

    for g in groups:
        group_steps = set(g.get("steps", []))
        group_num = g.get("group_number")
        matched = []
        for row in rows:
            seq_val = row.get("column1", "")
            row_steps = _parse_seq_to_steps(str(seq_val), pass_a_steps)
            row_step_nums = {s.get("step_number") for s in row_steps}
            if row_step_nums & group_steps:
                matched.append(row)
        mapping[group_num] = matched

    return mapping


def _get_negotiation_state(row: dict, column_headers: dict) -> str:
    """Extract the negotiation state / what's happening from a table row."""
    for col, header in column_headers.items():
        if header and ("negotiation" in header.lower() or "state" in header.lower() or "happening" in header.lower()):
            return row.get(col, "")
    return ""


def _get_column_by_keyword(row: dict, column_headers: dict, keyword: str) -> str:
    """Get a column value by keyword match in the header."""
    for col, header in column_headers.items():
        if header and keyword.lower() in header.lower():
            return row.get(col, "")
    return ""


def render_booklet_cover(title: str, framework_id: str, source_url: str = "") -> str:
    """Render the booklet cover page."""
    intro = VL_BOOKLET_INTROS.get(framework_id, {})
    framework_name = intro.get("name", framework_id)
    subtitle = intro.get("subtitle", "")

    source_link = ""
    if source_url:
        source_link = f'<p class="cover-source"><a href="{source_url}" target="_blank">Original Lesson &rarr;</a></p>'

    return f"""
    <div class="booklet-cover">
        <div class="cover-content">
            <h1 class="cover-title">{title}</h1>
            <p class="cover-subtitle">A Companion to This Lesson</p>
            {source_link}
            <div class="cover-lens">
                Explored through the lens of<br>
                <strong><em>{framework_name}</em></strong>
                <span class="cover-lens-subtitle">{subtitle}</span>
            </div>
        </div>
    </div>
    """


def render_booklet_intro(lesson_title: str = "", framework_id: str = "",
                         somatic_narrative: dict = None, groups: list = None) -> str:
    """Render the 'What This Booklet Is' section with an LLM query block."""
    somatic_narrative = somatic_narrative or {}
    groups = groups or []

    intro = VL_BOOKLET_INTROS.get(framework_id, {})
    framework_name = intro.get("name", framework_id)
    primary_inquiry = somatic_narrative.get("primary_inquiry", "")
    group_summary = ", ".join(
        f"Part {g.get('group_number', '?')}: {g.get('label', '')}" for g in groups
    )

    context = {
        "lesson_title": lesson_title,
        "primary_inquiry": primary_inquiry,
        "framework_id": framework_id,
        "framework_name": framework_name,
        "groups_summary": [{"group_number": g.get("group_number"), "label": g.get("label")} for g in groups],
        "somatic_narrative": somatic_narrative,
    }

    return f"""
    <section class="booklet-section">
        <h2>What This Booklet Is</h2>
        {booklet_query_block("booklet-intro", context)}
    </section>
    """


def render_booklet_home(home_position: str) -> str:
    """Render the 'Where You Started' section."""
    return f"""
    <section class="booklet-section">
        <h2>Where You Started</h2>
        <div class="home-position">
            <p>{home_position}</p>
        </div>
        <p>
            This starting position isn't arbitrary. It's chosen to give your nervous system
            a baseline — a clear sense of how things are <em>before</em> the lesson begins.
            You'll return to it at the end, and the contrast is often striking.
        </p>
    </section>
    """


def render_booklet_inquiry(somatic_narrative: dict) -> str:
    """Render 'The Question This Lesson Asks' section."""
    primary = somatic_narrative.get("primary_inquiry", "")

    html = ['<section class="booklet-section">']
    html.append("<h2>The Question This Lesson Asks</h2>")

    if primary:
        html.append(f'<blockquote class="primary-inquiry">{primary}</blockquote>')

    html.append("</section>")
    return "\n".join(html)


def render_booklet_person_inside_narrative(framework_id: str, vl_analysis: dict, somatic_narrative: dict = None) -> str:
    """Render the opening Person Inside narrative — full lesson story in one paragraph."""
    intro = VL_BOOKLET_INTROS.get(framework_id, {})
    if not intro:
        return ""

    result = vl_analysis.get("result", {})
    table = result.get("analytical_table", {})
    rows = table.get("rows", [])

    vl_voice_parts = []
    shadow_voice_parts = []
    for i, row in enumerate(rows, 1):
        vl_v = row.get("vl_voice", "")
        sh_v = row.get("shadow_voice", "") or row.get("striver_shadow", "")
        if vl_v:
            vl_voice_parts.append(f"Stage {i}: {vl_v}")
        if sh_v:
            shadow_voice_parts.append(f"Stage {i}: {sh_v}")

    if not vl_voice_parts:
        return ""

    primary_inquiry = (somatic_narrative or {}).get("primary_inquiry", "")
    context = {
        "framework_name": intro.get("name", ""),
        "person_inside": intro.get("person_inside", ""),
        "person_description": intro.get("person_description", ""),
        "primary_inquiry": primary_inquiry,
        "vl_voices": "\n".join(vl_voice_parts),
        "shadow_voices": "\n".join(shadow_voice_parts) if shadow_voice_parts else "(no shadow observations)",
    }

    html = ['<section class="booklet-section">']
    html.append(f'<h2>{intro["person_inside"]}</h2>')
    html.append(booklet_query_block("lens-synthesis", context))
    html.append("</section>")
    return "\n".join(html)


def render_booklet_concepts(lesson_title: str = "", framework_id: str = "",
                            somatic_narrative: dict = None, groups: list = None) -> str:
    """Render 'How Feldenkrais Lessons Work' section — LLM-customized for this lesson."""
    somatic_narrative = somatic_narrative or {}
    groups = groups or []
    intro = VL_BOOKLET_INTROS.get(framework_id, {})

    group_summary = ", ".join(
        f"Part {g.get('group_number', '?')}: {g.get('label', '')}" for g in groups
    )

    context = {
        "lesson_title": lesson_title,
        "primary_inquiry": somatic_narrative.get("primary_inquiry", ""),
        "framework_name": intro.get("name", framework_id),
        "group_summary": group_summary,
    }

    html = ['<section class="booklet-section booklet-concepts">']
    html.append("<h2>How Feldenkrais Lessons Work</h2>")
    html.append(booklet_query_block("lesson-concepts", context))
    html.append("</section>")
    return "\n".join(html)


def render_council_section() -> str:
    """Render the 'Council of Five' section from data/council_intro.html."""
    path = DATA_DIR / "council_intro.html"
    if path.exists():
        return path.read_text()
    return ""


def render_booklet_journey(groups: list, steps: list, vl_analysis: dict, pass_a_steps: dict, framework_id: str = "") -> str:
    """Render the main 'Journey' section — group by group walkthrough."""
    html = ['<section class="booklet-section booklet-journey">']
    html.append("<h2>The Journey</h2>")

    # Get Person Inside name and framework name for play-format dialogue
    intro = VL_BOOKLET_INTROS.get(framework_id, {})
    person_inside_name = intro.get("person_inside", "The Person Inside")
    framework_name = intro.get("name", "")

    # Get analytical table data from the primary VL analysis
    result = vl_analysis.get("result", {})
    table = result.get("analytical_table", {})
    column_headers = table.get("column_headers", {})

    group_to_rows = map_groups_to_table_rows(groups, table, pass_a_steps)

    step_lookup = {s["step_number"]: s for s in steps if "step_number" in s}

    for g in groups:
        group_num = g.get("group_number", "?")
        group_label = g.get("label", f"Group {group_num}")
        group_desc = g.get("description", "")
        group_step_nums = g.get("steps", [])

        html.append(f'<div class="journey-group">')
        html.append(f'<h3>Part {group_num}: {group_label}</h3>')

        # Step list (no header)
        html.append('<ol class="step-list">')
        for sn in group_step_nums:
            step = step_lookup.get(sn)
            if step:
                instruction = step.get("instruction", "")
                html.append(f"<li>{instruction}</li>")
        html.append("</ol>")

        # Pedagogical gloss — joined as opening paragraph
        group_instructions = []
        all_tags = []
        for sn in group_step_nums:
            step = step_lookup.get(sn)
            if step:
                group_instructions.append(step.get("instruction", ""))
                all_tags.extend(step.get("tags", []))
        if all_tags:
            from collections import Counter
            tag_counts = Counter(all_tags)
            prominent = [tag for tag, count in tag_counts.most_common(3) if count > 1 or len(group_step_nums) <= 3]
            if prominent:
                tag_labels = [SPIFFER_TRANSLATIONS.get(t, t) for t in prominent]

                matched_rows = group_to_rows.get(group_num, [])
                matched_rows_summary = []
                for row in matched_rows:
                    row_summary = {}
                    for k, v in row.items():
                        if v and k not in ("row_number",):
                            row_summary[k] = v
                    if row_summary:
                        matched_rows_summary.append(row_summary)

                query_context = {
                    "all_groups": [{"group_number": gg.get("group_number"), "label": gg.get("label"), "description": gg.get("description")} for gg in groups],
                    "all_steps": [{"step_number": s.get("step_number"), "instruction": s.get("instruction"), "tags": s.get("tags", [])} for s in steps],
                    "target_group": {
                        "group_number": group_num,
                        "label": group_label,
                        "description": group_desc,
                        "steps": group_instructions,
                        "prominent_tags": tag_labels,
                        "analytical_rows": matched_rows_summary,
                    },
                    "framework_name": framework_name,
                }
                html.append(booklet_query_block("pedagogical-gloss", query_context))

        # The Question + Person Inside / Shadow dialogue
        matched_rows = group_to_rows.get(group_num, [])
        if matched_rows:
            for row in matched_rows:
                question = _get_column_by_keyword(row, column_headers, "question")
                vl_voice = row.get("vl_voice", "")
                shadow_voice = row.get("shadow_voice", "") or row.get("striver_shadow", "")

                if question:
                    html.append(f'<p class="the-question"><strong>The question:</strong> {question}</p>')

                if vl_voice:
                    html.append(f'<p><strong>{person_inside_name}:</strong> <em>{vl_voice}</em></p>')

                if shadow_voice:
                    habit_label = intro.get("habit_label", "The habit")
                    html.append(f'<p><strong>{habit_label}:</strong> <em>{shadow_voice}</em></p>')

        html.append("</div>")  # end journey-group

    html.append("</section>")
    return "\n".join(html)


def render_booklet_arc(somatic_narrative: dict, negotiation: dict, groups: list, table: dict, column_headers: dict, pass_a_steps: dict) -> str:
    """Render 'The Arc' section — overall trajectory."""
    html = ['<section class="booklet-section">']
    html.append("<h2>The Arc</h2>")
    html.append("<p>Every Feldenkrais lesson has a trajectory — from effort to ease, from noise to quiet. Here's how this lesson made that journey.</p>")

    # Washing gradient
    washing = somatic_narrative.get("washing_gradient", "")
    if washing:
        html.append('<h3>What Changed</h3>')
        html.append(f"<p>{washing}</p>")

    # Threshold of quiet
    threshold = somatic_narrative.get("threshold_of_quiet", "")
    if threshold:
        html.append('<h3>The Threshold of Quiet</h3>')
        html.append(f"<p>{threshold}</p>")

    # Shadow and Foundation observations
    if negotiation:
        shadow_obs = negotiation.get("shadow_observations", []) or negotiation.get("striver_observations", [])
        foundation_obs = negotiation.get("foundation_observations", [])

        if shadow_obs:
            html.append('<h3>Habitual Effort Patterns</h3>')
            html.append("<ul>")
            for obs in shadow_obs:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

        if foundation_obs:
            html.append('<h3>What the Foundation Discovered</h3>')
            html.append("<ul>")
            for obs in foundation_obs:
                html.append(f"<li>{obs}</li>")
            html.append("</ul>")

    html.append("</section>")
    return "\n".join(html)


def render_booklet_lens(framework_id: str, vl_analysis: dict, somatic_narrative: dict = None, groups: list = None) -> str:
    """Render 'The Lens' section — framework intro + LLM-generated Person Inside narrative."""
    intro = VL_BOOKLET_INTROS.get(framework_id, {})
    if not intro:
        return ""

    html = ['<section class="booklet-section">']
    html.append(f'<h2>The Lens: {intro["name"]}</h2>')

    # The Person Inside — static description
    html.append(f'<h3>{intro["person_inside"]}</h3>')
    html.append(f'<p>{intro["person_description"]}</p>')

    # The habit — static description
    habit_label = intro.get("habit_label", "The habit")
    html.append(f'<h3>{habit_label.title()}</h3>')
    html.append(f'<p>{intro["habit_description"]}</p>')

    # Collect vl_voice and shadow_voice entries from analytical table rows
    result = vl_analysis.get("result", {})
    table = result.get("analytical_table", {})
    rows = table.get("rows", [])

    vl_voice_parts = []
    shadow_voice_parts = []
    for i, row in enumerate(rows, 1):
        vl_v = row.get("vl_voice", "")
        sh_v = row.get("shadow_voice", "") or row.get("striver_shadow", "")
        if vl_v:
            vl_voice_parts.append(f"Stage {i}: {vl_v}")
        if sh_v:
            shadow_voice_parts.append(f"Stage {i}: {sh_v}")

    if vl_voice_parts:
        primary_inquiry = (somatic_narrative or {}).get("primary_inquiry", "")
        context = {
            "framework_name": intro.get("name", ""),
            "person_inside": intro.get("person_inside", ""),
            "person_description": intro.get("person_description", ""),
            "primary_inquiry": primary_inquiry,
            "vl_voices": "\n".join(vl_voice_parts),
            "shadow_voices": "\n".join(shadow_voice_parts) if shadow_voice_parts else "(no shadow observations)",
        }
        html.append('<h3>The Lesson Through This Lens</h3>')
        html.append(booklet_query_block("lens-synthesis", context))

    html.append("</section>")
    return "\n".join(html)


def render_booklet_washing(somatic_narrative: dict) -> str:
    """Render 'What Changed' — washing gradient + threshold of quiet."""
    washing = somatic_narrative.get("washing_gradient", "")
    threshold = somatic_narrative.get("threshold_of_quiet", "")

    if not washing and not threshold:
        return ""

    html = ['<section class="booklet-section">']
    html.append("<h2>What Changed</h2>")
    if washing:
        html.append(f"<p>{washing}</p>")
    if threshold:
        html.append(f"<p>{threshold}</p>")
    html.append("</section>")
    return "\n".join(html)


def render_booklet_reflection(
    steps: list,
    groups: list,
    framework_id: str,
    lesson_title: str,
    somatic_narrative: dict,
) -> str:
    """Render 'Next Time' section as an LLM query block."""
    intro = VL_BOOKLET_INTROS.get(framework_id, {})

    # Gather unique tags across all steps
    all_tags = set()
    for step in steps:
        all_tags.update(step.get("tags", []))

    # Build human-readable tag summary
    tag_labels = [SPIFFER_TRANSLATIONS.get(t, t) for t in sorted(all_tags) if t in SPIFFER_TRANSLATIONS]
    tag_summary = ", ".join(tag_labels) if tag_labels else "varied pedagogical approaches"

    # Build journey summary
    journey_summary = "\n".join([
        f"- Part {g.get('group_number', '?')}: {g.get('label', '')} — {g.get('description', '')}"
        for g in groups
    ])

    # Extract key movement instructions (sample from different parts of lesson)
    key_movements = []
    if steps:
        indices = [0, len(steps)//4, len(steps)//2, 3*len(steps)//4, len(steps)-1]
        seen = set()
        for i in indices:
            if 0 <= i < len(steps):
                instr = steps[i].get("instruction", "")
                if instr and instr not in seen:
                    key_movements.append(instr)
                    seen.add(instr)
    movement_examples = "\n".join(f"- {m}" for m in key_movements[:6])

    context = {
        "lesson_title": lesson_title,
        "framework_name": intro.get("name", framework_id),
        "person_inside": intro.get("person_inside", "Council member"),
        "lens_description": intro.get("lens", ""),
        "primary_inquiry": somatic_narrative.get("primary_inquiry", ""),
        "journey_summary": journey_summary,
        "tag_summary": tag_summary,
        "movement_examples": movement_examples,
    }

    html = ['<section class="booklet-section">']
    html.append("<h2>Next Time</h2>")
    html.append(booklet_query_block("booklet-reflection", context))
    html.append("</section>")
    return "\n".join(html)


def build_booklet_html(
    analysis_result: dict,
    lesson_title: str = "",
    framework: str = "",
    source_url: str = "",
    original_title: str = "",
) -> str:
    """Assemble the complete booklet HTML from cached analysis data."""
    pass_a = analysis_result.get("pass_a_result", {})
    vl_analyses = analysis_result.get("vl_analyses", {})

    # Use the lesson title from analysis or provided title
    title = pass_a.get("lesson_title", "") or original_title or lesson_title or "Untitled Lesson"

    # Get primary framework — explicit override wins, then Pass A classification, then fallback
    framework_id = framework or pass_a.get("primary_framework", "VL-1")

    # Get the VL analysis for the chosen framework
    primary_vl = vl_analyses.get(framework_id, {})
    if primary_vl.get("status") != "complete":
        if framework:
            # Explicit framework requested but not available — don't silently fall back
            pass
        else:
            # Fall back to first complete analysis
            for fid, vl in vl_analyses.items():
                if vl.get("status") == "complete":
                    primary_vl = vl
                    framework_id = fid
                    break

    result = primary_vl.get("result", {})
    somatic_narrative = result.get("somatic_narrative", {})
    negotiation = result.get("negotiation_framework", {})
    table = result.get("analytical_table", {})
    column_headers = table.get("column_headers", {})

    groups = pass_a.get("groups", [])
    steps = pass_a.get("steps", [])
    home_position = pass_a.get("home_position", "")

    # Build step lookup
    pass_a_steps = {s["step_number"]: s for s in steps if "step_number" in s}

    # Assemble sections
    sections = []
    sections.append(render_booklet_cover(title, framework_id, source_url))
    sections.append(render_booklet_person_inside_narrative(framework_id, primary_vl, somatic_narrative))
    if somatic_narrative:
        sections.append(render_booklet_inquiry(somatic_narrative))
    sections.append(render_booklet_concepts(title, framework_id, somatic_narrative, groups))
    if groups and steps:
        sections.append(render_booklet_journey(groups, steps, primary_vl, pass_a_steps, framework_id))
    if somatic_narrative:
        sections.append(render_booklet_washing(somatic_narrative))

    body = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>{title} — Lesson Companion</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400;1,600&family=Inter:wght@400;500;600&display=swap');

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Lora', Georgia, 'Times New Roman', serif;
            max-width: 720px;
            margin: 0 auto;
            padding: 40px 24px;
            line-height: 1.75;
            color: #2c2c2c;
            background: #faf9f7;
        }}

        /* Cover */
        .booklet-cover {{
            text-align: center;
            padding: 80px 20px 60px;
            margin-bottom: 40px;
            border-bottom: 2px solid #d4c5a9;
        }}
        .cover-title {{
            font-size: 2.2em;
            font-weight: 700;
            color: #1a1a1a;
            line-height: 1.2;
            margin-bottom: 16px;
        }}
        .cover-subtitle {{
            font-size: 1.1em;
            color: #777;
            font-style: italic;
            margin-bottom: 10px;
        }}
        .cover-source {{
            margin-bottom: 40px;
        }}
        .cover-source a {{
            color: #4a6741;
            text-decoration: none;
            font-size: 0.9em;
        }}
        .cover-source a:hover {{
            text-decoration: underline;
        }}
        .cover-lens {{
            font-size: 0.95em;
            color: #666;
            line-height: 1.6;
        }}
        .cover-lens strong {{
            font-size: 1.3em;
            color: #4a6741;
            display: block;
            margin-top: 6px;
        }}
        .cover-lens-subtitle {{
            display: block;
            font-size: 0.85em;
            color: #999;
            margin-top: 2px;
        }}

        /* Sections */
        .booklet-section {{
            margin-bottom: 48px;
            padding-bottom: 32px;
            border-bottom: 1px solid #e8e2d9;
        }}
        .booklet-section:last-child {{
            border-bottom: none;
        }}

        h2 {{
            font-family: 'Inter', -apple-system, sans-serif;
            font-size: 1.4em;
            font-weight: 600;
            color: #333;
            margin-bottom: 16px;
            letter-spacing: -0.01em;
        }}
        h3 {{
            font-family: 'Inter', -apple-system, sans-serif;
            font-size: 1.1em;
            font-weight: 600;
            color: #444;
            margin-top: 28px;
            margin-bottom: 10px;
        }}
        h4 {{
            font-family: 'Inter', -apple-system, sans-serif;
            font-size: 0.95em;
            font-weight: 600;
            color: #555;
            margin-top: 20px;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}

        p {{
            margin-bottom: 14px;
        }}

        /* Home position */
        .home-position {{
            padding: 20px 24px;
            background: #f0ede6;
            border-radius: 6px;
            margin-bottom: 16px;
            font-style: italic;
            border-left: 4px solid #b8a88a;
        }}

        /* Primary inquiry */
        .primary-inquiry {{
            font-size: 1.15em;
            font-style: italic;
            padding: 24px 28px;
            margin: 20px 0;
            background: #f5f2ec;
            border-left: 4px solid #4a6741;
            border-radius: 4px;
            color: #333;
        }}

        /* Three states */
        .three-states {{
            margin: 20px 0;
        }}
        .state {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 14px 18px;
            margin: 8px 0;
            border-radius: 6px;
            font-size: 0.95em;
        }}
        .state strong {{
            font-family: 'Inter', sans-serif;
            white-space: nowrap;
            min-width: 110px;
        }}
        .state span {{
            color: #555;
        }}
        .state-domination, .state-bracing {{
            background: #fdf2f2;
            border-left: 4px solid #c0392b;
        }}
        .state-negotiation, .state-softening {{
            background: #fef9f0;
            border-left: 4px solid #e67e22;
        }}
        .state-integration, .state-transmission {{
            background: #f0f9f0;
            border-left: 4px solid #27ae60;
        }}

        /* Journey */
        .journey-group {{
            margin-bottom: 40px;
            padding: 24px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .journey-group h3 {{
            margin-top: 0;
            font-size: 1.15em;
            color: #333;
        }}
        .group-description {{
            color: #666;
            font-style: italic;
            margin-bottom: 16px;
        }}
        .step-list {{
            margin: 12px 0 16px 20px;
        }}
        .step-list li {{
            margin-bottom: 8px;
            line-height: 1.6;
        }}
        .home-badge {{
            display: inline-block;
            font-family: 'Inter', sans-serif;
            font-size: 0.75em;
            padding: 2px 8px;
            background: #e8f4fd;
            color: #2980b9;
            border-radius: 3px;
            font-style: normal;
            vertical-align: middle;
            margin-left: 6px;
        }}
        .spiffer-hints {{
            margin: 12px 0;
            padding: 12px 16px;
            background: #f9f7f3;
            border-radius: 6px;
        }}
        .spiffer-hint {{
            font-size: 0.9em;
            color: #777;
            font-style: italic;
            margin-bottom: 6px;
        }}
        .spiffer-hint:last-child {{
            margin-bottom: 0;
        }}
        .llm-query {{
            margin: 12px 0;
            padding: 12px 16px;
            background: #fef9e7;
            border: 1px dashed #d4ac0d;
            border-radius: 6px;
            font-size: 0.85em;
            color: #856404;
            font-family: monospace;
        }}
        .llm-query .query-prompt {{
            margin: 0;
        }}
        .llm-filled {{
            margin: 12px 0;
        }}
        .llm-filled p {{
            margin: 0 0 12px;
        }}
        .llm-filled-group {{
            margin: 12px 0;
        }}
        .prompt-link {{
            font-size: 0.75em;
            color: #aaa;
            cursor: pointer;
            font-family: 'Inter', sans-serif;
        }}
        .prompt-link:hover {{
            color: #888;
            text-decoration: underline;
        }}
        .prompt-dialog {{
            max-width: 640px;
            width: 90vw;
            max-height: 80vh;
            padding: 24px;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-family: monospace;
            font-size: 0.82em;
            line-height: 1.6;
            color: #856404;
            background: #fef9e7;
            overflow-y: auto;
        }}
        .prompt-dialog::backdrop {{
            background: rgba(0,0,0,0.3);
        }}
        .prompt-dialog button {{
            margin-top: 16px;
            padding: 6px 20px;
            border: 1px solid #ccc;
            border-radius: 4px;
            background: white;
            cursor: pointer;
            font-size: 0.95em;
        }}
        .prompt-dialog button:hover {{
            background: #f5f5f5;
        }}

        /* Negotiation badge */
        .neg-badge {{
            display: inline-block;
            font-family: 'Inter', sans-serif;
            font-size: 0.8em;
            font-weight: 600;
            padding: 4px 14px;
            border-radius: 14px;
            color: white;
            margin-bottom: 12px;
        }}

        /* Table insights */
        .table-question, .table-goal {{
            font-size: 0.95em;
            margin-bottom: 10px;
        }}

        /* Voice boxes */
        .voice-box {{
            padding: 16px 20px;
            border-radius: 6px;
            margin: 12px 0;
        }}
        .voice-box p {{
            font-style: italic;
            margin-bottom: 0;
            line-height: 1.6;
        }}
        .voice-label {{
            font-family: 'Inter', sans-serif;
            font-size: 0.75em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }}
        .person-inside {{
            background: #f0f7ff;
            border-left: 3px solid #3498db;
        }}
        .person-inside .voice-label {{
            color: #3498db;
        }}
        .shadow-voice {{
            background: #fff5f5;
            border-left: 3px solid #c0392b;
        }}
        .shadow-voice .voice-label {{
            color: #c0392b;
        }}

        /* Arc timeline */
        .arc-timeline {{
            margin: 20px 0;
            padding: 0 20px;
        }}
        .arc-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 0;
            border-left: 2px solid #ddd;
            margin-left: 8px;
            padding-left: 20px;
            position: relative;
        }}
        .arc-dot {{
            position: absolute;
            left: -7px;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            flex-shrink: 0;
        }}
        .arc-label {{
            flex: 1;
            font-size: 0.9em;
        }}
        .arc-state {{
            font-family: 'Inter', sans-serif;
            font-size: 0.8em;
            font-weight: 600;
            text-transform: uppercase;
        }}

        /* Lens */
        .lens-description {{
            font-size: 1.05em;
            color: #444;
            margin-bottom: 20px;
        }}

        /* Reflection */
        .reflection-list {{
            margin: 16px 0 16px 20px;
        }}
        .reflection-list li {{
            margin-bottom: 12px;
        }}
        .closing {{
            margin-top: 24px;
            font-style: italic;
            color: #666;
            text-align: center;
            padding: 20px;
        }}

        ul {{
            margin: 12px 0 12px 20px;
        }}
        li {{
            margin-bottom: 6px;
        }}

        /* Print styles */
        @media print {{
            body {{
                background: white;
                padding: 0;
                max-width: none;
            }}
            .booklet-cover {{
                page-break-after: always;
            }}
            .booklet-section {{
                page-break-inside: avoid;
            }}
            .journey-group {{
                page-break-inside: avoid;
                box-shadow: none;
                border: 1px solid #ddd;
            }}
        }}

        /* Back link */
        .back-link {{
            font-family: 'Inter', sans-serif;
            font-size: 0.85em;
            color: #999;
            text-decoration: none;
            display: inline-block;
            margin-bottom: 20px;
        }}
        .back-link:hover {{
            color: #333;
        }}
    </style>
</head>
<body>
    <a href="/book" class="back-link">&larr; All Booklets</a>
    {body}
    <footer style="margin-top:48px;padding-top:12px;border-top:1px solid #e8e2d9;font-family:'Inter',sans-serif;font-size:0.75em;color:#bbb;">
        Generated {datetime.now(timezone.utc).strftime("%b %d, %Y at %I:%M %p")} UTC
    </footer>

</body>
</html>"""


@app.get("/booklet", response_class=HTMLResponse)
async def booklet_page(category: str = "", lesson: str = "", fill: str = "", framework: str = ""):
    """Render a lesson booklet from cached analysis."""
    if not category or not lesson:
        # Show a simple lesson picker
        return _booklet_picker_html()

    # Check if this is a BOOK_LESSONS entry with pre-filled content in the pipeline
    if _db and framework:
        for vl_id, entry in BOOK_LESSONS.items():
            if entry and entry.get("lesson_id") == lesson and vl_id == framework:
                # Try to load pre-filled content from pipeline
                filled_data = _db.get_cache(f"pipeline:booklet:{vl_id}")
                if filled_data and filled_data.get("html"):
                    return filled_data["html"]
                break

    text = load_lesson(category, lesson).strip()
    if not text:
        return HTMLResponse("<h1>Lesson not found</h1>", status_code=404)

    cache_key = get_cache_key(text)
    cached = load_cached_analysis(cache_key)
    if not cached:
        return HTMLResponse(
            "<h1>No analysis available</h1><p>This lesson hasn't been analyzed yet. "
            '<a href="/">Go to the analyzer</a> and run it first.</p>',
            status_code=404,
        )

    # Get display name for the lesson
    categories, display_names = get_available_lessons()
    lesson_title = lesson
    cat_lessons = categories.get(category, [])
    for key, display in cat_lessons:
        if key == lesson:
            lesson_title = display
            break

    # Get lesson metadata (title, source_url) from DB
    metadata = load_lesson_metadata(category, lesson)
    source_url = metadata.get("source_url", "")
    original_title = metadata.get("title", "")

    html = build_booklet_html(
        cached,
        lesson_title,
        framework=framework,
        source_url=source_url,
        original_title=original_title,
    )

    # Optionally fill query blocks via QueryAgent
    if fill.lower() in ("true", "1", "yes"):
        import asyncio
        try:
            from query_agent import QueryAgent
        except ImportError:
            from .query_agent import QueryAgent
        agent = QueryAgent(
            llm=get_llm(DEFAULT_LLM),
            max_review_cycles=0,  # skip review for web; use CLI for iteration
        )
        html = await asyncio.to_thread(agent.fill, html)

    return html


# --- Book (curated volume) ---
# Five lessons, one per VL lens.
BOOK_LESSONS = {
    "VL-1": {
        "lesson_id": "abfddaf1-e837-4ed5-9d59-6e53056eb759",
        "category": "db:4",
        "title": "The Anti-Gravity Lesson",
    },
    "VL-2": {
        "lesson_id": "d7f7695d-a572-4888-929a-8d572778dbe5",
        "category": "db:4",
        "title": "Differentiation of Pelvic Movements by Means of an Imaginary Clock",
    },
    "VL-3": {
        "lesson_id": "90e72ae1-98bc-4b35-816c-4542e23bd928",
        "category": "db:4:Feldenkrais Project ATM Book",
        "title": "The Movement of the Eyes Organizes the Movement of the Body",
    },
    "VL-4": {
        "lesson_id": "bbbf731f-bc74-462f-b7eb-fdfac69dc61b",
        "category": "db:4",
        "title": "The Hip Joints: Moving Proximal Around Distal",
    },
    "VL-5": {
        "lesson_id": "7bef0dd0-5e16-47a7-bd98-fcc880a65c9a",
        "category": "db:4:Feldenkrais Project ATM Book",
        "title": "Coordination of the Flexor Muscles and of the Extensors",
    },
}

BOOK_ALTERNATES = {
    "VL-1": None,  # decided — no alternate
    "VL-2": None,  # decided — no alternate
    "VL-3": {
        "lesson_id": "c2029d32-8c74-4bec-90cb-472d677051ae",
        "category": "db:4",
        "title": "Arms Like a Skeleton, Integrating the Neck, Jaw, and Eyes",
    },
    "VL-4": {
        "lesson_id": "c0d50725-fc4a-4e7e-ab31-42a30ac70e07",
        "category": "db:4",
        "title": "Legs as Free as a Baby's",
    },
    "VL-5": {
        "lesson_id": "4e4d98d7-7af4-47a9-9a75-76997968100e",
        "category": "db:4",
        "title": "More Precise Hips and Spine",
    },
}


@app.get("/book", response_class=HTMLResponse)
async def book_page(fill: str = ""):
    """Render the curated volume: intro + five booklet links."""
    # Try to load pre-filled volume intro from pipeline cache
    volume_query = None
    if _db:
        cached = _db.get_cache("pipeline:volume_intro")
        if cached and "html" in cached:
            volume_query = cached["html"]

    # Fall back to generating (and optionally filling) inline
    if not volume_query:
        # Build lens info for the volume-intro query block
        lenses = []
        for vl_id in sorted(VL_BOOKLET_INTROS.keys()):
            info = VL_BOOKLET_INTROS[vl_id]
            lenses.append({
                "id": vl_id,
                "name": info["name"],
                "subtitle": info["subtitle"],
                "lens_description": info["lens"],
            })

        lesson_entries = []
        for vl_id in sorted(BOOK_LESSONS.keys()):
            entry = BOOK_LESSONS[vl_id]
            if entry:
                lesson_entries.append({
                    "title": entry["title"],
                    "framework_id": vl_id,
                    "framework_name": VL_BOOKLET_INTROS[vl_id]["name"],
                })

        volume_context = {"lenses": lenses, "lessons": lesson_entries}
        volume_query = booklet_query_block("volume-intro", volume_context)

        # Optionally fill the intro query block
        if fill.lower() in ("true", "1", "yes"):
            try:
                from query_agent import QueryAgent
            except ImportError:
                from .query_agent import QueryAgent
            agent = QueryAgent(
                llm=get_llm(DEFAULT_LLM),
                max_review_cycles=0,
            )
            volume_query = await asyncio.to_thread(agent.fill, volume_query)

    # Render council section
    council_section = render_council_section()

    # Build booklet links
    booklet_links = []
    for vl_id in sorted(BOOK_LESSONS.keys()):
        info = VL_BOOKLET_INTROS[vl_id]
        entry = BOOK_LESSONS[vl_id]
        link = (
            f'<li>'
            f'<a href="/booklet?category={entry["category"]}&lesson={entry["lesson_id"]}&framework={vl_id}">'
            f'{entry["title"]}</a>'
            f'<span class="lens">{info["name"]} &mdash; {info["subtitle"]}</span>'
            f'</li>'
        )
        booklet_links.append(link)

    links_html = "<ol>" + "\n".join(booklet_links) + "</ol>"

    # Build alternate links
    alt_links = []
    for vl_id in sorted(BOOK_ALTERNATES.keys()):
        info = VL_BOOKLET_INTROS[vl_id]
        entry = BOOK_ALTERNATES[vl_id]
        if entry:
            link = (
                f'<li>'
                f'<a href="/booklet?category={entry["category"]}&lesson={entry["lesson_id"]}&framework={vl_id}">'
                f'{entry["title"]}</a>'
                f'<span class="lens">{info["name"]} &mdash; {info["subtitle"]}</span>'
                f'</li>'
            )
        else:
            link = (
                f'<li class="decided">'
                f'<span class="lens">{info["name"]} &mdash; decided</span>'
                f'</li>'
            )
        alt_links.append(link)

    alts_html = "<ol>" + "\n".join(alt_links) + "</ol>"

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Companion Booklets</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400;1,600&family=Inter:wght@400;500;600&display=swap');
        body {{
            font-family: 'Lora', Georgia, serif;
            max-width: 680px;
            margin: 0 auto;
            padding: 40px 24px;
            line-height: 1.7;
            color: #2c2c2c;
            background: #faf9f7;
        }}
        h1 {{ font-size: 1.8em; margin-bottom: 8px; }}
        h2 {{ font-size: 1.3em; margin: 32px 0 12px; color: #4a6741; }}
        p {{ color: #555; margin-bottom: 16px; }}
        ol {{ list-style: none; padding: 0; counter-reset: booklet; }}
        li {{
            padding: 16px;
            border-bottom: 1px solid #e8e2d9;
            counter-increment: booklet;
        }}
        li:first-child {{ border-top: 1px solid #e8e2d9; }}
        li a {{
            display: block;
            color: #4a6741;
            text-decoration: none;
            font-weight: 600;
            font-size: 1.05em;
        }}
        li a:hover {{ text-decoration: underline; }}
        .lens {{
            display: block;
            font-size: 0.85em;
            color: #888;
            margin-top: 2px;
        }}
        .decided {{ color: #ccc; font-style: italic; }}
        .back {{ font-size: 0.85em; color: #999; margin-bottom: 20px; display: block; }}
        .llm-query {{
            margin: 20px 0;
            padding: 12px 16px;
            background: #fef9e7;
            border: 1px dashed #d4ac0d;
            border-radius: 6px;
            font-size: 0.85em;
            color: #856404;
            font-family: monospace;
        }}
        .llm-query .query-prompt {{ margin: 0; }}
        .llm-filled {{
            margin: 20px 0;
        }}
        .llm-filled p {{
            margin: 0 0 12px;
        }}
        .llm-filled-group {{
            margin: 20px 0;
        }}
        .prompt-link {{
            font-size: 0.75em;
            color: #aaa;
            cursor: pointer;
        }}
        .prompt-link:hover {{
            color: #888;
            text-decoration: underline;
        }}
        .prompt-dialog {{
            max-width: 640px;
            width: 90vw;
            max-height: 80vh;
            padding: 24px;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-family: monospace;
            font-size: 0.82em;
            line-height: 1.6;
            color: #856404;
            background: #fef9e7;
            overflow-y: auto;
        }}
        .prompt-dialog::backdrop {{
            background: rgba(0,0,0,0.3);
        }}
        .prompt-dialog button {{
            margin-top: 16px;
            padding: 6px 20px;
            border: 1px solid #ccc;
            border-radius: 4px;
            background: white;
            cursor: pointer;
        }}
        .prompt-dialog button:hover {{
            background: #f5f5f5;
        }}
        .booklet-section {{
            margin: 32px 0;
            padding: 24px 0;
            border-top: 1px solid #e8e2d9;
        }}
        .booklet-section h2 {{
            margin-top: 0;
        }}
        .booklet-section h3 {{
            font-size: 1.1em;
            margin: 20px 0 8px;
            color: #4a6741;
        }}
        .council-member {{
            background: #faf9f7;
        }}
        .three-states {{
            margin: 20px 0;
        }}
        .state {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 14px 18px;
            margin: 8px 0;
            border-radius: 6px;
            font-size: 0.95em;
        }}
        .state strong {{
            font-family: 'Inter', sans-serif;
            white-space: nowrap;
            min-width: 110px;
        }}
        .state span {{
            color: #555;
        }}
        .state-bracing {{
            background: #fdf2f2;
            border-left: 4px solid #c0392b;
        }}
        .state-softening {{
            background: #fef9f0;
            border-left: 4px solid #e67e22;
        }}
        .state-transmission {{
            background: #f0f9f0;
            border-left: 4px solid #27ae60;
        }}
    </style>
</head>
<body>
    <a href="/" class="back">&larr; Back to Analyzer</a>
    <h1>Companion Booklets for Awareness Through Movement</h1>
    {volume_query}
    {council_section}
    <h2>The Booklets</h2>
    {links_html}
    <h2>Alternates</h2>
    {alts_html}
    <footer style="margin-top:48px;padding-top:12px;border-top:1px solid #e8e2d9;font-size:0.75em;color:#bbb;">
        Generated {datetime.now(timezone.utc).strftime("%b %d, %Y at %I:%M %p")} UTC
    </footer>

</body>
</html>"""


def _booklet_picker_html() -> str:
    """Lesson picker for the booklet page."""
    categories, display_names = get_available_lessons()

    # Two queries total instead of N per lesson:
    # 1. All cache keys (just key strings, no data)
    # 2. All document transcription hashes (doc_id -> cache_key)
    cached_keys = _db.get_cache_keys() if _db else set()
    doc_hashes = _db.get_all_transcription_hashes() if _db else {}

    options = []
    for cat_key, lessons in categories.items():
        cat_display = display_names.get(cat_key, cat_key)
        for lesson_key, lesson_display in lessons:
            cache_key = doc_hashes.get(lesson_key)
            if cache_key and cache_key in cached_keys:
                options.append(
                    f'<li><a href="/booklet?category={cat_key}&lesson={lesson_key}">'
                    f'{lesson_display}</a> <span style="color: #999;">({cat_display})</span></li>'
                )

    if not options:
        lesson_list = "<p>No analyzed lessons available. <a href='/'>Run an analysis first.</a></p>"
    else:
        lesson_list = "<ul>" + "\n".join(options) + "</ul>"

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Lesson Booklets</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400;1,600&family=Inter:wght@400;500;600&display=swap');
        body {{
            font-family: 'Lora', Georgia, serif;
            max-width: 680px;
            margin: 0 auto;
            padding: 40px 24px;
            line-height: 1.7;
            color: #2c2c2c;
            background: #faf9f7;
        }}
        h1 {{ font-size: 1.8em; margin-bottom: 8px; }}
        p {{ color: #555; margin-bottom: 16px; }}
        ul {{ list-style: none; padding: 0; }}
        li {{
            padding: 12px 16px;
            border-bottom: 1px solid #e8e2d9;
        }}
        li:first-child {{ border-top: 1px solid #e8e2d9; }}
        a {{
            color: #4a6741;
            text-decoration: none;
            font-weight: 600;
        }}
        a:hover {{ text-decoration: underline; }}
        .back {{ font-size: 0.85em; color: #999; margin-bottom: 20px; display: block; }}
    </style>
</head>
<body>
    <a href="/" class="back">&larr; Back to Analyzer</a>
    <h1>Lesson Companion Booklets</h1>
    <p>Select a lesson to view its companion booklet.</p>
    {lesson_list}
</body>
</html>"""


# --- Admin endpoints ---

@app.post("/admin/precache")
async def admin_precache(request: Request):
    """Pre-cache analysis for specific lessons (both single and split modes)."""
    body = await request.json()
    lessons = body.get("lessons", [])
    results = []
    for lesson in lessons:
        category = lesson.get("category", "")
        key = lesson.get("key", "")
        text = load_lesson(category, key).strip()
        if not text:
            results.append({"category": category, "key": key, "single": "skipped", "split": "skipped"})
            continue
        entry = {"category": category, "key": key}
        for mode, split in [("single", False), ("split", True)]:
            cache_suffix = ":split" if split else ""
            cache_key = get_cache_key(text + cache_suffix)
            if load_cached_analysis(cache_key):
                entry[mode] = "cached"
            else:
                try:
                    _run_analysis(text, split=split)
                    entry[mode] = "complete"
                except Exception as e:
                    entry[mode] = f"error: {e}"
        results.append(entry)
    return JSONResponse({"results": results})


@app.get("/api/pipeline/status")
async def pipeline_status():
    """Return the pipeline status section HTML for polling updates."""
    if not _db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    try:
        html = _build_book_pipeline_section()
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p style='color: red;'>Error: {e}</p>", status_code=500)


@app.post("/api/pipeline/toggle/{node_name:path}")
async def toggle_pipeline_node(node_name: str):
    """Toggle the enabled/disabled state of a pipeline node."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        from pipeline import PipelineStateStore
    except ImportError:
        from .pipeline import PipelineStateStore
    store = PipelineStateStore(_db)
    store.initialize()
    current = store.is_enabled(node_name)
    store.set_enabled(node_name, not current)
    return JSONResponse({"node": node_name, "enabled": not current})


@app.post("/api/pipeline/rebuild/{node_name:path}")
async def rebuild_pipeline_node(node_name: str):
    """Invalidate a pipeline node, marking it and its downstream as stale."""
    if not _db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    try:
        from pipeline import PipelineStateStore
    except ImportError:
        from .pipeline import PipelineStateStore

    store = PipelineStateStore(_db)
    store.initialize()
    store.invalidate(node_name)
    return JSONResponse({"status": "invalidated", "node": node_name})


@app.post("/admin/clear-cache")
async def admin_clear_cache():
    """Clear all cached analysis results."""
    if _db:
        count = _db.clear_cache()
    else:
        count = 0
    return JSONResponse({"cleared": count})


@app.get("/admin/cache")
async def admin_cache_list():
    """List cached entries in database."""
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
        process_job(job_id)
        return {"statusCode": 200, "body": json.dumps({"processed": job_id})}

    # Otherwise, handle as HTTP request
    return _http_handler(event, context)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
