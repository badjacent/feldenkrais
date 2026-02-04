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


def _make_booklet_template_transform(db, vl_id: str, title: str, lesson_id: str):
    """Render booklet HTML template from cached analysis data (non-LLM).

    Produces HTML with unfilled query blocks that will be filled by the booklet node.
    """
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

        # Get source_url from document metadata
        source_url = ""
        doc = db.get_document(lesson_id)
        if doc:
            source_url = doc.get("source_url", "") or ""

        html = w.build_booklet_html(
            combined,
            lesson_title=title,
            framework=vl_id,
            source_url=source_url,
            original_title=title,
        )

        db.set_cache(f"pipeline:booklet_template:{vl_id}", {"html": html})
        return html

    return transform


def _make_booklet_transform(db, vl_id: str):
    """Fill LLM query blocks in a booklet template via QueryAgent.

    This is the final booklet node - produces the complete, filled booklet.
    """
    def transform(**_kwargs) -> str:
        w = _web()
        try:
            from query_agent import QueryAgent
        except ImportError:
            from src.kb.query_agent import QueryAgent

        template_data = db.get_cache(f"pipeline:booklet_template:{vl_id}")
        if not template_data or "html" not in template_data:
            raise ValueError(f"Booklet template not found for {vl_id}")

        html = template_data["html"]
        # Check node metadata for model override
        llm_key = w.DEFAULT_LLM
        meta = db.get_cache(f"pipeline_meta:booklet:{vl_id}")
        if meta and meta.get("llm"):
            llm_key = meta["llm"]
            print(f"[booklet:{vl_id}] Using model override: {llm_key}", flush=True)
        agent = QueryAgent(
            llm=w.get_llm(llm_key),
            max_review_cycles=0,
        )
        filled = agent.fill(html)

        db.set_cache(f"pipeline:booklet:{vl_id}", {"html": filled})
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
            data = db.get_cache(f"pipeline:booklet:{vl_id}")
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

        llm = w.get_llm("gemini-review")
        agent = ReviewAgent(llm=llm)

        # Collect chapter data from pipeline cache
        intro_data = db.get_cache("pipeline:volume_intro")
        intro_text = ""
        if intro_data and "html" in intro_data:
            intro_text = agent._html_to_text(intro_data["html"])

        # Skip VL-4 — the chapter is being reworked and its review
        # feedback currently pulls the overall review off-track.
        skip_vl_ids = {"VL-4"}

        chapters_data = []
        for vl_id in sorted(w.BOOK_LESSONS.keys()):
            if vl_id in skip_vl_ids:
                continue
            data = db.get_cache(f"pipeline:booklet:{vl_id}")
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
        _save_with_history(db, "pipeline:review", {"report": report})
        return report

    return transform


def _make_review_summary_transform(db):
    """Summarize review into ranked actionable issues.

    Takes the review report and produces a ranked list of the top 10 issues
    that could improve the book, with context about what's changeable.
    """
    def transform(**_kwargs) -> str:
        w = _web()

        review_data = db.get_cache("pipeline:review")
        if not review_data or "report" not in review_data:
            raise ValueError("Review report not found")

        report = review_data["report"]

        # Build context about book composition
        lens_info = []
        for vl_id in sorted(w.VL_BOOKLET_INTROS.keys()):
            info = w.VL_BOOKLET_INTROS[vl_id]
            lens_info.append(f"- {vl_id}: {info['name']} ({info['subtitle']})")

        query_types = list(w.BOOKLET_QUERY_TYPES.keys())

        composition_context = f"""
The book is generated by a pipeline with these components:

STRUCTURE:
- Volume introduction (LLM-generated via 'volume-intro' query type)
- Council of Five section (static HTML in data/council_intro.html)
- Five booklets, one per analytical lens:
{chr(10).join(lens_info)}

CONFIGURABLE COMPONENTS (can be changed via prompts/config):
- Query type prompts in BOOKLET_QUERY_TYPES: {', '.join(query_types)}
- VL framework definitions in data/frameworks/v2/frameworks.json
- Individual VL prompts in data/frameworks/v2/vl1.md through vl5.md
- Pass A prompt (segmentation/classification) in data/frameworks/v2/pass_a.md
- Pass B prompt (synthesis) in data/frameworks/v2/pass_b.md

EACH BOOKLET CONTAINS:
- Cover with title and lens info
- "How Feldenkrais Lessons Work" section (static)
- Journey section (LLM-filled, uses 'booklet-journey' query type)
- Arc section with analytical table (LLM-filled, uses 'booklet-arc' query type)
- The Lens section (describes the VL perspective)
- Next Time section (LLM-filled, uses 'booklet-reflection' query type)
"""

        prompt = f"""You are analyzing a book review to identify actionable improvements.

{composition_context}

REVIEW REPORT:
{report}

Based on this review, identify the TOP 10 issues that, if addressed, would most improve the book.

For each issue, provide:
1. **Issue**: Clear description of the problem
2. **Component**: Which part of the system it relates to (e.g., "booklet-reflection prompt", "VL-3 analysis", "volume-intro", "council section")
3. **Impact**: High / Medium / Low - how much would fixing this improve the reader experience?
4. **Actionable**: Yes / No - can this be fixed by changing prompts, config, or templates?
5. **Fix sketch**: If actionable, a brief description of what change might help

Rank by impact (highest first). Focus on issues that are ACTIONABLE through the configurable components listed above.

Return as a JSON object with this structure:
{{
  "issues": [
    {{
      "rank": 1,
      "issue": "...",
      "component": "...",
      "impact": "high|medium|low",
      "actionable": true|false,
      "fix_sketch": "..."
    }},
    ...
  ],
  "summary": "2-3 sentence overall assessment"
}}
"""

        llm = w.get_llm("gemini-review")
        result = llm.complete_structured_json(prompt, {
            "type": "object",
            "properties": {
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rank": {"type": "integer"},
                            "issue": {"type": "string"},
                            "component": {"type": "string"},
                            "impact": {"type": "string"},
                            "actionable": {"type": "boolean"},
                            "fix_sketch": {"type": "string"},
                        },
                        "required": ["rank", "issue", "component", "impact", "actionable"],
                    },
                },
                "summary": {"type": "string"},
            },
            "required": ["issues", "summary"],
        })

        _save_with_history(db, "pipeline:review_summary", result)
        return json.dumps(result, indent=2)

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
    graph.add_source(
        "src:review_agent",
        file_hasher(Path(__file__).parent / "review_agent.py"),
        description="Review agent prompts",
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

        # Transform: Render booklet template (unfilled HTML with query blocks)
        graph.add_transform(
            f"booklet_template:{vl_id}",
            input_names=[
                f"analysis_a:{vl_id}",
                f"analysis_b:{vl_id}",
                "src:booklet_config",
                "src:query_types",
            ],
            transform=_make_booklet_template_transform(db, vl_id, title, lesson_id),
            description=f"Booklet template: {title}",
            tags={"vl": vl_id},
        )

        # Transform: Fill query blocks to produce finished booklet
        graph.add_transform(
            f"booklet:{vl_id}",
            input_names=[
                f"booklet_template:{vl_id}",
                "src:query_types",
            ],
            transform=_make_booklet_transform(db, vl_id),
            description=f"Booklet: {title}",
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
        f"booklet:{vl}" for vl in sorted(w.BOOK_LESSONS.keys())
    ]
    graph.add_transform(
        "book",
        input_names=book_inputs,
        transform=_make_book_assembly_transform(db),
        description="Book assembly",
    )

    graph.add_transform(
        "review",
        input_names=["book", "src:review_agent"],
        transform=_make_review_transform(db),
        description="Book review",
        tags={"type": "llm"},
    )

    graph.add_transform(
        "review_summary",
        input_names=["review"],
        transform=_make_review_summary_transform(db),
        description="Review summary (ranked issues)",
        tags={"type": "llm"},
    )

    return graph
