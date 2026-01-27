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


def get_available_lessons() -> list[tuple[str, str]]:
    """Get list of (filename, display_name) for available lessons."""
    lessons = []
    if LESSONS_DIR.exists():
        for f in sorted(LESSONS_DIR.glob("*.txt")):
            display = f.stem.replace("_", " ").replace("-", " ").title()
            lessons.append((f.name, display))
    return lessons


def load_lesson(filename: str) -> str:
    """Load lesson text from file."""
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

    Returns VL classification AND SPIFFER-Q decomposed steps.
    """
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
            "steps": []
        }

    prompt = pass_a_prompt + "\n\n---\n\n## Lesson to Analyze:\n\n" + lesson_text

    llm = get_llm()
    result = llm.complete_structured_json(
        prompt,
        pass_a_schema,
        system=ROUTER_SYSTEM_PROMPT,
    )

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
        html.append("<ol>")
        for step in steps:
            tags = ", ".join(step.get("tags", []))
            instruction = step.get("instruction", "")
            note = f' <small style="color: #666;">({step["note"]})</small>' if step.get("note") else ""
            html.append(f"<li><strong>[{tags}]</strong> {instruction}{note}</li>")
        html.append("</ol>")
        html.append("</div>")

    return "\n".join(html)


def render_multi_vl_analyses(vl_analyses: dict, schema: dict) -> str:
    """Render multiple VL analyses with tabs for switching between them."""
    if not vl_analyses:
        return ""

    frameworks = list(vl_analyses.keys())

    if len(frameworks) == 1:
        # Single framework, no tabs needed
        framework = frameworks[0]
        entry = vl_analyses[framework]
        if entry.get("status") == "complete" and entry.get("result"):
            return f'<h2>{framework} Analysis</h2>' + render_analysis(entry["result"], schema)
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
            html.append(render_analysis(entry["result"], schema))
        elif entry.get("status") == "error":
            html.append(f'<div class="error">Error: {entry.get("error", "Unknown error")}</div>')
        else:
            html.append('<p>No analysis available.</p>')

        html.append('</div>')

    # Tab switching JavaScript
    html.append('''
        <script>
        function showVLTab(framework) {
            // Hide all content
            document.querySelectorAll('.vl-content').forEach(el => el.style.display = 'none');
            // Deactivate all tabs
            document.querySelectorAll('.vl-tabs button').forEach(el => {
                el.style.background = '#e9ecef';
                el.style.color = '#333';
            });
            // Show selected content
            document.getElementById('content-' + framework).style.display = 'block';
            // Activate selected tab
            document.getElementById('tab-' + framework).style.background = '#007bff';
            document.getElementById('tab-' + framework).style.color = 'white';
        }
        </script>
    ''')

    return "\n".join(html)


def render_pass_b_analysis(data: dict) -> str:
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

    # Analytical Table
    table = data.get("analytical_table", {})
    if table:
        rows = table.get("rows", [])
        column_headers = table.get("column_headers", {})

        html.append(f"<h3>Analytical Table ({len(rows)} rows)</h3>")

        if rows:
            # Determine columns to display
            if column_headers:
                columns = sorted([k for k in column_headers.keys() if column_headers.get(k)],
                                key=lambda x: int(x.replace("column", "")) if x.startswith("column") else 999)
                display_names = {col: column_headers[col] for col in columns}
            else:
                columns = ["column1", "column2", "column3", "column4", "column5"]
                display_names = {c: c for c in columns}

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

            html.append("</tbody></table>")

    return "\n".join(html)


def render_analysis(data: dict, schema: dict) -> str:
    """Render analysis dict as HTML based on schema structure.

    Handles both old (washing.schema.json) and new (pass_b.schema.json) formats.
    """
    # Check if this is Pass B format (has negotiation_framework, somatic_narrative, analytical_table)
    if "negotiation_framework" in data or "somatic_narrative" in data or "analytical_table" in data:
        return render_pass_b_analysis(data)

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


def build_html(lesson_text: str = "", selected_lesson: str = "", result: str = "") -> str:
    """Build the HTML page."""
    lessons = get_available_lessons()

    # Build lesson options
    lesson_options = ['<option value="">-- Paste custom text below --</option>']
    for filename, display in lessons:
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
            if (select.value) {{
                // Fetch lesson text and populate textarea
                try {{
                    const response = await fetch('/api/lesson/' + encodeURIComponent(select.value));
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
    </script>
</head>
<body>
    <h1>Feldenkrais Lesson Analyzer</h1>
    <p>Select a lesson or paste text. The system will classify which VL framework(s) apply and run the appropriate analysis.</p>

    <form id="analyze-form" method="post" action="/analyze" onsubmit="submitForm(event)">
        <div class="form-group">
            <label for="selected_lesson">Select a Lesson:</label>
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


@app.get("/api/lesson/{filename}")
async def get_lesson_text(filename: str):
    """Return lesson text for populating the textarea."""
    text = load_lesson(filename)
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
            "selected_lesson": selected_lesson,
        }
        save_job(job_id, job_data)

        # Invoke Lambda asynchronously to process
        invoke_lambda_async(job_id)

        return JSONResponse({"job_id": job_id, "status": "pending"})

    # Local development: process synchronously
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
        _, schema = load_pass_b("VL-1")  # Schema is shared
        vl_analyses = {}

        for framework in active_frameworks:
            try:
                analysis = run_pass_b(text, pass_a_result, framework)
                vl_analyses[framework] = {"status": "complete", "result": analysis}
            except Exception as e:
                vl_analyses[framework] = {"status": "error", "error": str(e)}

        analysis_html = render_multi_vl_analyses(vl_analyses, schema)

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
    selected_lesson: str = Form("")
):
    """Fallback for non-JavaScript form submission."""
    # Use textarea content
    text = lesson_text.strip()
    if not text:
        return build_html(
            lesson_text=lesson_text,
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

        analysis_html = render_multi_vl_analyses(vl_analyses, schema)
        result_html = f'<div class="result">{router_html}{analysis_html}</div>'

    except Exception as e:
        result_html = f'<div class="error">Error: {str(e)}</div>'

    return build_html(
        lesson_text=lesson_text if not selected_lesson else "",
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
