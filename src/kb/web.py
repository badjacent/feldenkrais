"""
Simple web interface for Feldenkrais lesson analysis.

Usage:
    cd src/kb
    uvicorn web:app --reload
"""

import json
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from llm import LLM

app = FastAPI(title="Feldenkrais Lesson Analyzer")

# Paths - handle both local development and Lambda environment
import os
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    # In Lambda, data files are packaged alongside the code
    DATA_DIR = Path(__file__).parent / "data"
else:
    # Local development
    DATA_DIR = Path(__file__).parent.parent.parent / "data"
LESSONS_DIR = DATA_DIR / "lessons"

# System prompt for analysis
SYSTEM_PROMPT = """You are an expert Feldenkrais practitioner analyzing lessons using the VL-1 protocol.
Follow the protocol exactly to analyze the lesson provided. Be thorough and specific in your analysis.

Important: The analysis table must cover ALL major movements identified in the lesson, from beginning to end.
This includes the final movements where the student returns to the initial position or rechecks their starting state.
Do not truncate the table early."""


def load_protocol() -> tuple[str, dict]:
    """Load the current protocol markdown and its JSON schema."""
    protocol_path = DATA_DIR / "washing.md"
    schema_path = DATA_DIR / "washing.schema.json"

    protocol = protocol_path.read_text() if protocol_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}

    return protocol, schema


def get_llm() -> LLM:
    """Get configured LLM instance."""
    return LLM(
        provider="gemini",
        model="gemini-3-flash-preview",
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


def render_analysis(data: dict, schema: dict) -> str:
    """Render analysis dict as HTML based on schema structure."""
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

        html.append(f"<h2>VL-1 Analytical Table ({len(rows)} rows)</h2>")

        if rows:
            # Infer columns from first row
            first_row = rows[0]
            columns = list(first_row.keys())

            # Pretty column names
            def pretty_col(col: str) -> str:
                return col.replace("_", " ").replace("spiffer q", "SPIFFER-Q").title()

            html.append("""
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
                <thead style="background-color: #f0f0f0;">
                    <tr>
            """)
            for col in columns:
                html.append(f"<th>{pretty_col(col)}</th>")
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
        function onLessonSelect(select) {{
            const textarea = document.getElementById('lesson_text');
            if (select.value) {{
                textarea.disabled = true;
                textarea.placeholder = 'Using selected lesson...';
            }} else {{
                textarea.disabled = false;
                textarea.placeholder = 'Paste lesson text here...';
            }}
        }}

        function onSubmit() {{
            const btn = document.getElementById('submit-btn');
            const spinner = document.getElementById('loading-spinner');
            btn.disabled = true;
            btn.textContent = 'Analyzing...';
            spinner.style.display = 'block';
            return true;
        }}
    </script>
</head>
<body>
    <h1>Feldenkrais Lesson Analyzer</h1>
    <p>Select a lesson from the knowledge base or paste custom text to analyze using the VL-1 protocol.</p>

    <form method="post" action="/analyze" onsubmit="return onSubmit()">
        <div class="form-group">
            <label for="selected_lesson">Select a Lesson:</label>
            <select name="selected_lesson" id="selected_lesson" onchange="onLessonSelect(this)">
                {lesson_select}
            </select>
        </div>

        <div class="or-divider">— OR —</div>

        <div class="form-group">
            <label for="lesson_text">Paste Lesson Text:</label>
            <textarea name="lesson_text" id="lesson_text" placeholder="Paste lesson text here..." {"disabled" if selected_lesson else ""}>{lesson_text if not selected_lesson else ""}</textarea>
        </div>

        <button type="submit" id="submit-btn">Analyze Lesson</button>
    </form>

    <div id="loading-spinner" class="loading-spinner">
        <span class="spinner"></span>
        Analyzing lesson with AI... This may take 30-60 seconds.
    </div>

    {result}
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home():
    """Show the input form."""
    return build_html()


@app.get("/analyze")
async def analyze_redirect():
    """Redirect GET requests to home."""
    return RedirectResponse(url="/", status_code=303)


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(
    lesson_text: str = Form(""),
    selected_lesson: str = Form("")
):
    """Analyze a lesson and display results."""
    # Determine which lesson text to use
    if selected_lesson:
        text = load_lesson(selected_lesson)
        if not text:
            return build_html(
                selected_lesson=selected_lesson,
                result='<div class="error">Could not load selected lesson.</div>'
            )
    else:
        text = lesson_text.strip()
        if not text:
            return build_html(
                lesson_text=lesson_text,
                result='<div class="error">Please select a lesson or enter text.</div>'
            )

    try:
        # Load protocol and schema from files
        protocol, schema = load_protocol()

        # Build prompt with protocol and lesson
        prompt = protocol + "\n\n" + text

        # Get structured analysis using JSON schema
        llm = get_llm()
        analysis = llm.complete_structured_json(
            prompt,
            schema,
            system=SYSTEM_PROMPT,
        )

        # Render result using dynamic renderer
        result_html = f'<div class="result">{render_analysis(analysis, schema)}</div>'

    except Exception as e:
        result_html = f'<div class="error">Error: {str(e)}</div>'

    return build_html(
        lesson_text=lesson_text if not selected_lesson else "",
        selected_lesson=selected_lesson,
        result=result_html
    )


# Lambda handler (for AWS Lambda + Function URL)
from mangum import Mangum
handler = Mangum(app, lifespan="off")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
