"""Book pipeline graph definition.

Wires up ~40 nodes for the 5-lesson book generation pipeline.
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


def _web():
    """Lazy import of web module to avoid heavyweight init at graph construction."""
    try:
        import web as m
    except ImportError:
        from src.kb import web as m
    return m


# ── Transform factories ─────────────────────────────────────────────────


def _make_analysis_a_transform(db, lesson_id: str, vl_id: str, fv: Optional[str]):
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
        db.set_cache(f"pipeline:analysis_a:{vl_id}", result)

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

        # Read Analysis A result from pipeline cache
        pass_a_result = db.get_cache(f"pipeline:analysis_a:{vl_id}")
        if not pass_a_result:
            raise ValueError(f"Analysis A result not found in pipeline cache for {vl_id}")

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


def _make_booklet_render_transform(db, vl_id: str, title: str):
    """Render booklet HTML from cached analysis data (non-LLM)."""
    def transform(**_kwargs) -> str:
        w = _web()

        pass_a = db.get_cache(f"pipeline:analysis_a:{vl_id}")
        pass_b = db.get_cache(f"pipeline:analysis_b:{vl_id}")
        if not pass_a or not pass_b:
            raise ValueError(f"Analysis data not found for {vl_id}")

        combined = {
            "pass_a_result": pass_a,
            "vl_analyses": {vl_id: {"status": "complete", "result": pass_b}},
        }

        html = w.build_booklet_html(combined, lesson_title=title, framework=vl_id)

        db.set_cache(f"pipeline:booklet:{vl_id}", {"html": html})
        return html

    return transform


def _make_fill_queries_transform(db, vl_id: str):
    """Fill LLM query blocks in a booklet via QueryAgent."""
    def transform(**_kwargs) -> str:
        w = _web()
        try:
            from query_agent import QueryAgent
        except ImportError:
            from src.kb.query_agent import QueryAgent

        booklet_data = db.get_cache(f"pipeline:booklet:{vl_id}")
        if not booklet_data or "html" not in booklet_data:
            raise ValueError(f"Booklet HTML not found for {vl_id}")

        html = booklet_data["html"]
        agent = QueryAgent(
            llm=w.get_llm(w.DEFAULT_LLM),
            cache_dir=str(w.DATA_DIR / "cache" / "query_agent"),
            max_review_cycles=0,
        )
        filled = agent.fill(html)

        db.set_cache(f"pipeline:fill_queries:{vl_id}", {"html": filled})
        return filled

    return transform


def _make_volume_intro_transform(db):
    """Generate the volume introduction (query block + LLM fill)."""
    def transform(**_kwargs) -> str:
        w = _web()
        try:
            from query_agent import QueryAgent
        except ImportError:
            from src.kb.query_agent import QueryAgent

        # Build lens and lesson context for the query block
        lenses = []
        for vl_id in sorted(w.VL_BOOKLET_INTROS.keys()):
            info = w.VL_BOOKLET_INTROS[vl_id]
            lenses.append({
                "id": vl_id,
                "name": info["name"],
                "subtitle": info["subtitle"],
                "lens_description": info["lens"],
            })

        lesson_entries = []
        for vl_id in sorted(w.BOOK_LESSONS.keys()):
            entry = w.BOOK_LESSONS[vl_id]
            if entry:
                lesson_entries.append({
                    "title": entry["title"],
                    "framework_id": vl_id,
                    "framework_name": w.VL_BOOKLET_INTROS[vl_id]["name"],
                })

        volume_context = {"lenses": lenses, "lessons": lesson_entries}
        volume_query = w.booklet_query_block("volume-intro", volume_context)

        agent = QueryAgent(
            llm=w.get_llm(w.DEFAULT_LLM),
            cache_dir=str(w.DATA_DIR / "cache" / "query_agent"),
            max_review_cycles=0,
        )
        filled = agent.fill(volume_query)

        db.set_cache("pipeline:volume_intro", {"html": filled})
        return filled

    return transform


def _make_book_assembly_transform(db):
    """Assemble the full book from volume intro + filled booklets."""
    def transform(**_kwargs) -> str:
        w = _web()

        # Gather all pieces
        intro_data = db.get_cache("pipeline:volume_intro")
        intro_html = intro_data.get("html", "") if intro_data else ""

        # Fixed council section (no LLM, rendered inline)
        council_html = w.render_council_section()

        booklet_parts = []
        for vl_id in sorted(w.BOOK_LESSONS.keys()):
            data = db.get_cache(f"pipeline:fill_queries:{vl_id}")
            if data and "html" in data:
                booklet_parts.append(
                    f'<div class="booklet-chapter" data-vl="{vl_id}">\n'
                    f'{data["html"]}\n'
                    f'</div>'
                )

        book_html = intro_html + "\n\n" + council_html + "\n\n" + "\n\n".join(booklet_parts)
        db.set_cache("pipeline:book", {"html": book_html})
        return book_html

    return transform


def _make_review_transform(db):
    """Run book review using ReviewAgent on the assembled book content.

    The ReviewAgent normally fetches via HTTP. For the pipeline, we feed it
    each chapter's HTML directly via review_chapter(html=...) and run
    a gross review from chapter summaries.
    """
    def transform(**_kwargs) -> str:
        w = _web()
        try:
            from review_agent import ReviewAgent, BookReview, format_report
        except ImportError:
            from src.kb.review_agent import ReviewAgent, BookReview, format_report

        llm = w.get_llm(w.DEFAULT_LLM)
        agent = ReviewAgent(llm=llm)

        # Collect chapter data from pipeline cache
        intro_data = db.get_cache("pipeline:volume_intro")
        intro_text = ""
        if intro_data and "html" in intro_data:
            intro_text = agent._html_to_text(intro_data["html"])

        chapters_data = []
        for vl_id in sorted(w.BOOK_LESSONS.keys()):
            data = db.get_cache(f"pipeline:fill_queries:{vl_id}")
            if data and "html" in data:
                html = data["html"]
                text = agent._html_to_text(agent._strip_style(html))
                summary = text[:2000]
                if len(text) > 2000:
                    summary += "\n[... truncated for summary ...]"
                info = w.VL_BOOKLET_INTROS[vl_id]
                entry = w.BOOK_LESSONS[vl_id]
                chapters_data.append({
                    "title": entry["title"],
                    "lens": info["name"],
                    "lens_description": info["lens"],
                    "html": html,
                    "text": text,
                    "summary": summary,
                })

        # Gross review
        gross = agent._gross_review(intro_text, chapters_data)
        review = BookReview(provider=llm.provider, gross=gross)

        # Per-chapter fine review
        for ch in chapters_data:
            chapter_review = agent.review_chapter(
                html=ch["html"], title=ch["title"], lens=ch["lens"]
            )
            review.chapters.append(chapter_review)

        report = format_report(review)
        db.set_cache("pipeline:review", {"report": report})
        return report

    return transform


# ── Graph builder ────────────────────────────────────────────────────────


def build_book_pipeline(db, framework_version: Optional[str] = None) -> PipelineGraph:
    """Build the complete pipeline graph for the 5-lesson book.

    Args:
        db: Database instance
        framework_version: Version string (e.g. "v1"). None uses current from frameworks.json.
    """
    w = _web()
    fv = framework_version
    prompts_dir, resolved_fv = w._resolve_prompts_dir(fv)

    graph = PipelineGraph()

    # ── Shared source nodes ──

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
    graph.add_source(
        "src:council_intro",
        file_hasher(w.DATA_DIR / "council_intro.html"),
        description="Council of Five intro section",
    )

    # ── Per-lesson source + transform nodes ──

    # Resolve VL → prompt file mapping from framework registry
    frameworks = w.load_frameworks(fv)
    vl_prompt_map = {f["id"]: f["prompt_file"] for f in frameworks}

    for vl_id, lesson_info in sorted(w.BOOK_LESSONS.items()):
        lesson_id = lesson_info["lesson_id"]
        title = lesson_info["title"]
        vl_file = vl_prompt_map.get(vl_id, f"{vl_id.lower().replace('-', '')}.md")

        # Source: lesson transcription from DB
        graph.add_source(
            f"src:lesson:{vl_id}",
            db_lesson_hasher(db, lesson_id),
            description=f"Lesson: {title}",
            tags={"vl": vl_id, "lesson_id": lesson_id},
        )

        # Source: VL-specific prompt file
        graph.add_source(
            f"src:vl_prompt:{vl_id}",
            file_hasher(prompts_dir / vl_file),
            description=f"VL prompt: {vl_file}",
            tags={"vl": vl_id},
        )

        # Transform: Analysis A (segmentation + classification + decomposition)
        graph.add_transform(
            f"analysis_a:{vl_id}",
            input_names=[
                f"src:lesson:{vl_id}",
                "src:analysis_a_prompt",
                "src:analysis_a_schema",
                "src:frameworks",
            ],
            transform=_make_analysis_a_transform(db, lesson_id, vl_id, fv),
            description=f"Analysis A: {title}",
            tags={"vl": vl_id, "type": "llm"},
        )

        # Transform: Analysis B (VL-specific synthesis)
        graph.add_transform(
            f"analysis_b:{vl_id}",
            input_names=[
                f"src:lesson:{vl_id}",
                f"analysis_a:{vl_id}",
                "src:analysis_b_prompt",
                f"src:vl_prompt:{vl_id}",
                "src:analysis_b_schema",
            ],
            transform=_make_analysis_b_transform(db, lesson_id, vl_id, fv),
            description=f"Analysis B: {title}",
            tags={"vl": vl_id, "type": "llm"},
        )

        # Transform: Render booklet HTML
        graph.add_transform(
            f"booklet:{vl_id}",
            input_names=[
                f"analysis_a:{vl_id}",
                f"analysis_b:{vl_id}",
                "src:booklet_config",
            ],
            transform=_make_booklet_render_transform(db, vl_id, title),
            description=f"Render booklet: {title}",
            tags={"vl": vl_id},
        )

        # Transform: Fill LLM query blocks
        graph.add_transform(
            f"fill_queries:{vl_id}",
            input_names=[
                f"booklet:{vl_id}",
                "src:query_types",
            ],
            transform=_make_fill_queries_transform(db, vl_id),
            description=f"Fill queries: {title}",
            tags={"vl": vl_id, "type": "llm"},
        )

    # ── Assembly nodes ──

    graph.add_transform(
        "volume_intro",
        input_names=["src:booklet_config", "src:query_types"],
        transform=_make_volume_intro_transform(db),
        description="Volume introduction",
        tags={"type": "llm"},
    )

    book_inputs = ["volume_intro", "src:council_intro"] + [
        f"fill_queries:{vl}" for vl in sorted(w.BOOK_LESSONS.keys())
    ]
    graph.add_transform(
        "book",
        input_names=book_inputs,
        transform=_make_book_assembly_transform(db),
        description="Book assembly",
    )

    graph.add_transform(
        "review",
        input_names=["book"],
        transform=_make_review_transform(db),
        description="Book review",
        tags={"type": "llm"},
    )

    return graph
