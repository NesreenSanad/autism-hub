"""Admin login. The web app sends email and password to /auth/login; we pass
them to Supabase Auth and return its token only if the email is in
ADMIN_EMAILS. Later requests carry the token; we ask Supabase who it belongs
to. The web app never talks to Supabase itself. No password is stored here."""
import time
from typing import Dict, Optional, Tuple

import httpx
from fastapi import Header, HTTPException

from app.config import get_settings

_CACHE_SECONDS = 60
_cache: Dict[str, Tuple[str, float]] = {}


def _check_setup():
    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_anon_key and settings.admin_email_list):
        raise HTTPException(503, "Admin login is not set up: SUPABASE_URL, SUPABASE_ANON_KEY and ADMIN_EMAILS")
    return settings


def _supabase(method: str, path: str, token: Optional[str] = None, **kwargs) -> httpx.Response:
    settings = get_settings()
    headers = {"apikey": settings.supabase_anon_key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        return httpx.request(method, f"{settings.supabase_url.rstrip('/')}/auth/v1{path}",
                             headers=headers, timeout=10, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Can't reach Supabase Auth: {exc}") from exc


def _user_email(token: str) -> Optional[str]:
    hit = _cache.get(token)
    if hit and hit[1] > time.monotonic():
        return hit[0]
    resp = _supabase("GET", "/user", token)
    if resp.status_code != 200:
        return None
    email = (resp.json().get("email") or "").strip().lower()
    if len(_cache) > 100:
        _cache.clear()
    _cache[token] = (email, time.monotonic() + _CACHE_SECONDS)
    return email


def _session(resp: httpx.Response) -> dict:
    """Supabase's token response, trimmed to what the web app needs."""
    if resp.status_code != 200:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        message = body.get("error_description") or body.get("msg") or "Sign-in failed"
        raise HTTPException(401, message)
    body = resp.json()
    email = ((body.get("user") or {}).get("email") or "").strip().lower()
    if email not in get_settings().admin_email_list:
        raise HTTPException(403, f"{email} is not an admin")
    return {"access_token": body["access_token"], "refresh_token": body.get("refresh_token"),
            "expires_in": body.get("expires_in"), "email": email}


def login(email: str, password: str) -> dict:
    _check_setup()
    return _session(_supabase("POST", "/token", params={"grant_type": "password"},
                              json={"email": email, "password": password}))


def refresh(refresh_token: str) -> dict:
    _check_setup()
    return _session(_supabase("POST", "/token", params={"grant_type": "refresh_token"},
                              json={"refresh_token": refresh_token}))


def logout(token: str) -> None:
    _cache.pop(token, None)
    try:
        _supabase("POST", "/logout", token)
    except HTTPException:
        pass  # the token expires on its own


def require_admin(authorization: Optional[str] = Header(None)) -> str:
    settings = _check_setup()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in first")
    email = _user_email(authorization[7:].strip())
    if not email:
        raise HTTPException(401, "Your session has expired; sign in again")
    if email not in settings.admin_email_list:
        raise HTTPException(403, f"{email} is not an admin")
    return email
