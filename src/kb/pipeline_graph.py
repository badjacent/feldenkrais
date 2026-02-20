"""Book pipeline graph definition.

Single-lesson pipeline: one lesson analysed through all 5 VL agents,
enriched via cross-VL queries, merged via a VL-synthesis node, then
rendered as a single booklet.

Pipeline shape:
    src:lesson → analysis_a → analysis_b:{VL-1..5} → cross_query:{VL-1..5}
                                                        → vl_synthesis → booklet_template → booklet → book

Each transform is a factory closure that does its actual work by reading
from DB/files/pipeline cache, and writes results to both pipeline
namespace and legacy cache keys for /booklet endpoint compatibility.
"""

import hashlib
import json
from pathlib import Path
from typing import Callable, Optional

try:
    from pipeline import PipelineGraph
except ImportError:
    from .pipeline import PipelineGraph


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
    try:
        import web as m
    except ImportError:
        from src.kb import web as m
    return m


# ── Transform factories ─────────────────────────────────────────────────


def _make_analysis_a_transform(db, lesson_id: str, fv: Optional[str]):
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
        db.set_cache("pipeline:analysis_a", result)

        return json.dumps(result, sort_keys=True)

    return transform


def _make_analysis_b_transform(db, lesson_id: str, vl_id: str, fv: Optional[str]):
    """Analysis B: VL-specific synthesis (somatic narrative + analytical table)."""
    def transform(**_kwargs) -> str:
        w = _web()

        text = db.get_document_transcription(lesson_id)
        if not text:
            raise ValueError(f"No transcription for lesson {lesson_id}")
        text = text.strip()

        # Read Analysis A result from pipeline cache (single, shared)
        pass_a_result = db.get_cache("pipeline:analysis_a")
        if not pass_a_result:
            raise ValueError("Analysis A result not found in pipeline cache")

        # Run Pass B for this specific VL framework
        result = w.run_pass_b(text, pass_a_result, vl_id, framework_version=fv)

        # Store in pipeline namespace
        db.set_cache(f"pipeline:analysis_b:{vl_id}", result)

        # Write combined result to legacy cache so /booklet endpoint works
        combined = {
            "pass_a_result": pass_a_result,
            "vl_analyses": {vl_id: {"status": "complete", "result": result}},
        }
        legacy_key = w.get_cache_key(text)
        db.set_cache(legacy_key, combined)

        return json.dumps(result, sort_keys=True)

    return transform


def _make_cross_query_transform(db, lesson_id: str, vl_id: str, fv: Optional[str]):
    """Cross-query enrichment: VL reviews peer analyses and asks focused follow-ups."""
    def transform(**_kwargs) -> str:
        w = _web()
        try:
            from cross_query import run_cross_query_pass
        except ImportError:
            from src.kb.cross_query import run_cross_query_pass

        text = db.get_document_transcription(lesson_id)
        if not text:
            raise ValueError(f"No transcription for lesson {lesson_id}")
        text = text.strip()

        frameworks = w.load_frameworks(fv)

        # Load Pass B schema
        schema_path = w.DATA_DIR / "pass_b.schema.json"
        pass_b_schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}

        llm = w.get_llm()

        result = run_cross_query_pass(
            llm, vl_id, db, text, frameworks, pass_b_schema,
        )

        return json.dumps(result, sort_keys=True)

    return transform


def _make_vl_synthesis_transform(db, vl_ids: list[str]):
    """Merge all 5 VL analysis_b results into a single synthesis object.

    Placeholder logic: packages the results into a combined dict.
    """
    def transform(**_kwargs) -> str:
        pass_a = db.get_cache("pipeline:analysis_a")
        if not pass_a:
            raise ValueError("Analysis A result not found in pipeline cache")

        vl_analyses = {}
        for vl_id in vl_ids:
            result = db.get_cache(f"pipeline:analysis_b:{vl_id}")
            if result:
                vl_analyses[vl_id] = {"status": "complete", "result": result}

        combined = {"pass_a_result": pass_a, "vl_analyses": vl_analyses}
        db.set_cache("pipeline:vl_synthesis", combined)
        return json.dumps(combined, sort_keys=True)

    return transform


def _make_booklet_template_transform(db, title: str, lesson_id: str):
    """Render booklet HTML template from VL synthesis data (non-LLM).

    Reads the synthesis output and uses the primary framework from pass_a
    to render a single-lens booklet. Produces HTML with unfilled query blocks.
    """
    def transform(**_kwargs) -> str:
        w = _web()

        synthesis = db.get_cache("pipeline:vl_synthesis")
        if not synthesis:
            raise ValueError("VL synthesis data not found")

        # Use primary framework from pass_a to pick the VL for rendering
        pass_a = synthesis.get("pass_a_result", {})
        primary_vl = pass_a.get("primary_framework", "VL-1")

        # Get source_url from document metadata
        source_url = ""
        doc = db.get_document(lesson_id)
        if doc:
            source_url = doc.get("source_url", "") or ""

        html = w.build_booklet_html(
            synthesis,
            lesson_title=title,
            framework=primary_vl,
            source_url=source_url,
            original_title=title,
        )

        db.set_cache("pipeline:booklet_template", {"html": html})
        return html

    return transform


def _make_booklet_transform(db):
    """Fill LLM query blocks in the single booklet template via QueryAgent.

    This is the final booklet node — produces the complete, filled booklet.
    """
    def transform(**_kwargs) -> str:
        w = _web()
        try:
            from query_agent import QueryAgent
        except ImportError:
            from src.kb.query_agent import QueryAgent

        template_data = db.get_cache("pipeline:booklet_template")
        if not template_data or "html" not in template_data:
            raise ValueError("Booklet template not found")

        html = template_data["html"]
        # Check node metadata for model override
        llm_key = w.DEFAULT_LLM
        meta = db.get_cache("pipeline_meta:booklet")
        if meta and meta.get("llm"):
            llm_key = meta["llm"]
            print(f"[booklet] Using model override: {llm_key}", flush=True)
        agent = QueryAgent(
            llm=w.get_llm(llm_key),
            max_review_cycles=0,
        )
        filled = agent.fill(html)

        db.set_cache("pipeline:booklet", {"html": filled})
        return filled

    return transform


def _make_book_assembly_transform(db):
    """Assemble the book from the single filled booklet."""
    def transform(**_kwargs) -> str:
        data = db.get_cache("pipeline:booklet")
        if not data or "html" not in data:
            raise ValueError("Booklet not found in pipeline cache")

        book_html = data["html"]
        db.set_cache("pipeline:book", {"html": book_html})
        return book_html

    return transform


# ── Graph builder ────────────────────────────────────────────────────────


def build_book_pipeline(db, framework_version: Optional[str] = None) -> PipelineGraph:
    """Build the single-lesson pipeline: one lesson through all 5 VL agents.

    Pipeline shape:
        src:lesson → analysis_a → analysis_b:{VL-1..5} → cross_query:{VL-1..5}
                                                            → vl_synthesis → booklet_template → booklet → book

    Args:
        db: Database instance
        framework_version: Version string (e.g. "v1"). None uses current from frameworks.json.
    """
    w = _web()
    fv = framework_version
    prompts_dir, resolved_fv = w._resolve_prompts_dir(fv)

    # Single lesson definition (The Anti-Gravity Lesson, formerly VL-1's lesson)
    PIPELINE_LESSON = {
        "lesson_id": "abfddaf1-e837-4ed5-9d59-6e53056eb759",
        "title": "The Anti-Gravity Lesson",
        "category": "db:4",
    }
    lesson_id = PIPELINE_LESSON["lesson_id"]
    title = PIPELINE_LESSON["title"]

    # All 5 VL IDs for the parallel analysis_b fan-out
    vl_ids = sorted(w.VL_BOOKLET_INTROS.keys())

    graph = PipelineGraph()

    # ── Source nodes ──

    graph.add_source(
        "src:lesson",
        db_lesson_hasher(db, lesson_id),
        description=f"Lesson: {title}",
        tags={"lesson_id": lesson_id},
    )
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
        file_hasher(w.DATA_DIR / "pass_b.schema.json"),
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
            "lessons": w.BOOK_LESSONS,
        }),
        description="Booklet intros + lesson selections",
    )
    graph.add_source(
        "src:query_types",
        dict_hasher(w.BOOKLET_QUERY_TYPES),
        description="Booklet query type definitions",
    )

    # ── Single Analysis A ──

    graph.add_transform(
        "analysis_a",
        input_names=[
            "src:lesson",
            "src:analysis_a_prompt",
            "src:analysis_a_schema",
            "src:frameworks",
        ],
        transform=_make_analysis_a_transform(db, lesson_id, fv),
        description=f"Analysis A: {title}",
        tags={"type": "llm"},
    )

    # ── Five parallel Analysis B nodes (one per VL) ──

    for vl_id in vl_ids:
        graph.add_transform(
            f"analysis_b:{vl_id}",
            input_names=[
                "src:lesson",
                "analysis_a",
                "src:analysis_b_prompt",
                "src:frameworks",
                "src:analysis_b_schema",
            ],
            transform=_make_analysis_b_transform(db, lesson_id, vl_id, fv),
            description=f"Analysis B ({vl_id}): {title}",
            tags={"vl": vl_id, "type": "llm"},
        )

    # ── Five parallel Cross-Query Enrichment nodes (one per VL) ──
    # Each depends on ALL 5 analysis_b nodes so all first-pass results are available.

    for vl_id in vl_ids:
        graph.add_transform(
            f"cross_query:{vl_id}",
            input_names=[f"analysis_b:{vid}" for vid in vl_ids],
            transform=_make_cross_query_transform(db, lesson_id, vl_id, fv),
            description=f"Cross-query enrichment ({vl_id})",
            tags={"vl": vl_id, "type": "llm"},
        )

    # ── VL Synthesis (merge all enriched analysis_b results) ──

    graph.add_transform(
        "vl_synthesis",
        input_names=[f"cross_query:{vl_id}" for vl_id in vl_ids],
        transform=_make_vl_synthesis_transform(db, vl_ids),
        description="VL synthesis (merge all analyses)",
    )

    # ── Single booklet template + booklet ──

    graph.add_transform(
        "booklet_template",
        input_names=[
            "vl_synthesis",
            "src:booklet_config",
            "src:query_types",
        ],
        transform=_make_booklet_template_transform(db, title, lesson_id),
        description=f"Booklet template: {title}",
    )

    graph.add_transform(
        "booklet",
        input_names=[
            "booklet_template",
            "src:query_types",
        ],
        transform=_make_booklet_transform(db),
        description=f"Booklet: {title}",
        tags={"type": "llm"},
    )

    # ── Book assembly (just wraps the single booklet) ──

    graph.add_transform(
        "book",
        input_names=["booklet"],
        transform=_make_book_assembly_transform(db),
        description="Book assembly",
    )

    return graph
