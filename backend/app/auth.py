"""Admin login: the web app signs in with Supabase Auth and sends the access
token; we ask Supabase who it belongs to and check the email against
ADMIN_EMAILS. No password or key is stored here."""
import time
from typing import Dict, Optional, Tuple

import httpx
from fastapi import Header, HTTPException

from app.config import get_settings

_CACHE_SECONDS = 60
_cache: Dict[str, Tuple[str, float]] = {}


def _user_email(token: str) -> Optional[str]:
    settings = get_settings()
    hit = _cache.get(token)
    if hit and hit[1] > time.monotonic():
        return hit[0]
    resp = httpx.get(f"{settings.supabase_url.rstrip('/')}/auth/v1/user", timeout=10,
                     headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        return None
    email = (resp.json().get("email") or "").strip().lower()
    if len(_cache) > 100:
        _cache.clear()
    _cache[token] = (email, time.monotonic() + _CACHE_SECONDS)
    return email


def require_admin(authorization: Optional[str] = Header(None)) -> str:
    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_anon_key and settings.admin_email_list):
        raise HTTPException(503, "Admin login is not set up: SUPABASE_URL, SUPABASE_ANON_KEY and ADMIN_EMAILS")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in first")
    email = _user_email(authorization[7:].strip())
    if not email:
        raise HTTPException(401, "Your session has expired; sign in again")
    if email not in settings.admin_email_list:
        raise HTTPException(403, f"{email} is not an admin")
    return email
