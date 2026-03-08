"""HTML page builders for the Feldenkrais web interface.

Extracted from web.py to reduce file size. These functions produce complete
HTML pages (f-strings) consumed by FastAPI route handlers.
"""

import html as html_mod
import json
from typing import Callable


def build_pass0b_html(search_mode: bool = False, get_lessons_fn: Callable | None = None) -> str:
    """Build the Pass 0b experiment page HTML."""
    categories, display_names = get_lessons_fn()

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
                        if (cascadeMode === 'confident') specHtml += '<br><span style="color: #555; font-size: 0.95em;">Ranked by distinctive movement patterns unique to each lesson.</span>';
                        else if (cascadeMode === 'blended') specHtml += '<br><span style="color: #555; font-size: 0.95em;">Blended ranking: combining semantic meaning with distinctive movement patterns.</span>';
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


def _auth_bar_html(user: dict | None, superuser_active: bool = False) -> str:
    """Render logout link for logged-in users, with superuser toggle if eligible."""
    if user:
        login = html_mod.escape(user.get("login", ""))
        parts = [login]
        if user.get("superuser_eligible"):
            if superuser_active:
                parts.append('<a href="/auth/superuser/off" style="color:#d32f2f; font-weight:600;">Exit Superuser</a>')
            else:
                parts.append('<a href="/auth/superuser/on">Superuser</a>')
        parts.append('<a href="/auth/logout">Log out</a>')
        parts.append('<a href="/docs/demo_overview">Docs</a>')
        return '<div class="auth-bar">' + ' &middot; '.join(parts) + '</div>'
    return ''


_AUTH_BAR_CSS = """
        .auth-bar { text-align: right; font-size: 0.85em; color: #666; margin-bottom: 8px; }
        .auth-bar a { color: #007bff; text-decoration: none; }
        .auth-bar a:hover { text-decoration: underline; }
"""


def build_search_home_html(extra_html: str = "", user: dict | None = None, superuser_active: bool = False, get_lessons_fn: Callable | None = None) -> str:
    """Build the search subdomain home page with search box and browse-by-source."""
    categories, display_names = get_lessons_fn()

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
    {_auth_bar_html(user, superuser_active=superuser_active)}
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
                        if (cascadeMode === 'confident') specHtml += '<br><span style="color:#555;font-size:0.95em;">Ranked by distinctive movement patterns unique to each lesson.</span>';
                        else if (cascadeMode === 'blended') specHtml += '<br><span style="color:#555;font-size:0.95em;">Blended ranking: combining semantic meaning with distinctive movement patterns.</span>';
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


def build_lesson_page_html(document_id: str, user: dict | None = None, search_mode: bool = False, superuser_active: bool = False) -> str:
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
    {_auth_bar_html(user, superuser_active=superuser_active)}
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
        <button onclick="switchTab('transcript')">Transcript</button>
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

    <!-- Transcript tab -->
    <div id="tab-transcript" style="display:none;">
        <div class="tab-intro">The full lesson transcript, organized by movement group and step.</div>
        <div id="transcript-content">
            <div style="color:#999; padding:16px;"><span class="spinner-inline"></span> Preparing transcript...</div>
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

const TAB_ORDER = ['steps', 'condensed', 'concepts', 'transcript', 'chat', 'detailed'];
let conceptsLoaded = false;
let transcriptRendered = false;

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
    if (tab === 'transcript' && !transcriptRendered) renderTranscript();
    if (tab === 'detailed' && !detailedRendered && analysisData) renderDetailed();
    if (tab === 'steps' && analysisData) renderCard();
    if (tab === 'chat') document.getElementById('chat-input').focus();
}}

// ── Transcript rendering ──

function renderTranscript() {{
    transcriptRendered = true;
    const div = document.getElementById('transcript-content');
    if (!analysisData || !analysisData.steps || !analysisData.steps.length) {{
        // Fallback to raw lesson text
        if (lessonText) {{
            div.innerHTML = '<pre style="white-space:pre-wrap;font-family:inherit;font-size:0.95em;line-height:1.7;color:#333;margin:0;padding:16px 0;">' + escHtml(lessonText) + '</pre>';
        }} else {{
            div.innerHTML = '<div style="color:#999;padding:16px;">No transcript data available.</div>';
        }}
        return;
    }}
    // Build step lookup
    const stepLookup = {{}};
    for (const step of analysisData.steps) stepLookup[step.step_number] = step;

    // Use groups from analysis data, which each have a .steps list of step numbers
    const groups = analysisData.groups || [];
    const groupedStepNums = new Set();
    let html = '';
    for (const group of groups) {{
        const stepNums = group.steps || [];
        stepNums.forEach(sn => groupedStepNums.add(sn));
        const heading = group.label ? 'Group ' + group.group_number + ': ' + escHtml(group.label) : 'Group ' + group.group_number;
        const pos = group.starting_position ? '<span style="color:#888;font-weight:400;font-size:0.85em;margin-left:8px;">' + escHtml(group.starting_position) + '</span>' : '';
        html += '<div style="margin-bottom:28px;">';
        html += '<div style="border-left:4px solid #1a73e8;padding:8px 16px;margin-bottom:12px;">';
        html += '<div style="font-size:1.05em;font-weight:600;color:#222;">' + heading + pos + '</div>';
        html += '</div>';
        const stepsInGroup = stepNums.map(sn => stepLookup[sn]).filter(Boolean);
        for (let si = 0; si < stepsInGroup.length; si++) {{
            const step = stepsInGroup[si];
            const segs = (step.segments || []).filter(s => s.text);
            if (!segs.length) continue;
            const text = segs.map(s => s.text.trim()).join(' ');
            const timeStr = (step.time_start != null && step.time_end != null) ? ' <span style="font-weight:400;color:#aaa;">' + fmtTime(step.time_start) + ' \u2013 ' + fmtTime(step.time_end) + '</span>' : '';
            html += '<div style="padding:0 16px 12px 24px;' + (si < stepsInGroup.length - 1 ? 'border-bottom:1px solid #f0f0f0;margin-bottom:12px;' : '') + '">';
            html += '<div style="font-size:0.75em;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Step ' + step.step_number + timeStr + '</div>';
            html += '<div style="font-size:0.95em;line-height:1.7;color:#333;">' + escHtml(text) + '</div>';
            html += '</div>';
        }}
        html += '</div>';
    }}
    // Any ungrouped steps
    const ungrouped = analysisData.steps.filter(s => !groupedStepNums.has(s.step_number));
    if (ungrouped.length) {{
        if (groups.length) {{
            html += '<div style="margin-bottom:28px;">';
            html += '<div style="border-left:4px solid #999;padding:8px 16px;margin-bottom:12px;">';
            html += '<div style="font-size:1.05em;font-weight:600;color:#222;">Other Steps</div>';
            html += '</div>';
        }}
        for (let si = 0; si < ungrouped.length; si++) {{
            const step = ungrouped[si];
            const segs = (step.segments || []).filter(s => s.text);
            if (!segs.length) continue;
            const text = segs.map(s => s.text.trim()).join(' ');
            const timeStr = (step.time_start != null && step.time_end != null) ? ' <span style="font-weight:400;color:#aaa;">' + fmtTime(step.time_start) + ' \u2013 ' + fmtTime(step.time_end) + '</span>' : '';
            html += '<div style="padding:0 16px 12px 24px;' + (si < ungrouped.length - 1 ? 'border-bottom:1px solid #f0f0f0;margin-bottom:12px;' : '') + '">';
            html += '<div style="font-size:0.75em;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Step ' + step.step_number + timeStr + '</div>';
            html += '<div style="font-size:0.95em;line-height:1.7;color:#333;">' + escHtml(text) + '</div>';
            html += '</div>';
        }}
        if (groups.length) html += '</div>';
    }}
    div.innerHTML = html;
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
                feldenkrais_concept: '#ff9800', teaching_strategy: '#e91e63', theme: '#607d8b',
                spatial_config: '#00897b', contact_point: '#5c6bc0', constraint: '#c62828', working_position: '#6a1b9a'
            }};
            const catLabels = {{
                body_region: 'Body Region', movement_pattern: 'Movement Pattern', position: 'Position',
                feldenkrais_concept: 'Feldenkrais Concept', teaching_strategy: 'Teaching Strategy', theme: 'Theme',
                spatial_config: 'Spatial Configuration', contact_point: 'Contact Point', constraint: 'Constraint', working_position: 'Working Position'
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


def build_pass0b_player_html(search_mode: bool = False, get_lessons_fn: Callable | None = None) -> str:
    """Build the Pass 0b audio player page HTML."""
    categories, display_names = get_lessons_fn()

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


def build_pass0b_lang_html(search_mode: bool = False, get_lessons_fn: Callable | None = None) -> str:
    """Build the Pass 0b-lang language analysis page HTML."""
    categories, display_names = get_lessons_fn()

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
