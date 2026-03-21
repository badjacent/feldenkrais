"""Admin routes: cache management, version endpoint, docs page."""

import json
import os
from pathlib import Path

import html as html_mod

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

import web as _web

router = APIRouter()

DOCS_DIR = Path(__file__).parent.parent.parent.parent / "docs" if not _web.IS_LAMBDA else Path(__file__).parent.parent / "docs"


@router.post("/admin/clear-cache")
async def admin_clear_cache(request: Request):
    """Clear all cached analysis results."""
    if _web.IS_LAMBDA and _web._auth_mod.get_access_tier(request) != "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if _web._db:
        count = _web._db.clear_cache()
    else:
        count = 0
    return JSONResponse({"cleared": count})


@router.get("/admin/cache")
async def admin_cache_list(request: Request):
    """List cached entries in database."""
    if _web.IS_LAMBDA and _web._auth_mod.get_access_tier(request) != "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if _web._db:
        entries = _web._db.list_cache()
        for e in entries:
            e["size_kb"] = round(e.pop("size_bytes", 0) / 1024, 1)
            for k in ("created_at", "updated_at"):
                if e.get(k):
                    e[k] = str(e[k])
    else:
        entries = []
    return JSONResponse({"count": len(entries), "entries": entries})


@router.get("/version")
async def version():
    sha = os.environ.get("DEPLOY_SHA", "local")
    return {"sha": sha, "short": sha[:7] if len(sha) > 7 else sha}


@router.get("/docs/{slug}", response_class=HTMLResponse)
async def docs_page(request: Request, slug: str):
    """Render a markdown doc file."""
    if not all(c.isalnum() or c in "-_" for c in slug):
        return HTMLResponse("Not found", status_code=404)
    md_path = DOCS_DIR / f"{slug}.md"
    if not md_path.exists():
        return HTMLResponse("Not found", status_code=404)
    md_content = md_path.read_text()
    user = _web._auth_mod.get_current_user(request) if _web._auth_mod else None
    if user:
        login = html_mod.escape(user.get("login", ""))
        su_parts = []
        if user.get("superuser_eligible"):
            if _web._is_superuser_active(request, user):
                su_parts.append('<a href="/auth/superuser/off" style="color:#d32f2f; font-weight:600;">Exit Superuser</a>')
            else:
                su_parts.append('<a href="/auth/superuser/on">Superuser</a>')
        su_html = (' &middot; ' + ' &middot; '.join(su_parts)) if su_parts else ''
        auth_bar = f'<div class="auth-bar">{login}{su_html} &middot; <a href="/auth/logout">Log out</a> &middot; <a href="/">Search</a></div>'
    else:
        auth_bar = ''
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Docs</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }}
    {_web._AUTH_BAR_CSS}
    h1 {{ border-bottom: 2px solid #eee; padding-bottom: 8px; }}
    h2 {{ border-bottom: 1px solid #eee; padding-bottom: 4px; margin-top: 2em; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
    th {{ background: #f5f5f5; }}
    code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
    pre {{ background: #f5f5f5; padding: 12px; border-radius: 4px; overflow-x: auto; }}
    pre code {{ background: none; padding: 0; }}
    a {{ color: #007bff; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
</style>
</head><body>
{auth_bar}
<div id="content"></div>
<script>
    const md = {json.dumps(md_content)};
    document.getElementById('content').innerHTML = marked.parse(md);
    // Set page title from first h1
    const h1 = document.querySelector('#content h1');
    if (h1) document.title = h1.textContent;
</script>
</body></html>""")
