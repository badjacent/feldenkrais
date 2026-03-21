"""Book pipeline graph definition.

Per-lesson pipeline: each lesson is analysed through all 5 VL agents,
enriched via cross-VL queries, merged via a VL-synthesis node, then
rendered as five per-VL booklets and synthesized into a book.

All node names and cache keys are namespaced by lesson_id so multiple
lessons can coexist in the same pipeline state table and cache.

Pipeline shape (per lesson):
    {lid}:src:lesson -> {lid}:analysis_a -> {lid}:analysis_b:{VL-1..5}
        -> {lid}:cross_query:{VL-1..5} -> {lid}:analysis_merge
        -> {lid}:booklet_template:{VL-1..5} -> {lid}:booklet:{VL-1..5}

Book synthesis (global):
    book (depends on all lessons' booklets)

Each transform is a factory closure that does its actual work by reading
from DB/files/pipeline cache, and writes results to both pipeline
namespace and legacy cache keys for /booklet endpoint compatibility.
"""

import hashlib
import json
from pathlib import Path
from typing import Callable, Optional

from pipeline import PipelineGraph


# ── Hash functions for source nodes ──────────────────────────────────────


def file_hasher(path: str | Path) -> Callable[[], str]:
    """Hash function for a file source. Returns 'missing' if file doesn't exist."""
    def hash_fn() -> str:
        p = Path(path)
        if not p.exists():
            return "missing"
        content = p.read_text()
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    return hash_fn


def dict_hasher(d: dict) -> Callable[[], str]:
    """Hash function for a code config source (dict serialized to JSON)."""
    def hash_fn() -> str:
        return hashlib.sha256(
            json.dumps(d, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
    return hash_fn


def db_lesson_hasher(db, doc_id: str) -> Callable[[], str]:
    """Hash function for a DB lesson transcription."""
    def hash_fn() -> str:
        text = db.get_document_transcription(doc_id)
        return hashlib.sha256((text or "").strip().encode()).hexdigest()[:16]
    return hash_fn


# ── Helpers ──────────────────────────────────────────────────────────────


def _clear_cache_key(db, key: str):
    """Delete a specific cache entry by exact key."""
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM cache WHERE key = %s", (key,))


def _save_with_history(db, key: str, data: dict, max_history: int = 20):
    """Save data to cache and append to history.

    Keeps a versioned history at {key}_history for tracking changes over time.
    """
    from datetime import datetime, timezone

    # Save current version
    db.set_cache(key, data)

    # Load existing history
    history_key = f"{key}_history"
    history = db.get_cache(history_key) or {"versions": []}

    # Append new version with timestamp
    history["versions"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    })

    # Trim to max_history
    if len(history["versions"]) > max_history:
        history["versions"] = history["versions"][-max_history:]

    db.set_cache(history_key, history)


def _web():
    """Lazy import of web module to avoid heavyweight init at graph construction."""
    import web as m
    return m


def _private_data_dir() -> Path:
    """Return the data directory, checking private/data/ first, then public data/.

    Now that this file lives in src/kb/, resolve relative to the project root.
    """
    project_root = Path(__file__).parent.parent.parent
    private_data = project_root / "private" / "data"
    if private_data.exists():
        return private_data
    return _web().DATA_DIR


# ── Transform factories ─────────────────────────────────────────────────


def _make_analysis_a_transform(db, lesson_id: str, cache_prefix: str, fv: Optional[str]):
    """Analysis A: segmentation + classification + SPIFFER-Q decomposition."""
    def transform(**_kwargs) -> str:
        w = _web()

        text = db.get_document_transcription(lesson_id)
        if not text:
            raise ValueError(f"No transcription for lesson {lesson_id}")
        text = text.strip()

        # Clear legacy Pass A cache to force recompute
        legacy_key = w._pass_a_cache_key(text, framework_version=fv)
        _clear_cache_key(db, legacy_key)

        # Run Pass A (will recompute since legacy cache is empty)
        result = w.run_pass_a(text, framework_version=fv)

        # Store in pipeline namespace for downstream nodes
        db.set_cache(f"{cache_prefix}:analysis_a", result)

        return json.dumps(result, sort_keys=True)

    return transform


def _make_analysis_b_transform(db, lesson_id: str, vl_id: str, cache_prefix: str, fv: Optional[str]):
    """Analysis B: VL-specific synthesis (somatic narrative + analytical table)."""
    def transform(**_kwargs) -> str:
        w = _web()

        text = db.get_document_transcription(lesson_id)
        if not text:
            raise ValueError(f"No transcription for lesson {lesson_id}")
        text = text.strip()

        # Read Analysis A result from pipeline cache (namespaced)
        pass_a_result = db.get_cache(f"{cache_prefix}:analysis_a")
        if not pass_a_result:
            raise ValueError("Analysis A result not found in pipeline cache")

        # Run Pass B for this specific VL framework
        result = w.run_pass_b(text, pass_a_result, vl_id, framework_version=fv)

        # Store in pipeline namespace
        db.set_cache(f"{cache_prefix}:analysis_b:{vl_id}", result)

        # Write combined result to legacy cache so /booklet endpoint works
        combined = {
            "pass_a_result": pass_a_result,
            "vl_analyses": {vl_id: {"status": "complete", "result": result}},
        }
        legacy_key = w.get_cache_key(text)
        db.set_cache(legacy_key, combined)

        return json.dumps(result, sort_keys=True)

    return transform


def _make_cross_query_transform(db, lesson_id: str, vl_id: str, cache_prefix: str, fv: Optional[str]):
    """Cross-query enrichment: VL reviews peer analyses and asks focused follow-ups."""
    def transform(**_kwargs) -> str:
        w = _web()
        from cross_query import run_cross_query_pass

        text = db.get_document_transcription(lesson_id)
        if not text:
            raise ValueError(f"No transcription for lesson {lesson_id}")
        text = text.strip()

        frameworks = w.load_frameworks(fv)

        # Load Pass B schema
        schema_path = _private_data_dir() / "pass_b.schema.json"
        pass_b_schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}

        llm = w.get_llm()

        result = run_cross_query_pass(
            llm, vl_id, db, text, frameworks, pass_b_schema,
            cache_prefix=cache_prefix,
        )

        return json.dumps(result, sort_keys=True)

    return transform


def _make_analysis_merge_transform(db, vl_ids: list[str], cache_prefix: str):
    """Merge all 5 VL analysis_b results into a single synthesis object.

    Placeholder logic: packages the results into a combined dict.
    """
    def transform(**_kwargs) -> str:
        pass_a = db.get_cache(f"{cache_prefix}:analysis_a")
        if not pass_a:
            raise ValueError("Analysis A result not found in pipeline cache")

        vl_analyses = {}
        for vl_id in vl_ids:
            result = db.get_cache(f"{cache_prefix}:analysis_b:{vl_id}")
            if result:
                vl_analyses[vl_id] = {"status": "complete", "result": result}

        combined = {"pass_a_result": pass_a, "vl_analyses": vl_analyses}
        db.set_cache(f"{cache_prefix}:analysis_merge", combined)
        return json.dumps(combined, sort_keys=True)

    return transform


def _make_booklet_template_transform(db, title: str, lesson_id: str, vl_id: str, cache_prefix: str):
    """Render booklet HTML template for a specific VL from synthesis data (non-LLM).

    Produces HTML with unfilled query blocks for the given VL lens.
    """
    def transform(**_kwargs) -> str:
        w = _web()

        synthesis = db.get_cache(f"{cache_prefix}:analysis_merge")
        if not synthesis:
            raise ValueError("VL synthesis data not found")

        # Get source_url from document metadata
        source_url = ""
        doc = db.get_document(lesson_id)
        if doc:
            source_url = doc.get("source_url", "") or ""

        html = w.build_booklet_html(
            synthesis,
            lesson_title=title,
            framework=vl_id,
            source_url=source_url,
            original_title=title,
        )

        db.set_cache(f"{cache_prefix}:booklet_template:{vl_id}", {"html": html})
        return html

    return transform


def _make_booklet_transform(db, vl_id: str, cache_prefix: str, node_name: str):
    """Fill LLM query blocks in a per-VL booklet template via QueryAgent.

    Produces the complete, filled booklet for the given VL lens.
    """
    def transform(**_kwargs) -> str:
        w = _web()
        from query_agent import QueryAgent

        template_data = db.get_cache(f"{cache_prefix}:booklet_template:{vl_id}")
        if not template_data or "html" not in template_data:
            raise ValueError(f"Booklet template not found for {vl_id}")

        html = template_data["html"]
        # Check node metadata for model override (use namespaced node name)
        llm_key = w.DEFAULT_LLM
        meta = db.get_cache(f"pipeline_meta:{node_name}")
        if meta and meta.get("llm"):
            llm_key = meta["llm"]
            print(f"[{node_name}] Using model override: {llm_key}", flush=True)
        agent = QueryAgent(
            llm=w.get_llm(llm_key),
            max_review_cycles=0,
        )
        filled = agent.fill(html)

        db.set_cache(f"{cache_prefix}:booklet:{vl_id}", {"html": filled})
        return filled

    return transform


def _make_book_synthesis_transform(db, lesson_entries: list[dict], vl_ids: list[str]):
    """Synthesize all lessons' booklets into a combined book.

    Placeholder: stores individual booklet references for later synthesis.
    """
    def transform(**_kwargs) -> str:
        booklets = {}
        for entry in lesson_entries:
            lid = entry["lesson_id"]
            cp = f"pipeline:{lid}"
            for vl_id in vl_ids:
                data = db.get_cache(f"{cp}:booklet:{vl_id}")
                if data and data.get("html"):
                    booklets[f"{lid}:{vl_id}"] = data["html"]

        if not booklets:
            raise ValueError("No filled booklets found in pipeline cache")

        # Placeholder: store booklet map for future synthesis logic
        db.set_cache("pipeline:book", {
            "booklets": booklets,
            "lesson_ids": [e["lesson_id"] for e in lesson_entries],
            "vl_ids": vl_ids,
        })
        return json.dumps({"keys": list(booklets.keys())})

    return transform


# ── Graph builder ────────────────────────────────────────────────────────


def build_lesson_pipeline(
    db,
    lesson_id: str,
    title: str,
    framework_version: Optional[str] = None,
) -> PipelineGraph:
    """Build the pipeline graph for a single lesson through all 5 VL agents.

    All node names are prefixed with ``{lesson_id}:`` and all cache keys
    use ``pipeline:{lesson_id}:`` so multiple lessons can coexist.

    Args:
        db: Database instance
        lesson_id: UUID of the lesson document
        title: Display title for the lesson
        framework_version: Version string (e.g. "v1"). None uses current.
    """
    w = _web()
    fv = framework_version
    prompts_dir, resolved_fv = w._resolve_prompts_dir(fv)

    # Namespace prefix for node names and cache keys
    lid = lesson_id
    cache_prefix = f"pipeline:{lid}"

    # All 5 VL IDs for the parallel analysis_b fan-out
    vl_ids = sorted(w.VL_BOOKLET_INTROS.keys())

    graph = PipelineGraph()

    # ── Source nodes (namespaced per lesson) ──

    graph.add_source(
        f"{lid}:src:lesson",
        db_lesson_hasher(db, lesson_id),
        description=f"Lesson: {title}",
        tags={"lesson_id": lesson_id},
    )

    # Shared source nodes (not namespaced — same prompts for all lessons)
    graph.add_source(
        "src:analysis_a_prompt",
        file_hasher(prompts_dir / "pass_a.md"),
        description="Analysis A prompt template",
    )
    graph.add_source(
        "src:analysis_a_schema",
        file_hasher(w.DATA_DIR / "pass_a.schema.json"),
        description="Analysis A JSON schema",
    )
    graph.add_source(
        "src:analysis_b_prompt",
        file_hasher(prompts_dir / "pass_b.md"),
        description="Analysis B base prompt",
    )
    graph.add_source(
        "src:analysis_b_schema",
        file_hasher(_private_data_dir() / "pass_b.schema.json"),
        description="Analysis B JSON schema",
    )
    graph.add_source(
        "src:frameworks",
        file_hasher(prompts_dir / "frameworks.json"),
        description="Framework registry",
    )
    graph.add_source(
        "src:booklet_config",
        dict_hasher({
            "intros": w.VL_BOOKLET_INTROS,
        }),
        description="Booklet intros config",
    )
    graph.add_source(
        "src:query_types",
        dict_hasher(w.BOOKLET_QUERY_TYPES),
        description="Booklet query type definitions",
    )

    # ── Single Analysis A ──

    graph.add_transform(
        f"{lid}:analysis_a",
        input_names=[
            f"{lid}:src:lesson",
            "src:analysis_a_prompt",
            "src:analysis_a_schema",
            "src:frameworks",
        ],
        transform=_make_analysis_a_transform(db, lesson_id, cache_prefix, fv),
        description=f"Analysis A: {title}",
        tags={"type": "llm", "lesson_id": lesson_id},
    )

    # ── Five parallel Analysis B nodes (one per VL) ──

    for vl_id in vl_ids:
        graph.add_transform(
            f"{lid}:analysis_b:{vl_id}",
            input_names=[
                f"{lid}:src:lesson",
                f"{lid}:analysis_a",
                "src:analysis_b_prompt",
                "src:frameworks",
                "src:analysis_b_schema",
            ],
            transform=_make_analysis_b_transform(db, lesson_id, vl_id, cache_prefix, fv),
            description=f"Analysis B ({vl_id}): {title}",
            tags={"vl": vl_id, "type": "llm", "lesson_id": lesson_id},
        )

    # ── Five parallel Cross-Query Enrichment nodes (one per VL) ──
    # Each depends on ALL 5 analysis_b nodes so all first-pass results are available.

    for vl_id in vl_ids:
        graph.add_transform(
            f"{lid}:cross_query:{vl_id}",
            input_names=[f"{lid}:analysis_b:{vid}" for vid in vl_ids],
            transform=_make_cross_query_transform(db, lesson_id, vl_id, cache_prefix, fv),
            description=f"Cross-query enrichment ({vl_id})",
            tags={"vl": vl_id, "type": "llm", "lesson_id": lesson_id},
        )

    # ── VL Synthesis (merge all enriched analysis_b results) ──

    graph.add_transform(
        f"{lid}:analysis_merge",
        input_names=[f"{lid}:cross_query:{vl_id}" for vl_id in vl_ids],
        transform=_make_analysis_merge_transform(db, vl_ids, cache_prefix),
        description=f"VL synthesis (merge): {title}",
        tags={"lesson_id": lesson_id},
    )

    # ── Five parallel booklet templates + booklets (one per VL) ──

    for vl_id in vl_ids:
        graph.add_transform(
            f"{lid}:booklet_template:{vl_id}",
            input_names=[
                f"{lid}:analysis_merge",
                "src:booklet_config",
                "src:query_types",
            ],
            transform=_make_booklet_template_transform(db, title, lesson_id, vl_id, cache_prefix),
            description=f"Booklet template ({vl_id}): {title}",
            tags={"lesson_id": lesson_id},
        )

        booklet_node = f"{lid}:booklet:{vl_id}"
        graph.add_transform(
            booklet_node,
            input_names=[
                f"{lid}:booklet_template:{vl_id}",
                "src:query_types",
            ],
            transform=_make_booklet_transform(db, vl_id, cache_prefix, booklet_node),
            description=f"Booklet ({vl_id}): {title}",
            tags={"vl": vl_id, "type": "llm", "lesson_id": lesson_id},
        )

    return graph


def build_book_pipeline(
    db,
    lesson_entries: Optional[list[dict]] = None,
    framework_version: Optional[str] = None,
) -> PipelineGraph:
    """Build the full book pipeline for all registered lessons.

    If lesson_entries is None, loads from the ``pipeline:book_lessons`` cache key.
    Each entry must have ``lesson_id`` and ``title``.

    The returned graph includes per-lesson subgraphs plus a final ``book``
    synthesis node that depends on all lessons' booklets.
    """
    w = _web()

    # Load lesson registry if not provided
    if lesson_entries is None:
        lesson_entries = get_book_lessons(db)

    if not lesson_entries:
        # Return an empty graph if no lessons registered
        return PipelineGraph()

    fv = framework_version
    prompts_dir, resolved_fv = w._resolve_prompts_dir(fv)
    vl_ids = sorted(w.VL_BOOKLET_INTROS.keys())

    graph = PipelineGraph()

    # ── Shared source nodes (prompt files, config — same for all lessons) ──

    graph.add_source(
        "src:analysis_a_prompt",
        file_hasher(prompts_dir / "pass_a.md"),
        description="Analysis A prompt template",
    )
    graph.add_source(
        "src:analysis_a_schema",
        file_hasher(w.DATA_DIR / "pass_a.schema.json"),
        description="Analysis A JSON schema",
    )
    graph.add_source(
        "src:analysis_b_prompt",
        file_hasher(prompts_dir / "pass_b.md"),
        description="Analysis B base prompt",
    )
    graph.add_source(
        "src:analysis_b_schema",
        file_hasher(_private_data_dir() / "pass_b.schema.json"),
        description="Analysis B JSON schema",
    )
    graph.add_source(
        "src:frameworks",
        file_hasher(prompts_dir / "frameworks.json"),
        description="Framework registry",
    )
    graph.add_source(
        "src:booklet_config",
        dict_hasher({
            "intros": w.VL_BOOKLET_INTROS,
        }),
        description="Booklet intros config",
    )
    graph.add_source(
        "src:query_types",
        dict_hasher(w.BOOKLET_QUERY_TYPES),
        description="Booklet query type definitions",
    )

    # ── Per-lesson nodes ──

    all_booklet_nodes = []

    for entry in lesson_entries:
        lesson_id = entry["lesson_id"]
        title = entry["title"]
        lid = lesson_id
        cache_prefix = f"pipeline:{lid}"

        # Lesson source
        graph.add_source(
            f"{lid}:src:lesson",
            db_lesson_hasher(db, lesson_id),
            description=f"Lesson: {title}",
            tags={"lesson_id": lesson_id},
        )

        # Analysis A
        graph.add_transform(
            f"{lid}:analysis_a",
            input_names=[
                f"{lid}:src:lesson",
                "src:analysis_a_prompt",
                "src:analysis_a_schema",
                "src:frameworks",
            ],
            transform=_make_analysis_a_transform(db, lesson_id, cache_prefix, fv),
            description=f"Analysis A: {title}",
            tags={"type": "llm", "lesson_id": lesson_id},
        )

        # Five parallel Analysis B
        for vl_id in vl_ids:
            graph.add_transform(
                f"{lid}:analysis_b:{vl_id}",
                input_names=[
                    f"{lid}:src:lesson",
                    f"{lid}:analysis_a",
                    "src:analysis_b_prompt",
                    "src:frameworks",
                    "src:analysis_b_schema",
                ],
                transform=_make_analysis_b_transform(db, lesson_id, vl_id, cache_prefix, fv),
                description=f"Analysis B ({vl_id}): {title}",
                tags={"vl": vl_id, "type": "llm", "lesson_id": lesson_id},
            )

        # Five parallel Cross-Query Enrichment
        for vl_id in vl_ids:
            graph.add_transform(
                f"{lid}:cross_query:{vl_id}",
                input_names=[f"{lid}:analysis_b:{vid}" for vid in vl_ids],
                transform=_make_cross_query_transform(db, lesson_id, vl_id, cache_prefix, fv),
                description=f"Cross-query enrichment ({vl_id})",
                tags={"vl": vl_id, "type": "llm", "lesson_id": lesson_id},
            )

        # VL Synthesis (merge)
        graph.add_transform(
            f"{lid}:analysis_merge",
            input_names=[f"{lid}:cross_query:{vl_id}" for vl_id in vl_ids],
            transform=_make_analysis_merge_transform(db, vl_ids, cache_prefix),
            description=f"VL synthesis (merge): {title}",
            tags={"lesson_id": lesson_id},
        )

        # Five parallel booklet templates + booklets
        for vl_id in vl_ids:
            graph.add_transform(
                f"{lid}:booklet_template:{vl_id}",
                input_names=[
                    f"{lid}:analysis_merge",
                    "src:booklet_config",
                    "src:query_types",
                ],
                transform=_make_booklet_template_transform(db, title, lesson_id, vl_id, cache_prefix),
                description=f"Booklet template ({vl_id}): {title}",
                tags={"lesson_id": lesson_id},
            )

            booklet_node = f"{lid}:booklet:{vl_id}"
            graph.add_transform(
                booklet_node,
                input_names=[
                    f"{lid}:booklet_template:{vl_id}",
                    "src:query_types",
                ],
                transform=_make_booklet_transform(db, vl_id, cache_prefix, booklet_node),
                description=f"Booklet ({vl_id}): {title}",
                tags={"vl": vl_id, "type": "llm", "lesson_id": lesson_id},
            )
            all_booklet_nodes.append(booklet_node)

    # ── Book synthesis (combine all lessons' booklets) ──

    graph.add_transform(
        "book",
        input_names=all_booklet_nodes,
        transform=_make_book_synthesis_transform(db, lesson_entries, vl_ids),
        description="Book synthesis (all lessons)",
    )

    return graph


# ── Lesson registry helpers ──────────────────────────────────────────────

BOOK_LESSONS_CACHE_KEY = "pipeline:book_lessons"


def get_book_lessons(db) -> list[dict]:
    """Load the ordered list of book lessons from cache.

    Returns list of dicts with at least ``lesson_id`` and ``title``.
    """
    data = db.get_cache(BOOK_LESSONS_CACHE_KEY)
    if data and isinstance(data, dict) and "lessons" in data:
        return data["lessons"]
    return []


def set_book_lessons(db, lessons: list[dict]):
    """Save the ordered list of book lessons to cache.

    Wraps in a dict because the cache table stores JSONB and get_cache
    returns a dict.
    """
    db.set_cache(BOOK_LESSONS_CACHE_KEY, {"lessons": lessons})
