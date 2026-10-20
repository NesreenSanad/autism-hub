"""Admin sign-in through the backend, with Supabase Auth mocked."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import get_settings


@pytest.fixture
def client(monkeypatch):
    for key, value in {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_ANON_KEY": "anon",
                       "ADMIN_EMAILS": "Admin@Example.org"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    calls = []

    def fake_supabase(method, path, token=None, **kwargs):
        calls.append((method, path, kwargs.get("params"), kwargs.get("json")))
        body = kwargs.get("json") or {}
        if body.get("password") == "right" or body.get("refresh_token") == "r1":
            email = body.get("email", "admin@example.org")
            return httpx.Response(200, json={"access_token": "t1", "refresh_token": "r2", "expires_in": 3600,
                                             "user": {"email": email}})
        if path == "/logout":
            return httpx.Response(204)
        return httpx.Response(400, json={"error_description": "Invalid login credentials"})

    monkeypatch.setattr(auth, "_supabase", fake_supabase)
    from app.main import app
    yield TestClient(app), calls
    get_settings.cache_clear()


def test_login_returns_a_token_for_admins(client):
    http, calls = client
    resp = http.post("/auth/login", json={"email": "admin@example.org", "password": "right"})
    assert resp.status_code == 200
    assert resp.json() == {"access_token": "t1", "refresh_token": "r2", "expires_in": 3600,
                           "email": "admin@example.org"}
    assert calls[0][:3] == ("POST", "/token", {"grant_type": "password"})


def test_login_refuses_wrong_password_and_non_admins(client):
    http, _ = client
    wrong = http.post("/auth/login", json={"email": "admin@example.org", "password": "wrong"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "Invalid login credentials"
    other = http.post("/auth/login", json={"email": "someone@example.org", "password": "right"})
    assert other.status_code == 403


def test_refresh_and_logout(client):
    http, calls = client
    assert http.post("/auth/refresh", json={"refresh_token": "r1"}).json()["access_token"] == "t1"
    assert http.post("/auth/refresh", json={"refresh_token": "old"}).status_code == 401
    assert http.post("/auth/logout", headers={"Authorization": "Bearer t1"}).json() == {"ok": True}
    assert calls[-1][1] == "/logout"


def test_login_not_set_up(client, monkeypatch):
    http, _ = client
    monkeypatch.setenv("ADMIN_EMAILS", "")
    get_settings.cache_clear()
    assert http.post("/auth/login", json={"email": "a@b.c", "password": "right"}).status_code == 503
