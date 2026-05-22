"""HTML page builders for the Feldenkrais web interface.

Extracted from web.py to reduce file size. These functions produce complete
HTML pages (f-strings) consumed by FastAPI route handlers.
"""

import html as html_mod
import json
from typing import Callable



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
            display: flex; flex-wrap: wrap; gap: 0; border-bottom: 2px solid #e0e0e0; margin-bottom: 16px;
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
    <div id="no-audio-msg" style="display:none; color: #888; font-size: 0.9em; font-style: italic; margin-bottom: 12px;">No recording available.</div>

    <div class="tab-bar" id="tab-bar">
        <button class="active" onclick="switchTab('steps')">Step by Step</button>
        <button onclick="switchTab('condensed')">Overview</button>
        <button onclick="switchTab('somatic')">Somatic</button>
        <button onclick="switchTab('language')">Language</button>
        <button onclick="switchTab('classification')">Classification</button>
        <button onclick="switchTab('pipeline')">Pipeline</button>
        <button onclick="switchTab('concepts')">Search Specifics</button>
        <button onclick="switchTab('transcript')">Transcript</button>
        <button onclick="switchTab('chat')">Q&amp;A</button>
        <button onclick="switchTab('history')">History</button>
        <button onclick="switchTab('rawio')">Raw I/O</button>
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

    <!-- Somatic tab -->
    <div id="tab-somatic" style="display:none;">
        <div class="tab-intro">Per-step biomechanical analysis: differentiation, activation, inhibition, transmission route, and ground push.</div>
        <div id="somatic-tab-content">
            <div style="color:#999; padding:16px;"><span class="spinner-inline"></span> Loading somatic analysis...</div>
        </div>
    </div>

    <!-- Language tab -->
    <div id="tab-language" style="display:none;">
        <div class="tab-intro">Per-step language analysis: explanatory language, attention cues, effort descriptors, imagery, and distinctive phrasing.</div>
        <div id="language-tab-content">
            <div style="color:#999; padding:16px;"><span class="spinner-inline"></span> Loading language analysis...</div>
        </div>
    </div>

    <!-- Classification tab -->
    <div id="tab-classification" style="display:none;">
        <div class="tab-intro">Pass A decomposition and framework classification for this lesson.</div>
        <div id="classification-tab-content">
            <div style="color:#999; padding:16px;"><span class="spinner-inline"></span> Loading classification data...</div>
        </div>
    </div>

    <!-- Pipeline tab -->
    <div id="tab-pipeline" style="display:none;">
        <div class="tab-intro">Pipeline analysis status: which steps have been completed, their versions, and any errors.</div>
        <div id="pipeline-tab-content">
            <div style="color:#999; padding:16px;"><span class="spinner-inline"></span> Loading pipeline status...</div>
        </div>
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
        <div class="tab-intro" style="display:flex;align-items:center;justify-content:space-between;">
            <span>The full lesson transcript, organized by movement group and step.</span>
            <button onclick="downloadTranscript()" style="padding:4px 12px;font-size:0.85em;border:1px solid #ccc;border-radius:4px;background:#fff;cursor:pointer;">Download</button>
        </div>
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

    <!-- History tab: prior Q&A on this lesson -->
    <div id="tab-history" style="display:none;">
        <div class="tab-intro">Previous Q&amp;A conversations on this lesson. <a href="/conversations" style="margin-left:8px;">All conversations &rarr;</a></div>
        <div id="history-tab-content">
            <div style="color:#999; padding:16px;"><span class="spinner-inline"></span> Loading history...</div>
        </div>
    </div>

    <!-- Raw I/O tab -->
    <div id="tab-rawio" style="display:none;">
        <div class="tab-intro">Raw LLM prompts and responses for each analysis step. Useful for debugging and understanding what data was produced.</div>
        <div id="rawio-tab-content">
            <div style="color:#999; padding:16px;"><span class="spinner-inline"></span> Loading raw I/O data...</div>
        </div>
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
let activeTab = 'steps';

const audio = document.getElementById('audio-player');
const audioContainer = document.getElementById('audio-container');

// ── Tab switching ──

const TAB_ORDER = ['steps', 'condensed', 'somatic', 'language', 'classification', 'pipeline', 'concepts', 'transcript', 'chat', 'history', 'rawio'];
let conceptsLoaded = false;
let transcriptRendered = false;
let somaticTabLoaded = false;
let languageTabLoaded = false;
let classificationTabLoaded = false;
let pipelineTabLoaded = false;
let rawioTabLoaded = false;
let historyTabLoaded = false;

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
if (tab === 'steps' && analysisData) renderCard();
    if (tab === 'chat') document.getElementById('chat-input').focus();
    if (tab === 'somatic' && !somaticTabLoaded) loadSomaticTab();
    if (tab === 'language' && !languageTabLoaded) loadLanguageTab();
    if (tab === 'classification' && !classificationTabLoaded) loadClassificationTab();
    if (tab === 'pipeline' && !pipelineTabLoaded) loadPipelineTab();
    if (tab === 'rawio' && !rawioTabLoaded) loadRawIOTab();
    if (tab === 'history' && !historyTabLoaded) loadHistoryTab();
}}

// ── History tab (per-lesson chat history) ──

async function loadHistoryTab() {{
    historyTabLoaded = true;
    const div = document.getElementById('history-tab-content');
    try {{
        const res = await fetch('/api/lesson-chat-history/' + encodeURIComponent(DOCUMENT_ID));
        const data = await res.json();
        if (data.error) {{
            div.innerHTML = '<div style="color:#999;padding:16px;">' + escHtml(data.error) + '</div>';
            return;
        }}
        if (!data.turns || data.turns.length === 0) {{
            div.innerHTML = '<div style="color:#999;padding:16px;">No previous Q&amp;A on this lesson yet.</div>';
            return;
        }}
        const parts = [];
        if (data.is_superuser_view) {{
            parts.push('<div style="font-size:0.85em;color:#888;padding:0 0 12px 0;">Superuser view: showing all users.</div>');
        }}
        for (const t of data.turns) {{
            const ts = t.created_at ? new Date(t.created_at).toLocaleString() : '';
            const userTag = (data.is_superuser_view && t.user_id) ? ' &middot; user ' + t.user_id : '';
            parts.push(
                '<div style="border-left:3px solid #007bff;padding:8px 14px;margin:14px 0;background:#f8f9fa;">' +
                '<div style="font-size:0.8em;color:#888;margin-bottom:6px;">' + escHtml(ts) + escHtml(userTag) + '</div>' +
                '<div style="font-weight:600;color:#333;margin-bottom:8px;white-space:pre-wrap;">' + escHtml(t.query) + '</div>' +
                '<div style="color:#555;white-space:pre-wrap;line-height:1.5;">' + escHtml(t.response) + '</div>' +
                '</div>'
            );
        }}
        div.innerHTML = parts.join('');
    }} catch (e) {{
        div.innerHTML = '<div style="color:#c62828;padding:16px;">Failed to load history: ' + escHtml(e.message) + '</div>';
    }}
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
        const stepsInGroup = stepNums.map(sn => stepLookup[sn]).filter(Boolean);
        const heading = group.label ? 'Group ' + group.group_number + ': ' + escHtml(group.label) : 'Group ' + group.group_number;
        const pos = group.starting_position ? '<span style="color:#888;font-weight:400;font-size:0.85em;margin-left:8px;">' + escHtml(group.starting_position) + '</span>' : '';
        // Compute time range and step range from contained steps
        let groupMeta = '';
        if (stepsInGroup.length) {{
            const firstStep = stepsInGroup[0];
            const lastStep = stepsInGroup[stepsInGroup.length - 1];
            const parts = [];
            if (firstStep.time_start != null && lastStep.time_end != null) {{
                parts.push(fmtTime(firstStep.time_start) + ' \u2013 ' + fmtTime(lastStep.time_end));
            }}
            if (stepNums.length === 1) {{
                parts.push('step ' + stepNums[0]);
            }} else if (stepNums.length > 1) {{
                parts.push('steps ' + stepNums[0] + '\u2013' + stepNums[stepNums.length - 1]);
            }}
            if (parts.length) {{
                groupMeta = '<div style="font-style:italic;font-size:0.78em;color:#bbb;font-weight:400;margin-top:2px;">' + parts.join(' \u00a0\u00b7\u00a0 ') + '</div>';
            }}
        }}
        html += '<div style="margin-bottom:28px;">';
        html += '<div style="border-left:4px solid #1a73e8;padding:8px 16px;margin-bottom:12px;">';
        html += '<div style="font-size:1.05em;font-weight:600;color:#222;">' + heading + pos + '</div>';
        html += groupMeta;
        html += '</div>';
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

function downloadTranscript() {{
    let text = '';
    const title = document.querySelector('h1')?.textContent || 'transcript';
    if (!analysisData || !analysisData.steps || !analysisData.steps.length) {{
        text = lessonText || '';
    }} else {{
        const stepLookup = {{}};
        for (const step of analysisData.steps) stepLookup[step.step_number] = step;
        const groups = analysisData.groups || [];
        const groupedStepNums = new Set();
        for (const group of groups) {{
            const stepNums = group.steps || [];
            stepNums.forEach(sn => groupedStepNums.add(sn));
            const heading = group.label ? 'Group ' + group.group_number + ': ' + group.label : 'Group ' + group.group_number;
            const pos = group.starting_position ? ' (' + group.starting_position + ')' : '';
            text += heading + pos + '\\n';
            text += '='.repeat(heading.length) + '\\n\\n';
            const stepsInGroup = stepNums.map(sn => stepLookup[sn]).filter(Boolean);
            for (const step of stepsInGroup) {{
                const segs = (step.segments || []).filter(s => s.text);
                if (!segs.length) continue;
                const body = segs.map(s => s.text.trim()).join(' ');
                text += 'Step ' + step.step_number + '\\n';
                text += body + '\\n\\n';
            }}
        }}
        const ungrouped = analysisData.steps.filter(s => !groupedStepNums.has(s.step_number));
        if (ungrouped.length) {{
            if (groups.length) text += 'Other Steps\\n===========\\n\\n';
            for (const step of ungrouped) {{
                const segs = (step.segments || []).filter(s => s.text);
                if (!segs.length) continue;
                const body = segs.map(s => s.text.trim()).join(' ');
                text += 'Step ' + step.step_number + '\\n';
                text += body + '\\n\\n';
            }}
        }}
    }}
    const blob = new Blob([text], {{type: 'text/plain'}});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = title.replace(/[^a-zA-Z0-9 _-]/g, '').trim().replace(/\\s+/g, '_') + '.txt';
    a.click();
    URL.revokeObjectURL(a.href);
}}

// ── Concepts loading ──

async function loadConcepts() {{
    conceptsLoaded = true;
    const div = document.getElementById('concepts-content');
    try {{
        // Load concepts and positions in parallel
        const [res, posRes] = await Promise.all([
            fetch('/api/lesson/' + DOCUMENT_ID + '/concepts'),
            fetch('/api/lesson/' + DOCUMENT_ID + '/positions'),
        ]);
        const data = await res.json();
        const posData = await posRes.json();
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
        // Position sequence (spatial v3)
        if (posData.available && posData.steps && posData.steps.length) {{
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#00695c;margin-bottom:8px;">Positions Through the Lesson</div>';
            if (posData.position_summary && posData.position_summary.length) {{
                html += '<div style="margin-bottom:12px;">';
                posData.position_summary.forEach((p, i) => {{
                    const pos = typeof p === 'string' ? p : p.position;
                    const alts = (typeof p === 'object' && p.alternates) ? p.alternates : [];
                    html += '<div style="margin-bottom:6px;">';
                    html += '<span style="padding:4px 10px;background:#e0f2f1;border:1px solid #b2dfdb;border-radius:14px;font-size:0.82em;color:#00695c;">' + escConcept(pos) + '</span>';
                    if (alts.length) {{
                        html += '<div style="margin-left:16px;margin-top:3px;display:flex;flex-wrap:wrap;gap:4px;">';
                        alts.forEach(a => {{
                            html += '<span style="padding:2px 8px;background:#f5f5f5;border:1px solid #e0e0e0;border-radius:10px;font-size:0.75em;color:#888;">' + escConcept(a) + '</span>';
                        }});
                        html += '</div>';
                    }}
                    html += '</div>';
                }});
                html += '</div>';
            }}
            let curGroup = -1;
            posData.steps.forEach(s => {{
                if (s.group_number !== curGroup) {{
                    if (curGroup !== -1) html += '</div>';
                    curGroup = s.group_number;
                    html += '<div style="margin-bottom:8px;">';
                    html += '<div style="font-size:0.78em;font-weight:600;color:#666;margin-top:8px;margin-bottom:4px;">Group ' + s.group_number + '</div>';
                }}
                const posStyle = 'font-weight:500;color:#00796b;';
                const actionStyle = 'color:#555;';
                const tagStyle = 'display:inline-block;font-size:0.7em;padding:1px 5px;border-radius:3px;margin-left:4px;';
                let tags = '';
                if (s.is_rest) tags += '<span style="' + tagStyle + 'background:#e8eaf6;color:#5c6bc0;">rest</span>';
                if (s.is_transition) tags += '<span style="' + tagStyle + 'background:#fff3e0;color:#e65100;">transition</span>';
                html += '<div style="font-size:0.85em;line-height:1.5;padding:2px 0;">';
                html += '<span style="color:#999;font-size:0.8em;margin-right:4px;">' + s.step_number + '.</span>';
                html += '<span style="' + posStyle + '">[' + escConcept(s.position) + ']</span> ';
                html += '<span style="' + actionStyle + '">' + escConcept(s.action) + '</span>';
                html += tags;
                html += '</div>';
            }});
            if (curGroup !== -1) html += '</div>';
            html += '</div>';
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
                spatial_config: '#00897b', contact_point: '#5c6bc0', constraint: '#c62828', working_position: '#6a1b9a',
                position_v3: '#00695c', position_alt: '#26a69a'
            }};
            const catLabels = {{
                body_region: 'Body Region', movement_pattern: 'Movement Pattern', position: 'Position',
                feldenkrais_concept: 'Feldenkrais Concept', teaching_strategy: 'Teaching Strategy', theme: 'Theme',
                spatial_config: 'Spatial Configuration', contact_point: 'Contact Point', constraint: 'Constraint', working_position: 'Working Position',
                position_v3: 'Position (detailed)', position_alt: 'Position (alternate phrasing)'
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

// ── Somatic tab ──

async function loadSomaticTab() {{
    somaticTabLoaded = true;
    const div = document.getElementById('somatic-tab-content');
    try {{
        const res = await fetch('/api/lesson/' + DOCUMENT_ID + '/somatic');
        const data = await res.json();
        if (data.error) {{
            div.innerHTML = '<div style="color:#999;padding:16px;">' + escHtml(data.error) + '</div>';
            return;
        }}
        let html = '';
        // Summary section
        const summary = data.summary || {{}};
        if (summary.lesson_intent || summary.overall_transmission_route) {{
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#1a73e8;margin-bottom:8px;">Somatic Summary</div>';
            if (summary.lesson_intent) html += '<p style="margin:0 0 8px 0;"><strong>Lesson Intent:</strong> ' + escHtml(summary.lesson_intent) + '</p>';
            if (summary.overall_transmission_route) html += '<p style="margin:0 0 8px 0;"><strong>Overall Transmission Route:</strong> ' + escHtml(summary.overall_transmission_route) + '</p>';
            if (summary.primary_differentiation) html += '<p style="margin:0;"><strong>Primary Differentiation:</strong> ' + escHtml(summary.primary_differentiation) + '</p>';
            html += '</div>';
        }}
        // Per-step cards
        const steps = data.enriched_steps || [];
        const paSteps = (data.pass_a && data.pass_a.steps) || [];
        const paLookup = {{}};
        paSteps.forEach(s => {{ paLookup[s.step_number] = s; }});
        steps.forEach(step => {{
            const pa = paLookup[step.step_number] || {{}};
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:14px 18px;margin-bottom:10px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;color:#999;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Step ' + step.step_number + '</div>';
            if (pa.instruction) html += '<div style="font-style:italic;color:#444;margin-bottom:8px;font-size:0.9em;">' + escHtml(pa.instruction) + '</div>';
            const fields = [
                ['Clarified', 'clarified'], ['Intent', 'intent'],
                ['Differentiation', 'differentiation'], ['Activation', 'activation'],
                ['Inhibition', 'inhibition'], ['Transmission Route', 'transmission_route'],
                ['Ground Push', 'ground_push'], ['Mechanical Directive', 'mechanical_directive'],
                ['Attentional Cue', 'attentional_cue'],
            ];
            let bullets = '';
            fields.forEach(([label, key]) => {{
                const val = step[key];
                if (val && val.toLowerCase() !== 'none') {{
                    bullets += '<li><strong>' + label + ':</strong> ' + escHtml(val) + '</li>';
                }}
            }});
            if (bullets) html += '<ul style="margin:0;padding-left:18px;font-size:0.9em;color:#444;line-height:1.5;">' + bullets + '</ul>';
            else html += '<div style="color:#999;font-size:0.9em;">No somatic data for this step.</div>';
            html += '</div>';
        }});
        if (!steps.length) html += '<div style="color:#999;padding:16px;">No somatic step data available.</div>';
        div.innerHTML = html;
    }} catch (e) {{
        div.innerHTML = '<div style="color:#999;padding:16px;">Failed to load somatic data.</div>';
    }}
}}

// ── Language tab ──

async function loadLanguageTab() {{
    languageTabLoaded = true;
    const div = document.getElementById('language-tab-content');
    try {{
        const res = await fetch('/api/lesson/' + DOCUMENT_ID + '/language');
        const data = await res.json();
        if (data.error) {{
            div.innerHTML = '<div style="color:#999;padding:16px;">' + escHtml(data.error) + '</div>';
            return;
        }}
        let html = '';
        // Summary
        const summary = data.summary || {{}};
        if (summary.dominant_language_type || summary.recurring_imagery) {{
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#9c27b0;margin-bottom:8px;">Language Summary</div>';
            if (summary.dominant_language_type) html += '<p style="margin:0 0 8px 0;"><strong>Dominant Language Type:</strong> ' + escHtml(summary.dominant_language_type) + '</p>';
            if (summary.recurring_imagery) html += '<p style="margin:0 0 8px 0;"><strong>Recurring Imagery:</strong> ' + escHtml(summary.recurring_imagery) + '</p>';
            if (summary.attention_pattern) html += '<p style="margin:0;"><strong>Attention Pattern:</strong> ' + escHtml(summary.attention_pattern) + '</p>';
            html += '</div>';
        }}
        // Per-step cards
        const steps = data.enriched_steps || [];
        const paSteps = (data.pass_a && data.pass_a.steps) || [];
        const paLookup = {{}};
        paSteps.forEach(s => {{ paLookup[s.step_number] = s; }});
        steps.forEach(step => {{
            const pa = paLookup[step.step_number] || {{}};
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:14px 18px;margin-bottom:10px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;color:#999;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Step ' + step.step_number + '</div>';
            if (pa.instruction) html += '<div style="font-style:italic;color:#444;margin-bottom:8px;font-size:0.9em;">' + escHtml(pa.instruction) + '</div>';
            const fields = [
                ['Explanatory', 'explanatory_language'], ['Attention Cues', 'attention_cues'],
                ['Effort', 'effort_language'], ['Imagery', 'image_language'],
                ['Distinctive Phrasing', 'distinctive_phrasing'], ['Sensory Inquiry', 'sensory_inquiry'],
            ];
            let bullets = '';
            fields.forEach(([label, key]) => {{
                const val = step[key];
                if (val && val.toLowerCase() !== 'none') {{
                    bullets += '<li><strong>' + label + ':</strong> ' + escHtml(val) + '</li>';
                }}
            }});
            if (bullets) html += '<ul style="margin:0;padding-left:18px;font-size:0.9em;color:#444;line-height:1.5;">' + bullets + '</ul>';
            else html += '<div style="color:#999;font-size:0.9em;">No language data for this step.</div>';
            html += '</div>';
        }});
        if (!steps.length) html += '<div style="color:#999;padding:16px;">No language step data available.</div>';
        div.innerHTML = html;
    }} catch (e) {{
        div.innerHTML = '<div style="color:#999;padding:16px;">Failed to load language data.</div>';
    }}
}}

// ── Classification tab ──

async function loadClassificationTab() {{
    classificationTabLoaded = true;
    const div = document.getElementById('classification-tab-content');
    try {{
        const res = await fetch('/api/lesson/' + DOCUMENT_ID + '/classification');
        const data = await res.json();
        if (data.error) {{
            div.innerHTML = '<div style="color:#999;padding:16px;">' + escHtml(data.error) + '</div>';
            return;
        }}
        let html = '';
        // Framework classification if present
        if (data.primary_framework || data.classification) {{
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#e65100;margin-bottom:8px;">Framework Classification</div>';
            if (data.primary_framework) html += '<p style="margin:0 0 8px 0;"><strong>Primary Framework:</strong> ' + escHtml(data.primary_framework) + '</p>';
            if (data.rationale) html += '<p style="margin:0 0 12px 0;color:#555;font-size:0.9em;">' + escHtml(data.rationale) + '</p>';
            // Bar chart for classification scores
            const scores = data.classification || {{}};
            const entries = Object.entries(scores).sort((a, b) => b[1] - a[1]);
            if (entries.length) {{
                const maxVal = Math.max(...entries.map(e => e[1]), 1);
                html += '<div style="margin-top:8px;">';
                entries.forEach(([name, score]) => {{
                    const pct = Math.round((score / maxVal) * 100);
                    const barColor = name === data.primary_framework ? '#e65100' : '#90a4ae';
                    html += '<div style="display:flex;align-items:center;margin-bottom:6px;">';
                    html += '<div style="width:140px;font-size:0.85em;color:#333;font-weight:500;flex-shrink:0;">' + escHtml(name) + '</div>';
                    html += '<div style="flex:1;height:20px;background:#f0f0f0;border-radius:4px;overflow:hidden;margin:0 10px;">';
                    html += '<div style="width:' + pct + '%;height:100%;background:' + barColor + ';border-radius:4px;transition:width 0.3s;"></div>';
                    html += '</div>';
                    html += '<div style="width:40px;text-align:right;font-size:0.85em;color:#666;">' + (typeof score === 'number' ? score.toFixed(2) : score) + '</div>';
                    html += '</div>';
                }});
                html += '</div>';
            }}
            html += '</div>';
        }}
        // Groups summary
        const groups = data.groups || [];
        if (groups.length) {{
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#1a73e8;margin-bottom:8px;">Movement Groups (' + groups.length + ')</div>';
            groups.forEach(g => {{
                html += '<div style="padding:8px 0;border-bottom:1px solid #f0f0f0;">';
                html += '<strong>Group ' + g.group_number + ':</strong> ' + escHtml(g.label || '');
                if (g.starting_position) html += ' <span style="color:#888;font-size:0.85em;">(' + escHtml(g.starting_position) + ')</span>';
                if (g.description) html += '<div style="color:#555;font-size:0.9em;margin-top:2px;">' + escHtml(g.description) + '</div>';
                html += '</div>';
            }});
            html += '</div>';
        }}
        // Steps count
        const steps = data.steps || [];
        if (steps.length) {{
            html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#333;margin-bottom:8px;">Steps (' + steps.length + ')</div>';
            html += '<div style="font-size:0.9em;color:#555;">Pass A decomposed this lesson into ' + steps.length + ' steps across ' + groups.length + ' groups.</div>';
            html += '</div>';
        }}
        if (!html) html = '<div style="color:#999;padding:16px;">No classification data available.</div>';
        div.innerHTML = html;
    }} catch (e) {{
        div.innerHTML = '<div style="color:#999;padding:16px;">Failed to load classification data.</div>';
    }}
}}

// ── Pipeline tab ──

// ── Pipeline controls (shared by pipeline + raw I/O tabs) ──

let _pipelineLocalControls = null;

async function checkPipelineControls() {{
    if (_pipelineLocalControls !== null) return _pipelineLocalControls;
    try {{
        const res = await fetch('/api/analysis/capabilities');
        const data = await res.json();
        _pipelineLocalControls = data.local_controls || false;
    }} catch {{ _pipelineLocalControls = false; }}
    return _pipelineLocalControls;
}}

let _pipelinePolling = false;

async function runStepForLesson(stepName) {{
    const btn = event.target;
    btn.disabled = true;
    const origText = btn.textContent;
    btn.textContent = '...';
    try {{
        const res = await fetch('/api/analysis/run-step', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{document_id: DOCUMENT_ID, step_name: stepName}})
        }});
        const data = await res.json();
        if (data.status === 'running') {{
            btn.textContent = 'running';
            startPipelinePolling();
        }} else {{
            alert(data.error || 'Error');
            btn.textContent = origText;
            btn.disabled = false;
        }}
    }} catch (e) {{
        alert('Error: ' + e);
        btn.textContent = origText;
        btn.disabled = false;
    }}
}}

function startPipelinePolling() {{
    if (_pipelinePolling) return;
    _pipelinePolling = true;
    _pollPipeline();
}}

async function _pollPipeline() {{
    if (!_pipelinePolling) return;
    try {{
        const res = await fetch('/api/lesson/' + DOCUMENT_ID + '/pipeline-status');
        const data = await res.json();
        const steps = data.steps || [];
        const hasWork = steps.some(s => s.stale || s.error);

        // Refresh pipeline tab
        pipelineTabLoaded = false;
        await loadPipelineTab();

        if (hasWork) {{
            // Still working — poll again
            setTimeout(_pollPipeline, 3000);
        }} else {{
            // All done — stop polling, invalidate other tabs so they reload
            _pipelinePolling = false;
            condensedLoaded = false;
            somaticTabLoaded = false;
            languageTabLoaded = false;
            classificationTabLoaded = false;
            rawioTabLoaded = false;
            // Re-fetch the main analysis data (steps/groups may have changed)
            try {{
                const r2 = await fetch('/api/lesson/' + DOCUMENT_ID + '/combined');
                const newData = await r2.json();
                if (newData && newData.steps) {{
                    analysisData = newData;
                    if (activeTab === 'steps') renderCard();
                }}
            }} catch {{}}
        }}
    }} catch {{
        _pipelinePolling = false;
    }}
}}

async function loadPipelineTab() {{
    pipelineTabLoaded = true;
    const div = document.getElementById('pipeline-tab-content');
    const localControls = await checkPipelineControls();
    try {{
        const res = await fetch('/api/lesson/' + DOCUMENT_ID + '/pipeline-status');
        const data = await res.json();
        if (data.error) {{
            div.innerHTML = '<div style="color:#999;padding:16px;">' + escHtml(data.error) + '</div>';
            return;
        }}
        let html = '';
        // Document metadata
        const doc = data.document || {{}};
        html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
        html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#333;margin-bottom:8px;">Document Metadata</div>';
        html += '<table style="font-size:0.9em;color:#444;border-collapse:collapse;width:100%;">';
        html += '<tr><td style="padding:4px 12px 4px 0;font-weight:600;color:#666;white-space:nowrap;">Document ID</td><td style="padding:4px 0;font-family:monospace;font-size:0.9em;">' + escHtml(doc.document_id || '') + '</td></tr>';
        html += '<tr><td style="padding:4px 12px 4px 0;font-weight:600;color:#666;">Title</td><td style="padding:4px 0;">' + escHtml(doc.title || '') + '</td></tr>';
        html += '<tr><td style="padding:4px 12px 4px 0;font-weight:600;color:#666;">Created</td><td style="padding:4px 0;">' + escHtml(doc.created_at || 'N/A') + '</td></tr>';
        html += '<tr><td style="padding:4px 12px 4px 0;font-weight:600;color:#666;">Status</td><td style="padding:4px 0;">' + escHtml(doc.status || 'N/A') + '</td></tr>';
        if (doc.s3_key) html += '<tr><td style="padding:4px 12px 4px 0;font-weight:600;color:#666;">Media</td><td style="padding:4px 0;font-family:monospace;font-size:0.85em;">' + escHtml(doc.s3_key) + '</td></tr>';
        html += '</table></div>';
        // Pipeline steps
        const steps = data.steps || [];
        html += '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
        html += '<div style="font-size:0.75em;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#333;margin-bottom:12px;">Analysis Steps</div>';
        html += '<table style="font-size:0.85em;border-collapse:collapse;width:100%;">';
        html += '<tr style="border-bottom:2px solid #e0e0e0;">';
        html += '<th style="text-align:left;padding:6px 10px;color:#666;font-weight:600;">Step</th>';
        html += '<th style="text-align:center;padding:6px 10px;color:#666;font-weight:600;">Status</th>';
        html += '<th style="text-align:center;padding:6px 10px;color:#666;font-weight:600;">Version</th>';
        html += '<th style="text-align:left;padding:6px 10px;color:#666;font-weight:600;">Completed</th>';
        html += '<th style="text-align:left;padding:6px 10px;color:#666;font-weight:600;">Details</th>';
        if (localControls) html += '<th style="text-align:center;padding:6px 10px;color:#666;font-weight:600;"></th>';
        html += '</tr>';
        steps.forEach(step => {{
            let statusBadge, statusColor;
            if (step.error) {{
                statusBadge = 'Error';
                statusColor = '#c62828';
            }} else if (step.stale) {{
                statusBadge = 'Stale';
                statusColor = '#f57f17';
            }} else if (step.completed_version != null) {{
                if (step.completed_version < step.current_version) {{
                    statusBadge = 'Outdated';
                    statusColor = '#f57f17';
                }} else {{
                    statusBadge = 'Done';
                    statusColor = '#2e7d32';
                }}
            }} else {{
                statusBadge = 'Pending';
                statusColor = '#757575';
            }}
            const versionStr = step.completed_version != null ? 'v' + step.completed_version + ' / v' + step.current_version : '- / v' + step.current_version;
            const completedStr = step.completed_at ? new Date(step.completed_at).toLocaleString() : '-';
            let detailStr = '';
            if (step.error) detailStr = '<span style="color:#c62828;">' + escHtml(step.error) + '</span>';
            else if (step.depends_on && step.depends_on.length) detailStr = '<span style="color:#888;font-size:0.9em;">depends: ' + step.depends_on.join(', ') + '</span>';
            html += '<tr style="border-bottom:1px solid #f0f0f0;">';
            html += '<td style="padding:8px 10px;font-weight:500;">' + escHtml(step.step_name) + '</td>';
            html += '<td style="padding:8px 10px;text-align:center;"><span style="display:inline-block;padding:2px 10px;border-radius:10px;font-size:0.8em;font-weight:600;color:#fff;background:' + statusColor + ';">' + statusBadge + '</span></td>';
            html += '<td style="padding:8px 10px;text-align:center;font-family:monospace;font-size:0.85em;">' + versionStr + '</td>';
            html += '<td style="padding:8px 10px;">' + completedStr + '</td>';
            html += '<td style="padding:8px 10px;">' + detailStr + '</td>';
            if (localControls) {{
                html += '<td style="padding:8px 10px;text-align:center;">';
                html += '<button onclick="runStepForLesson(\\'' + step.step_name + '\\')" '
                    + 'style="font-size:0.7em;padding:2px 8px;border:1px solid #007bff;border-radius:4px;background:white;color:#007bff;cursor:pointer;"'
                    + '>&#9654; run</button>';
                html += '</td>';
            }}
            html += '</tr>';
        }});
        html += '</table></div>';
        div.innerHTML = html;

        // If we're polling from a run-step trigger, the poll loop handles refresh.
        // No need for independent auto-refresh here.
    }} catch (e) {{
        div.innerHTML = '<div style="color:#999;padding:16px;">Failed to load pipeline status.</div>';
    }}
}}

// ── Raw I/O tab ──

async function loadRawIOTab() {{
    rawioTabLoaded = true;
    const div = document.getElementById('rawio-tab-content');
    try {{
        const res = await fetch('/api/lesson/' + DOCUMENT_ID + '/raw-io');
        const data = await res.json();
        if (data.error) {{
            div.innerHTML = '<div style="color:#999;padding:16px;">' + escHtml(data.error) + '</div>';
            return;
        }}
        const steps = data.steps || [];
        if (!steps.length) {{
            div.innerHTML = '<div style="color:#999;padding:16px;">No analysis data found for this lesson.</div>';
            return;
        }}
        let html = '';
        const preStyle = 'background:#f8f9fa;border:1px solid #e9ecef;border-radius:6px;padding:12px;font-size:0.8em;overflow-x:auto;max-height:500px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;';
        const sectionLabel = 'display:inline-block;padding:2px 8px;border-radius:6px;font-size:0.75em;font-weight:600;margin-bottom:4px;';
        steps.forEach(step => {{
            html += '<details style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;margin-bottom:10px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">';
            html += '<summary style="padding:14px 18px;cursor:pointer;font-weight:600;color:#333;font-size:0.95em;">';
            html += escHtml(step.name);
            if (step.has_data) {{
                html += ' <span style="display:inline-block;padding:1px 8px;border-radius:8px;font-size:0.75em;font-weight:600;color:#fff;background:#2e7d32;margin-left:8px;">cached</span>';
                if (step.raw_json_length) html += ' <span style="color:#888;font-size:0.8em;font-weight:400;">(' + (step.raw_json_length / 1024).toFixed(1) + ' KB)</span>';
            }} else {{
                html += ' <span style="display:inline-block;padding:1px 8px;border-radius:8px;font-size:0.75em;font-weight:600;color:#fff;background:#757575;margin-left:8px;">not cached</span>';
            }}
            if (step.stale) {{
                html += ' <span style="display:inline-block;padding:1px 8px;border-radius:8px;font-size:0.75em;font-weight:600;color:#856404;background:#fff3cd;margin-left:4px;">stale</span>';
            }}
            if (step.error) {{
                html += ' <span style="display:inline-block;padding:1px 8px;border-radius:8px;font-size:0.75em;font-weight:600;color:#fff;background:#dc3545;margin-left:4px;">error</span>';
            }}
            if (step.has_prompt) {{
                html += ' <span style="display:inline-block;padding:1px 8px;border-radius:8px;font-size:0.75em;font-weight:600;color:#fff;background:#1565c0;margin-left:4px;">prompt</span>';
            }}
            html += '</summary>';
            html += '<div style="padding:0 18px 14px 18px;">';

            // --- Prompt / System / Schema section ---
            if (step.has_prompt) {{
                html += '<div style="margin-bottom:12px;">';
                html += '<div style="' + sectionLabel + 'color:#1565c0;background:#e3f2fd;">System Prompt</div>';
                html += '<pre style="' + preStyle + 'max-height:200px;border-color:#bbdefb;">' + escHtml(step.system || '') + '</pre>';
                html += '</div>';

                html += '<details style="margin-bottom:12px;">';
                html += '<summary style="cursor:pointer;font-size:0.85em;font-weight:600;color:#1565c0;">User Prompt <span style="color:#888;font-weight:400;">(' + (step.prompt.length / 1024).toFixed(1) + ' KB)</span></summary>';
                html += '<pre style="' + preStyle + 'max-height:600px;border-color:#bbdefb;">' + escHtml(step.prompt || '') + '</pre>';
                html += '</details>';

                if (step.schema) {{
                    html += '<details style="margin-bottom:12px;">';
                    html += '<summary style="cursor:pointer;font-size:0.85em;font-weight:600;color:#1565c0;">JSON Schema</summary>';
                    html += '<pre style="' + preStyle + 'max-height:400px;border-color:#bbdefb;">' + escHtml(step.schema) + '</pre>';
                    html += '</details>';
                }}

                html += '<hr style="border:none;border-top:1px solid #e0e0e0;margin:12px 0;">';
            }}

            // --- Output section ---
            if (step.has_data) {{
                html += '<div style="' + sectionLabel + 'color:#2e7d32;background:#e8f5e9;">Output</div>';
                if (step.top_level_keys && step.top_level_keys.length) {{
                    html += '<div style="margin-bottom:8px;font-size:0.85em;color:#666;">Keys: <code>' + step.top_level_keys.join(', ') + '</code></div>';
                }}
                html += '<div style="font-size:0.8em;color:#888;margin-bottom:4px;">Cache key: <code>' + escHtml(step.cache_key) + '</code>';
                if (step.truncated) html += ' <span style="color:#f57f17;">(truncated to 50KB)</span>';
                html += '</div>';
                html += '<pre style="' + preStyle + '">' + escHtml(step.raw_json || '') + '</pre>';
            }} else {{
                html += '<div style="color:#999;font-size:0.9em;">No cached data. Run the analysis pipeline to populate this step.</div>';
            }}
            html += '</div></details>';
        }});
        div.innerHTML = html;

    }} catch (e) {{
        div.innerHTML = '<div style="color:#999;padding:16px;">Failed to load raw I/O data.</div>';
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
        html += '<div class="group-tag">Group ' + card.group_number + ': ' + escHtml(card.group_label || '') + '</div>';
        // Timestamps + step range
        const tr = getGroupTimeRange(card.group_number);
        const adGrp = analysisData && analysisData.groups ? analysisData.groups.find(g => g.group_number === card.group_number) : null;
        const stepNums = adGrp ? (adGrp.steps || []) : [];
        const metaParts = [];
        if (tr) metaParts.push(fmtTime(tr.start) + ' \\u2013 ' + fmtTime(tr.end));
        if (stepNums.length === 1) metaParts.push('step ' + stepNums[0]);
        else if (stepNums.length > 1) metaParts.push('steps ' + stepNums[0] + '\\u2013' + stepNums[stepNums.length - 1]);
        if (metaParts.length) html += '<div style="font-style:italic;font-size:0.72em;color:#bbb;font-weight:400;margin-bottom:4px;">' + metaParts.join(' \\u00a0\\u00b7\\u00a0 ') + '</div>';
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
        const res = await fetch('/api/lesson/' + encodeURIComponent(DOCUMENT_ID) + '/condensed');
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
                document_id: DOCUMENT_ID,
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
        showStatus('<span class="spinner-inline"></span> Loading analysis...');
        const res = await fetch('/api/lesson/' + encodeURIComponent(DOCUMENT_ID) + '/combined');
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


def build_fmtt_page_html(
    user: dict | None,
    superuser_active: bool,
    fmtt_lessons: list,
) -> str:
    """Dedicated FMTT browse page: lessons grouped by segment, sorted numerically.

    fmtt_lessons: list of dicts each containing
        id, title, lesson_date (iso str or None), segment_label,
        presenter (str or None), origin (str or None), lesson_type (str or None)
    """
    auth_bar = _auth_bar_html(user, superuser_active=superuser_active)

    import re as _re
    # Group by segment
    by_segment: dict = {}
    for L in fmtt_lessons:
        seg = L.get("segment_label") or "(no segment)"
        by_segment.setdefault(seg, []).append(L)

    def seg_sort_key(label):
        m = _re.match(r"\s*Segment\s+(\d+)", label)
        return (int(m.group(1)) if m else 9999, label)

    sections_html = []
    for seg in sorted(by_segment.keys(), key=seg_sort_key):
        items = by_segment[seg]
        items.sort(key=lambda L: (L.get("lesson_date") or "", L.get("title") or ""))
        rows = []
        for L in items:
            doc_id = html_mod.escape(str(L["id"]))
            title = html_mod.escape(L.get("title") or "(untitled)")
            ltype = (L.get("lesson_type") or "").strip()
            type_class = ltype.lower().replace(" ", "-") if ltype else ""
            type_pill = (
                f'<span class="type-pill type-{html_mod.escape(type_class)}">'
                f'{html_mod.escape(ltype)}</span>'
                if ltype else ""
            )
            extras = []
            if L.get("presenter"): extras.append(html_mod.escape(L["presenter"]))
            if L.get("origin"):    extras.append(html_mod.escape(L["origin"]))
            extra_line = (
                f'<div class="lesson-extra">{" · ".join(extras)}</div>'
                if extras else ""
            )
            rows.append(
                f'<li class="lesson-row">{type_pill}'
                f'<a href="/lesson/{doc_id}" class="lesson-title-link">{title}</a>'
                f'{extra_line}</li>'
            )
        sections_html.append(
            f'<section class="segment-block">'
            f'<h2 class="segment-heading">{html_mod.escape(seg)} '
            f'<span class="count">({len(items)})</span></h2>'
            f'<ul class="lesson-ul">{"".join(rows)}</ul>'
            f'</section>'
        )

    total = len(fmtt_lessons)
    n_segments = len(by_segment)

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Feldenkrais Manhattan Training</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 1000px; margin: 0 auto; padding: 24px; color: #222; }}
        h1 {{ margin: 0 0 4px; font-size: 1.7em; }}
        .subtitle {{ color: #888; font-size: 0.95em; margin-bottom: 28px; }}
        .segment-block {{ margin-bottom: 28px; border: 1px solid #e7e7e7; border-radius: 10px;
                          padding: 18px 22px; background: #fff; }}
        .segment-heading {{ font-size: 1.1em; color: #1a1a1a; margin: 0 0 14px;
                            padding-bottom: 8px; border-bottom: 1px solid #f0f0f0; }}
        .segment-heading .count {{ color: #999; font-weight: 400; font-size: 0.85em;
                                    margin-left: 6px; }}
        .lesson-ul {{ list-style: none; padding: 0; margin: 0; }}
        .lesson-row {{ padding: 9px 0; border-top: 1px solid #f4f4f4;
                       display: grid; grid-template-columns: 90px 1fr;
                       gap: 10px; align-items: baseline; }}
        .lesson-row:first-child {{ border-top: 0; }}
        .type-pill {{ display: inline-block; padding: 2px 8px; font-size: 0.72em;
                      border-radius: 10px; color: #fff; background: #888;
                      text-transform: uppercase; letter-spacing: 0.04em; text-align: center;
                      align-self: start; justify-self: start; }}
        .type-pill:empty {{ display: none; }}
        .type-pill.type-atm {{ background: #007bff; }}
        .type-pill.type-discussion {{ background: #6c757d; }}
        .type-pill.type-fi-demo {{ background: #20c997; }}
        .type-pill.type-lecture {{ background: #fd7e14; }}
        .type-pill.type-practice {{ background: #9b59b6; }}
        .lesson-title-link {{ color: #1565c0; text-decoration: none;
                              font-size: 0.97em; line-height: 1.4; }}
        .lesson-title-link:hover {{ text-decoration: underline; }}
        .lesson-extra {{ color: #777; font-size: 0.82em; margin-top: 2px; grid-column: 2; }}
{_AUTH_BAR_CSS}
    </style>
</head>
<body>
    {auth_bar}
    <h1>Feldenkrais Manhattan Training</h1>
    <p class="subtitle">{total} lessons across {n_segments} segments. Click any lesson for full analysis, transcript, and Q&amp;A.</p>
    {"".join(sections_html) if sections_html else '<p style="color:#888;padding:20px;">No FMTT lessons available to you.</p>'}
</body>
</html>"""


def build_conversations_page_html(user: dict, superuser_active: bool = False) -> str:
    """Central page listing the user's chat history grouped by lesson.

    Superusers in superuser mode see every user's chats; everyone else sees
    only their own.
    """
    auth_bar = _auth_bar_html(user, superuser_active)
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Conversations</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 900px; margin: 0 auto; padding: 20px; color: #222; }}
        h1 {{ font-size: 1.6em; margin: 0 0 4px 0; }}
        .subtitle {{ color: #888; font-size: 0.9em; margin-bottom: 20px; }}
        .lesson-card {{ border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px;
                        margin: 14px 0; background: #fff; transition: box-shadow 0.15s; }}
        .lesson-card:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .lesson-card-header {{ display: flex; justify-content: space-between; align-items: baseline;
                                cursor: pointer; }}
        .lesson-title {{ font-weight: 600; color: #007bff; text-decoration: none; font-size: 1.05em; }}
        .lesson-title:hover {{ text-decoration: underline; }}
        .lesson-meta {{ color: #888; font-size: 0.85em; white-space: nowrap; }}
        .turn {{ border-left: 3px solid #007bff; padding: 8px 14px; margin: 12px 0;
                 background: #f8f9fa; border-radius: 0 6px 6px 0; }}
        .turn-ts {{ font-size: 0.78em; color: #888; margin-bottom: 6px; }}
        .turn-q {{ font-weight: 600; color: #333; margin-bottom: 8px; white-space: pre-wrap; }}
        .turn-a {{ color: #555; white-space: pre-wrap; line-height: 1.5; font-size: 0.95em; }}
        .turns-container {{ margin-top: 12px; display: none; }}
        .turns-container.open {{ display: block; }}
        .toggle-hint {{ color: #888; font-size: 0.85em; user-select: none; }}
        #status {{ color: #888; padding: 20px; text-align: center; }}
    </style>
</head>
<body>
    {auth_bar}
    <h1>Conversations</h1>
    <div class="subtitle" id="subtitle">Loading…</div>
    <div id="lessons"></div>
    <div id="status"></div>

<script>
function escHtml(s) {{
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}}

async function toggleLesson(cardId, docId, userId) {{
    const container = document.getElementById('turns-' + cardId);
    const hint = document.getElementById('hint-' + cardId);
    if (container.classList.contains('open')) {{
        container.classList.remove('open');
        hint.textContent = 'Show turns ▾';
        return;
    }}
    if (!container.dataset.loaded) {{
        container.innerHTML = '<div style="color:#888;padding:8px;">Loading turns…</div>';
        try {{
            const res = await fetch('/api/lesson-chat-history/' + encodeURIComponent(docId));
            const data = await res.json();
            const filtered = (data.turns || []).filter(t => userId === null || t.user_id === userId);
            if (filtered.length === 0) {{
                container.innerHTML = '<div style="color:#999;padding:8px;">(no turns)</div>';
            }} else {{
                container.innerHTML = filtered.map(t => {{
                    const ts = t.created_at ? new Date(t.created_at).toLocaleString() : '';
                    return '<div class="turn">' +
                        '<div class="turn-ts">' + escHtml(ts) + '</div>' +
                        '<div class="turn-q">' + escHtml(t.query) + '</div>' +
                        '<div class="turn-a">' + escHtml(t.response) + '</div>' +
                        '</div>';
                }}).join('');
            }}
            container.dataset.loaded = '1';
        }} catch (e) {{
            container.innerHTML = '<div style="color:#c62828;padding:8px;">Failed: ' + escHtml(e.message) + '</div>';
        }}
    }}
    container.classList.add('open');
    hint.textContent = 'Hide turns ▴';
}}

(async function init() {{
    try {{
        const res = await fetch('/api/conversations');
        const data = await res.json();
        const lessons = data.lessons || [];
        document.getElementById('subtitle').textContent = data.is_superuser_view
            ? 'Superuser view — showing every user\\'s chats.'
            : 'Your chat history, grouped by lesson.';
        const container = document.getElementById('lessons');
        if (lessons.length === 0) {{
            container.innerHTML = '<div style="color:#888;padding:20px;">No conversations yet.</div>';
            document.getElementById('status').textContent = '';
            return;
        }}
        const parts = [];
        lessons.forEach((l, i) => {{
            const cardId = 'c' + i;
            const ts = l.last_chat_at ? new Date(l.last_chat_at).toLocaleString() : '';
            const userPart = data.is_superuser_view && l.user_email
                ? ' · ' + escHtml(l.user_email)
                : '';
            const userIdJs = data.is_superuser_view ? (l.user_id == null ? 'null' : l.user_id) : 'null';
            parts.push(
                '<div class="lesson-card">' +
                '<div class="lesson-card-header" onclick="toggleLesson(\\'' + cardId + '\\', \\'' +
                    escHtml(l.document_id) + '\\', ' + userIdJs + ')">' +
                '<div><a class="lesson-title" href="/lesson/' + escHtml(l.document_id) + '" onclick="event.stopPropagation()">' +
                    escHtml(l.title) + '</a>' +
                '<span class="toggle-hint" id="hint-' + cardId + '" style="margin-left:12px;">Show turns ▾</span></div>' +
                '<div class="lesson-meta">' + escHtml(ts) + ' · ' + l.turn_count + ' turn' +
                    (l.turn_count === 1 ? '' : 's') + escHtml(userPart) + '</div>' +
                '</div>' +
                '<div class="turns-container" id="turns-' + cardId + '"></div>' +
                '</div>'
            );
        }});
        container.innerHTML = parts.join('');
        document.getElementById('status').textContent = '';
    }} catch (e) {{
        document.getElementById('status').textContent = 'Failed to load: ' + e.message;
    }}
}})();
</script>
</body>
</html>"""

