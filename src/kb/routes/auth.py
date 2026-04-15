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


@router.get("/guest/{token}")
async def guest_login(token: str, request: Request, redirect: str = "/"):
    """Log in as a guest user via token link."""
    result = _web._auth_mod.guest_login(token, redirect, request, _web._db)
    if result:
        return result
    return RedirectResponse("/")


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


IMPERSONATE_COOKIE = "fk_real_session"


@router.get("/auth/impersonate/back")
async def impersonate_back(request: Request):
    """Restore the original session after impersonation."""
    real_cookie = request.cookies.get(IMPERSONATE_COOKIE, "")
    if not real_cookie:
        return RedirectResponse("/")

    response = RedirectResponse("/")
    response.set_cookie(
        _web._auth_mod.COOKIE_NAME,
        real_cookie,
        max_age=_web._auth_mod.COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_web._auth_mod._is_secure(request),
    )
    response.delete_cookie(IMPERSONATE_COOKIE)
    return response


@router.get("/auth/impersonate/{login}")
async def impersonate(login: str, request: Request):
    """Switch to a different user (admin/superuser only). Saves real session for restore."""
    user = _web._auth_mod.get_current_user(request)
    if not user or user.get("tier") != "admin":
        if not user or not user.get("superuser_eligible"):
            return RedirectResponse("/")

    # Look up the target user
    if not _web._db:
        return RedirectResponse("/")
    with _web._db.get_cursor(commit=False) as cur:
        cur.execute("SELECT id, login, name, tier, superuser_eligible FROM users WHERE login = %s", (login,))
        target = cur.fetchone()
    if not target:
        return RedirectResponse("/")

    # Save current session cookie so we can restore it
    real_cookie = request.cookies.get(_web._auth_mod.COOKIE_NAME, "")

    # Set new session as the target user
    import time
    cookie_data = {
        "user_id": target["id"],
        "tier": target["tier"],
        "login": target["login"],
        "superuser_eligible": bool(target.get("superuser_eligible", False)),
        "exp": int(time.time()) + _web._auth_mod.COOKIE_MAX_AGE,
    }
    response = RedirectResponse("/")
    response.set_cookie(
        _web._auth_mod.COOKIE_NAME,
        _web._auth_mod.encode_cookie(cookie_data),
        max_age=_web._auth_mod.COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_web._auth_mod._is_secure(request),
    )
    # Save real session in a separate cookie
    response.set_cookie(
        IMPERSONATE_COOKIE,
        real_cookie,
        max_age=_web._auth_mod.COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_web._auth_mod._is_secure(request),
    )
    return response
