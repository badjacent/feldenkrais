"""OAuth (GitHub + Google) and cookie-based session management."""

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
COOKIE_SECRET = os.environ.get("COOKIE_SECRET", "")

COOKIE_NAME = "fk_session"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days


# ── Cookie helpers ────────────────────────────────────────────────────

def _sign(payload_b64: str) -> str:
    return hmac.new(COOKIE_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()


def encode_cookie(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def decode_cookie(cookie: str) -> dict | None:
    if not cookie or "." not in cookie or not COOKIE_SECRET:
        return None
    payload, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


# ── Request helpers ───────────────────────────────────────────────────

def get_current_user(request: Request) -> dict | None:
    """Return {user_id, tier, login} or None."""
    cookie = request.cookies.get(COOKIE_NAME)
    return decode_cookie(cookie) if cookie else None


def get_access_tier(request: Request) -> str:
    """Return 'anonymous', 'user', or 'admin'."""
    user = get_current_user(request)
    return user["tier"] if user else "anonymous"


# ── Shared helpers ────────────────────────────────────────────────────

def _is_secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


def _base_url(request: Request) -> str:
    scheme = "https" if _is_secure(request) else "http"
    host = request.headers.get("host", "localhost:8000")
    return f"{scheme}://{host}"


def _set_session_cookie(user: dict, request: Request) -> RedirectResponse:
    cookie_data = {
        "user_id": user["id"],
        "tier": user["tier"],
        "login": user["login"],
        "exp": int(time.time()) + COOKIE_MAX_AGE,
    }
    response = RedirectResponse("/")
    response.set_cookie(
        COOKIE_NAME,
        encode_cookie(cookie_data),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
    )
    return response


# ── GitHub OAuth ──────────────────────────────────────────────────────

def github_login_redirect(request: Request) -> RedirectResponse:
    callback = f"{_base_url(request)}/auth/github/callback"
    params = urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": callback,
        "scope": "read:user user:email",
    })
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{params}")


def github_oauth_callback(request: Request, db) -> RedirectResponse:
    code = request.query_params.get("code")
    if not code:
        return RedirectResponse("/")

    token_resp = httpx.post(
        "https://github.com/login/oauth/access_token",
        json={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code,
        },
        headers={"Accept": "application/json"},
    )
    access_token = token_resp.json().get("access_token")
    if not access_token:
        return RedirectResponse("/")

    gh = httpx.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    ).json()

    # Allowlist check (GitHub username)
    if not db.is_login_allowed(gh["login"]):
        return RedirectResponse("/?login_denied=1")

    user = db.upsert_user(
        provider="github",
        provider_id=str(gh["id"]),
        login=gh["login"],
        name=gh.get("name"),
        email=gh.get("email"),
        avatar_url=gh.get("avatar_url"),
    )
    return _set_session_cookie(user, request)


# ── Google OAuth ──────────────────────────────────────────────────────

def google_login_redirect(request: Request) -> RedirectResponse:
    callback = f"{_base_url(request)}/auth/google/callback"
    params = urlencode({
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": callback,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


def google_oauth_callback(request: Request, db) -> RedirectResponse:
    code = request.query_params.get("code")
    if not code:
        return RedirectResponse("/")

    callback = f"{_base_url(request)}/auth/google/callback"
    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": callback,
        },
    )
    access_token = token_resp.json().get("access_token")
    if not access_token:
        return RedirectResponse("/")

    guser = httpx.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()

    email = guser.get("email", "")

    # Allowlist check (Google email)
    if not db.is_login_allowed(email):
        return RedirectResponse("/?login_denied=1")

    user = db.upsert_user(
        provider="google",
        provider_id=guser["id"],
        login=email,
        name=guser.get("name"),
        email=email,
        avatar_url=guser.get("picture"),
    )
    return _set_session_cookie(user, request)


# ── Logout ────────────────────────────────────────────────────────────

def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse("/")
    response.delete_cookie(COOKIE_NAME)
    return response
