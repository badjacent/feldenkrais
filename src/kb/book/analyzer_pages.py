"""Analyzer page HTML builders: build_html and build_result_html."""

import json

from book.state import get_available_lessons, FRAMEWORK_VERSION
from book.analysis import get_available_framework_versions
from book.analyzer_html import render_router_result, render_multi_vl_analyses, render_analysis


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
        cat: [(t[0], t[1]) for t in lessons]
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
       <a href="/pass0b" style="float: right; color: #007bff; text-decoration: none; font-size: 0.9em;">Pass 0b Analysis &rarr;</a>
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
                    <option value="gemini">Gemini 3 Flash</option>
                    <option value="openai">OpenAI (GPT-5)</option>
                    <option value="anthropic">Sonnet 4.6</option>
                    <option value="anthropic-sonnet-4.5">Sonnet 4.5</option>
                    <option value="anthropic-haiku">Haiku 4.5</option>
                </select>
            </label>
            <label style="font-size: 0.9em;">
                Pass B provider:
                <select name="pass_b_provider" style="margin-left: 4px; padding: 2px 6px;">
                    <option value="">Default (Gemini)</option>
                    <option value="gemini">Gemini 3 Flash</option>
                    <option value="openai">OpenAI (GPT-5)</option>
                    <option value="anthropic">Sonnet 4.6</option>
                    <option value="anthropic-sonnet-4.5">Sonnet 4.5</option>
                    <option value="anthropic-haiku">Haiku 4.5</option>
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
    <p><a href="/analyzer">Start a new analysis</a></p>
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
    <p><a href="/analyzer">Try again</a></p>
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
        <a href="/analyzer">&larr; Analyze another lesson</a>
    </div>
    {result_html}
</body>
</html>
"""

