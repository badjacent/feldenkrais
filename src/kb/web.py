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

import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from llm import LLM

app = FastAPI(title="Feldenkrais Lesson Analyzer")

# Paths - handle both local development and Lambda environment
IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
if IS_LAMBDA:
    # In Lambda, data files are packaged alongside the code
    DATA_DIR = Path(__file__).parent / "data"
else:
    # Local development
    DATA_DIR = Path(__file__).parent.parent.parent / "data"
LESSONS_DIR = DATA_DIR / "lessons"

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


def load_pass_a() -> tuple[str, dict]:
    """Load Pass A prompt and schema (Decomposition + Routing)."""
    prompt_path = DATA_DIR / "pass_a.md"
    schema_path = DATA_DIR / "pass_a.schema.json"

    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}

    return prompt, schema


def load_pass_b(framework: str) -> tuple[str, dict]:
    """Load Pass B prompt and schema for a specific VL framework.

    Pass B receives Pass A output and applies the VL-specific lens.
    """
    # Load base Pass B protocol
    base_path = DATA_DIR / "pass_b.md"
    base_protocol = base_path.read_text() if base_path.exists() else ""

    # Load VL-specific module
    vl_map = {
        "VL-1": "vl1.md",
        "VL-2": "vl2.md",
        "VL-3": "vl3.md",
        "VL-4": "vl4.md",
        "VL-5": "vl5.md",
    }
    vl_file = vl_map.get(framework, "vl1.md")
    vl_path = DATA_DIR / vl_file
    vl_module = vl_path.read_text() if vl_path.exists() else ""

    # Combine: Pass B base + VL-specific framework
    protocol = base_protocol + "\n\n---\n\n## Framework: " + framework + "\n\n" + vl_module

    # Pass B schema
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


# Threshold for running a VL analysis (score must be >= this)
VL_ANALYSIS_THRESHOLD = 0.5


def get_llm() -> LLM:
    """Get configured LLM instance."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable not set")
    return LLM(
        provider="gemini",
        model="gemini-3-flash-preview",
        api_key=api_key,
        default_max_tokens=65536,
    )


def get_available_lessons() -> dict[str, list[tuple[str, str]]]:
    """Get dict of category -> [(filename, display_name)] for available lessons."""
    categories = {}
    if LESSONS_DIR.exists():
        # Scan subdirectories
        for subdir in sorted(LESSONS_DIR.iterdir()):
            if subdir.is_dir() and not subdir.name.startswith('.'):
                lessons = []
                for f in sorted(subdir.glob("*.txt")):
                    display = f.stem.replace("_", " ").replace("-", " ").title()
                    lessons.append((f.name, display))
                if lessons:
                    # Format category name nicely
                    cat_display = subdir.name.replace("_", " ").title()
                    categories[subdir.name] = lessons
        # Also check for lessons directly in LESSONS_DIR (uncategorized)
        uncategorized = []
        for f in sorted(LESSONS_DIR.glob("*.txt")):
            display = f.stem.replace("_", " ").replace("-", " ").title()
            uncategorized.append((f.name, display))
        if uncategorized:
            categories["uncategorized"] = uncategorized
    return categories


def load_lesson(category: str, filename: str) -> str:
    """Load lesson text from file."""
    if category and category != "uncategorized":
        path = LESSONS_DIR / category / filename
    else:
        path = LESSONS_DIR / filename
    if path.exists() and path.is_file():
        return path.read_text()
    return ""


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


def run_pass_a(lesson_text: str) -> dict:
    """Run Pass A: Decomposition + Routing.

    Internally runs Pass 0 to segment text, then passes segmented text to LLM.
    Returns VL classification, SPIFFER-Q decomposed steps, AND Pass 0 segments.
    """
    # Run Pass 0 to segment the text
    pass_0_result = run_pass_0(lesson_text)
    segmented_text = format_text_with_segments(pass_0_result)

    pass_a_prompt, pass_a_schema = load_pass_a()

    if not pass_a_prompt or not pass_a_schema:
        # Return default if Pass A not configured
        return {
            "classifications": {
                "vl1_vertical_self": 1.0,
                "vl2_homunculus": 0.0,
                "vl3_steering": 0.0,
                "vl4_proximal_distal": 0.0,
                "vl5_folding": 0.0,
            },
            "primary_framework": "VL-1",
            "rationale": "Pass A not configured, defaulting to VL-1",
            "steps": [],
            "pass_0": pass_0_result,
        }

    prompt = pass_a_prompt + "\n\n---\n\n## Lesson to Analyze:\n\n" + segmented_text

    llm = get_llm()
    result = llm.complete_structured_json(
        prompt,
        pass_a_schema,
        system=ROUTER_SYSTEM_PROMPT,
    )

    # Attach Pass 0 segments to result for UI rendering
    result["pass_0"] = pass_0_result

    return result


# Alias for backwards compatibility
def run_router(lesson_text: str) -> dict:
    """Alias for run_pass_a for backwards compatibility."""
    return run_pass_a(lesson_text)


def get_active_frameworks(pass_a_result: dict, threshold: float = VL_ANALYSIS_THRESHOLD) -> list[str]:
    """Get list of VL frameworks that scored above the threshold."""
    classifications = pass_a_result.get("classifications", {})

    # Map classification keys to framework names
    key_to_framework = {
        "vl1_vertical_self": "VL-1",
        "vl2_homunculus": "VL-2",
        "vl3_steering": "VL-3",
        "vl4_proximal_distal": "VL-4",
        "vl5_folding": "VL-5",
    }

    active = []
    for key, framework in key_to_framework.items():
        score = classifications.get(key, 0)
        if score >= threshold:
            active.append((framework, score))

    # Sort by score descending, return framework names
    active.sort(key=lambda x: -x[1])
    return [f for f, s in active]


def run_pass_b(lesson_text: str, pass_a_result: dict, framework: str) -> dict:
    """Run Pass B: VL Framework Synthesis.

    Takes the original lesson text AND Pass A output, applies VL-specific lens.
    """
    protocol, schema = load_pass_b(framework)

    # Build prompt with both lesson text and Pass A output
    prompt = protocol + "\n\n---\n\n## Original Lesson Text:\n\n" + lesson_text
    prompt += "\n\n---\n\n## Pass A Analysis:\n\n```json\n" + json.dumps(pass_a_result, indent=2) + "\n```"

    llm = get_llm()
    analysis = llm.complete_structured_json(
        prompt,
        schema,
        system=ANALYSIS_SYSTEM_PROMPT,
    )
    return analysis


# Global to store Pass A result for the current request (used by run_vl_analysis alias)
_current_pass_a_result = None


def run_vl_analysis(lesson_text: str, framework: str) -> dict:
    """Alias that runs Pass B using stored Pass A result.

    This is for backwards compatibility with code that calls run_vl_analysis
    after run_pass_a has already been called.
    """
    global _current_pass_a_result
    if _current_pass_a_result is None:
        # If no Pass A result stored, run it first
        _current_pass_a_result = run_pass_a(lesson_text)
    return run_pass_b(lesson_text, _current_pass_a_result, framework)


def process_job(job_id: str) -> None:
    """Process a pending job (called by async Lambda invocation)."""
    job = load_job(job_id)
    if not job or job.get("status") != "pending":
        return

    try:
        lesson_text = job["lesson_text"]

        # Step 1: Run Pass A (Decomposition + Routing) - ONCE
        pass_a_result = run_pass_a(lesson_text)
        job["pass_a_result"] = pass_a_result

        # For backwards compatibility, also store as router_result
        job["router_result"] = pass_a_result

        # Step 2: Determine which frameworks to analyze
        active_frameworks = get_active_frameworks(pass_a_result)

        if not active_frameworks:
            # Fallback to primary if nothing above threshold
            primary = pass_a_result.get("primary_framework", "VL-1")
            active_frameworks = [primary]

        # Step 3: Run Pass B for each active framework
        # Pass B receives lesson text + Pass A output
        job["vl_analyses"] = {}

        for framework in active_frameworks:
            try:
                analysis = run_pass_b(lesson_text, pass_a_result, framework)
                job["vl_analyses"][framework] = {
                    "status": "complete",
                    "result": analysis
                }
            except Exception as e:
                job["vl_analyses"][framework] = {
                    "status": "error",
                    "error": str(e)
                }

        # For backwards compatibility, also store primary result in "result"
        primary = active_frameworks[0] if active_frameworks else "VL-1"
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
        framework_names = {
            "vl1_vertical_self": "VL-1 Vertical Self",
            "vl2_homunculus": "VL-2 Homunculus",
            "vl3_steering": "VL-3 Steering",
            "vl4_proximal_distal": "VL-4 Proximal-Distal",
            "vl5_folding": "VL-5 Folding",
        }
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
    if steps:
        html.append('<div class="spiffer-q-steps" style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-bottom: 20px;">')

        # Lesson title if present
        if router_result.get("lesson_title"):
            html.append(f"<h2>{router_result['lesson_title']}</h2>")

        html.append("<h3>SPIFFER-Q Decomposition (Pass A)</h3>")

        # Build segment lookup from Pass 0
        pass_0 = router_result.get("pass_0", {})
        segments_list = pass_0.get("segments", [])
        segment_lookup = {seg["id"]: seg["text"] for seg in segments_list}

        html.append("<ol>")
        for step in steps:
            tags = ", ".join(step.get("tags", []))
            instruction = step.get("instruction", "")
            note = f' <small style="color: #666;">({step["note"]})</small>' if step.get("note") else ""

            # Get referenced source text
            segment_ids = step.get("segments", [])
            source_texts = [segment_lookup.get(sid, f"[segment {sid} not found]") for sid in segment_ids]

            html.append(f"<li><strong>[{tags}]</strong> {instruction}{note}")

            # Show source text if we have segment references
            if source_texts:
                html.append('<details style="margin-top: 8px; margin-left: 20px;">')
                html.append(f'<summary style="cursor: pointer; color: #666; font-size: 0.9em;">Source text (segments {", ".join(str(s) for s in segment_ids)})</summary>')
                html.append('<blockquote style="margin: 8px 0; padding: 8px 12px; background: #fff; border-left: 3px solid #007bff; font-style: italic; color: #555;">')
                html.append(" ".join(source_texts))
                html.append('</blockquote></details>')

            html.append("</li>")
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

        if neg.get("striver_observations"):
            html.append("<h4>The Striver (Effort System)</h4><ul>")
            for obs in neg["striver_observations"]:
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
            if pass_a_result:
                for step in pass_a_result.get("steps", []):
                    pass_a_steps[step["step_number"]] = step
                pass_0 = pass_a_result.get("pass_0", {})
                for seg in pass_0.get("segments", []):
                    segment_lookup[seg["id"]] = seg["text"]

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
                    html.append(f"<td>{val}</td>")
                html.append("</tr>")

                # Add voice/shadow row if present
                vl_voice = row.get("vl_voice", "")
                striver_shadow = row.get("striver_shadow", "")
                if vl_voice or striver_shadow:
                    html.append(f'<tr><td colspan="{len(columns)}" style="background: #fafafa; padding: 8px 12px; font-size: 0.9em;">')
                    if vl_voice:
                        html.append(f'<span style="color: #007bff; font-weight: bold;">Person Inside:</span> <em>{vl_voice}</em>')
                    if vl_voice and striver_shadow:
                        html.append(' &mdash; ')
                    if striver_shadow:
                        html.append(f'<span style="color: #dc3545; font-weight: bold;">Shadow:</span> <em>{striver_shadow}</em>')
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

                # Card header - Seq and Negotiation State
                seq_val = row.get("column1", "")
                neg_state = ""
                # Find the negotiation state column (typically column4)
                for col in columns:
                    header = display_names.get(col, "").lower()
                    if "negotiation" in header or "state" in header:
                        neg_state = row.get(col, "")
                        break

                state_colors = {
                    "domination": "#dc3545",
                    "negotiation": "#fd7e14",
                    "integration": "#28a745",
                }
                state_color = state_colors.get(neg_state.lower(), "#6c757d") if neg_state else "#6c757d"

                html.append(f'''
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <span style="font-size: 0.9em; color: #999;">Steps {seq_val}</span>
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
                    if "negotiation" in header_lower or "state" in header_lower:
                        continue  # already shown as badge
                    html.append(f'''
                        <div style="margin-bottom: 12px;">
                            <div style="font-size: 0.8em; font-weight: bold; color: #888; text-transform: uppercase; margin-bottom: 4px;">{header}</div>
                            <div>{val}</div>
                        </div>
                    ''')

                # VL Voice and Striver Shadow
                vl_voice = row.get("vl_voice", "")
                striver_shadow = row.get("striver_shadow", "")
                if vl_voice or striver_shadow:
                    html.append('<div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #eee;">')
                    if vl_voice:
                        html.append(f'''
                            <div style="margin-bottom: 12px; padding: 12px 16px; background: #f0f7ff; border-radius: 6px; border-left: 3px solid #007bff;">
                                <div style="font-size: 0.75em; font-weight: bold; color: #007bff; text-transform: uppercase; margin-bottom: 4px;">The Person Inside</div>
                                <div style="font-style: italic; color: #333; line-height: 1.5;">{vl_voice}</div>
                            </div>
                        ''')
                    if striver_shadow:
                        html.append(f'''
                            <div style="margin-bottom: 12px; padding: 12px 16px; background: #fff5f5; border-radius: 6px; border-left: 3px solid #dc3545;">
                                <div style="font-size: 0.75em; font-weight: bold; color: #dc3545; text-transform: uppercase; margin-bottom: 4px;">The Shadow</div>
                                <div style="font-style: italic; color: #555; line-height: 1.5;">{striver_shadow}</div>
                            </div>
                        ''')
                    html.append('</div>')

                # SPIFFER-Q steps from Pass A that match this row's Seq
                if pass_a_steps and seq_val:
                    matched_steps = _parse_seq_to_steps(seq_val, pass_a_steps)
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

        # Striver observations
        striver_key = next((k for k in phase2.keys() if "striver" in k.lower() or "effort" in k.lower()), None)
        if striver_key and phase2.get(striver_key):
            html.append("<h3>The Striver (Effort System)</h3>")
            html.append("<ul>")
            for obs in phase2[striver_key]:
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
    categories = get_available_lessons()

    # Build category options
    category_options = ['<option value="">-- Select category --</option>']
    for cat_name in categories.keys():
        cat_display = cat_name.replace("_", " ").title()
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
                // Fetch lesson text and populate textarea
                try {{
                    const response = await fetch('/api/lesson/' + encodeURIComponent(category) + '/' + encodeURIComponent(select.value));
                    const data = await response.json();
                    if (data.text) {{
                        textarea.value = data.text;
                        textarea.disabled = false;
                    }} else {{
                        textarea.value = '';
                        textarea.placeholder = 'Error loading lesson';
                    }}
                }} catch (error) {{
                    textarea.value = '';
                    textarea.placeholder = 'Error loading lesson';
                }}
            }} else {{
                textarea.value = '';
                textarea.disabled = false;
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
                    resetForm();
                    return;
                }}

                // Otherwise, poll for result
                if (data.job_id) {{
                    spinner.querySelector('span:last-child').textContent =
                        'Analyzing lesson... This typically takes 30-60 seconds.';
                    await pollForResult(data.job_id);
                }}

            }} catch (error) {{
                resultDiv.innerHTML = '<div class="error">Error: ' + error.message + '</div>';
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

        function resetForm() {{
            const btn = document.getElementById('submit-btn');
            const spinner = document.getElementById('loading-spinner');
            btn.disabled = false;
            btn.textContent = 'Analyze Lesson';
            spinner.style.display = 'none';
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
    <p>Select a lesson or paste text. The system will classify which VL framework(s) apply and run the appropriate analysis.</p>

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

        <button type="submit" id="submit-btn">Analyze Lesson</button>
    </form>

    <div id="loading-spinner" class="loading-spinner">
        <span class="spinner"></span>
        <span>Analyzing lesson with AI... This may take 30-60 seconds.</span>
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


# JSON API endpoints for AJAX polling
from fastapi.responses import JSONResponse


@app.post("/api/analyze")
async def api_analyze(
    lesson_text: str = Form(""),
    selected_category: str = Form(""),
    selected_lesson: str = Form("")
):
    """Start analysis and return job_id for polling."""
    # Use textarea content (which may have been populated from a selected lesson)
    text = lesson_text.strip()
    if not text:
        return JSONResponse({"error": "Please enter or select lesson text."}, status_code=400)

    # In Lambda with S3 configured, use async pattern
    if IS_LAMBDA and KB_BUCKET:
        job_id = str(uuid.uuid4())

        # Save job to S3
        job_data = {
            "status": "pending",
            "lesson_text": text,
            "selected_category": selected_category,
            "selected_lesson": selected_lesson,
        }
        save_job(job_id, job_data)

        # Invoke Lambda asynchronously to process
        invoke_lambda_async(job_id)

        return JSONResponse({"job_id": job_id, "status": "pending"})

    # Local development: process synchronously
    try:
        # Step 1: Run Pass A (Decomposition + Routing) - ONCE
        import logging
        logger = logging.getLogger("feldenkrais")
        logger.info("Starting Pass A...")
        pass_a_result = run_pass_a(text)
        logger.info("Pass A complete. Primary: %s", pass_a_result.get("primary_framework"))
        router_html = render_router_result(pass_a_result)

        # Step 2: Get active frameworks
        active_frameworks = get_active_frameworks(pass_a_result)
        if not active_frameworks:
            primary = pass_a_result.get("primary_framework", "VL-1")
            active_frameworks = [primary]
        logger.info("Active frameworks: %s", active_frameworks)

        # Step 3: Run Pass B for each active framework
        _, schema = load_pass_b("VL-1")  # Schema is shared
        vl_analyses = {}

        for framework in active_frameworks:
            try:
                logger.info("Starting Pass B for %s...", framework)
                analysis = run_pass_b(text, pass_a_result, framework)
                logger.info("Pass B for %s complete.", framework)
                vl_analyses[framework] = {"status": "complete", "result": analysis}
            except Exception as e:
                logger.error("Pass B for %s failed: %s", framework, e)
                vl_analyses[framework] = {"status": "error", "error": str(e)}

        analysis_html = render_multi_vl_analyses(vl_analyses, schema, pass_a_result)

        return JSONResponse({
            "status": "complete",
            "result_html": router_html + analysis_html
        })

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
    selected_lesson: str = Form("")
):
    """Fallback for non-JavaScript form submission."""
    # Use textarea content
    text = lesson_text.strip()
    if not text:
        return build_html(
            lesson_text=lesson_text,
            selected_category=selected_category,
            result='<div class="error">Please enter or select lesson text.</div>'
        )

    try:
        # Step 1: Run Pass A (Decomposition + Routing) - ONCE
        pass_a_result = run_pass_a(text)
        router_html = render_router_result(pass_a_result)

        # Step 2: Get active frameworks
        active_frameworks = get_active_frameworks(pass_a_result)
        if not active_frameworks:
            primary = pass_a_result.get("primary_framework", "VL-1")
            active_frameworks = [primary]

        # Step 3: Run Pass B for each active framework
        _, schema = load_pass_b("VL-1")
        vl_analyses = {}

        for framework in active_frameworks:
            try:
                analysis = run_pass_b(text, pass_a_result, framework)
                vl_analyses[framework] = {"status": "complete", "result": analysis}
            except Exception as e:
                vl_analyses[framework] = {"status": "error", "error": str(e)}

        analysis_html = render_multi_vl_analyses(vl_analyses, schema, pass_a_result)
        result_html = f'<div class="result">{router_html}{analysis_html}</div>'

    except Exception as e:
        result_html = f'<div class="error">Error: {str(e)}</div>'

    return build_html(
        lesson_text=lesson_text if not selected_lesson else "",
        selected_category=selected_category,
        selected_lesson=selected_lesson,
        result=result_html
    )


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
