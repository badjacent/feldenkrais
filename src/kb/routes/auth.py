"""Auth routes: OAuth login/callback, logout, superuser toggle."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

import web as _web

router = APIRouter()


@router.get("/auth/github/login")
async def auth_github_login(request: Request):
    return _web._auth_mod.github_login_redirect(request)


@router.get("/auth/github/callback")
async def auth_github_callback(request: Request):
    return _web._auth_mod.github_oauth_callback(request, _web._db)


@router.get("/auth/google/login")
async def auth_google_login(request: Request):
    return _web._auth_mod.google_login_redirect(request)


@router.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    return _web._auth_mod.google_oauth_callback(request, _web._db)


@router.get("/auth/logout")
async def auth_logout(request: Request):
    return _web._auth_mod.logout(request)


@router.get("/auth/superuser/on")
async def superuser_on(request: Request):
    """Enable superuser mode (for eligible users only)."""
    user = _web._auth_mod.get_current_user(request)
    if not user or not user.get("superuser_eligible"):
        return RedirectResponse("/")
    response = RedirectResponse("/")
    response.set_cookie(
        _web.SUPERUSER_COOKIE, "1",
        max_age=30 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=_web._auth_mod._is_secure(request),
    )
    return response


@router.get("/auth/superuser/off")
async def superuser_off(request: Request):
    """Disable superuser mode."""
    response = RedirectResponse("/")
    response.delete_cookie(_web.SUPERUSER_COOKIE)
    return response
