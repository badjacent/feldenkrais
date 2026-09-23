"""Landing page route."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

import web as _web

router = APIRouter()


@router.get("/fmtt", response_class=HTMLResponse)
async def fmtt_page(request: Request):
    """Dedicated Feldenkrais Manhattan Training browse page.

    Restricted to users who have access to source_id=3 (FMTT). Superusers/admins
    see everything; everyone else gets a 404 unless their group grants access.
    """
    user = _web._set_source_access(request)
    su_active = _web._is_superuser_active(request, user)
    allowed = _web._db.allowed_source_ids if _web._db else set()
    if allowed is not None and 3 not in allowed:
        return HTMLResponse("<h1>Not Found</h1>", status_code=404)

    lessons = _web._db.get_fmtt_lessons() if _web._db else []
    return HTMLResponse(_web.build_fmtt_page_html(
        user=user,
        superuser_active=su_active,
        fmtt_lessons=lessons,
    ))


@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Landing page: search home, plus dev tools for admin or local dev."""
    # Interiority subdomain serves the research booklet
    if _web._is_interiority_site(request):
        candidates = [
            Path(__file__).parent.parent / "docs" / "interiority" / "index.html",
            Path(__file__).parent.parent.parent.parent / "docs" / "interiority" / "index.html",
        ]
        for p in candidates:
            if p.exists():
                return HTMLResponse(p.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Coming soon</h1>", status_code=404)

    # Finder subdomain gets its own UI
    if _web._is_finder_site(request):
        return HTMLResponse(_web._build_finder_html())
    user = _web._set_source_access(request)
    search_mode = _web._is_search_site(request)
    su_active = _web._is_superuser_active(request, user)
    # Search subdomain: always show clean search page, no dev tools
    if search_mode:
        return HTMLResponse(_web.build_search_home_html(user=user, superuser_active=su_active, get_lessons_fn=_web.get_available_lessons))
    show_dev = not _web.IS_LAMBDA or (user and user.get("tier") == "admin")
    if not show_dev:
        return HTMLResponse(_web.build_search_home_html(user=user, superuser_active=su_active, get_lessons_fn=_web.get_available_lessons))
    # Show dev tool links for admin or local dev
    links = []
    if _web._book_available:
        links.append('<li><a href="/book"><strong>Book</strong></a></li>')
        links.append('<li><a href="/explore">Explore</a></li>')
    links.append('<li><a href="/ingestion">Ingestion Dashboard</a></li>')
    # The analysis pipeline is visible only on local deployments (laptop, Mac mini).
    if _web.PIPELINE_LOCAL_CONTROLS:
        links.append('<li><a href="/analysis">Analysis Pipeline</a></li>')
        if _web._book_available:
            links.append('<li><a href="/pipeline">Pipeline Dashboard</a></li>')
    if user and user.get("tier") == "admin":
        links.append('<li><a href="/admin/nyc">NYC Directory (crawler admin)</a></li>')
    dev_section = f"""<h2 style="margin-top:40px; border-top:1px solid #e0e0e0; padding-top:20px;">Dev Tools</h2>
<ul style="list-style:none; padding:0;">{"".join(links)}</ul>"""
    return HTMLResponse(_web.build_search_home_html(extra_html=dev_section, user=user, superuser_active=su_active, get_lessons_fn=_web.get_available_lessons))
