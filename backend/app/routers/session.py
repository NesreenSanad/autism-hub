"""Sign-in for the admin page (see app/auth.py)."""
from typing import Optional

from fastapi import APIRouter, Header
from pydantic import BaseModel

from app import auth

router = APIRouter(prefix="/auth", tags=["auth"])


class Login(BaseModel):
    email: str
    password: str


class Refresh(BaseModel):
    refresh_token: str


@router.post("/login")
def login(body: Login) -> dict:
    return auth.login(body.email, body.password)


@router.post("/refresh")
def refresh(body: Refresh) -> dict:
    return auth.refresh(body.refresh_token)


@router.post("/logout")
def logout(authorization: Optional[str] = Header(None)) -> dict:
    if authorization and authorization.lower().startswith("bearer "):
        auth.logout(authorization[7:].strip())
    return {"ok": True}
