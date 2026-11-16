"""Supabase Auth: token verification dependency + user_id scoping.

Covers the three behaviours that matter in review:

* auth off (env unset) → every route stays open, nothing breaks;
* auth on → missing/garbage/expired tokens get 401, valid ones pass;
* data saved with a verified user_id is only listed back for that user
  (plus legacy NULL rows, treated as the project owner's own history).
"""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.core.auth import _decode_token, _get_jwk_client
from app.core.config import settings
from app.db import ask_chats, runs
from app.main import app

AUTH_URL = "https://auth-project.supabase.co"
ISSUER = f"{AUTH_URL}/auth/v1"

client = TestClient(app)


def _enable_auth(monkeypatch) -> ec.EllipticCurvePrivateKey:
    """Flip auth on and point the JWKS client at a locally-signed key."""
    monkeypatch.setattr(settings, "supabase_auth_url", AUTH_URL)
    monkeypatch.setattr(settings, "supabase_auth_jwks_url", f"{AUTH_URL}/auth/v1/.well-known/jwks.json")

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    fake_signing_key = SimpleNamespace(key=public_key, algorithm_name="ES256")
    fake_client = SimpleNamespace(get_signing_key_from_jwt=lambda token: fake_signing_key)
    monkeypatch.setattr("app.core.auth._get_jwk_client", lambda: fake_client)
    return private_key


def _make_token(private_key, sub="user-111", email="dev@example.com", **overrides):
    claims = {
        "sub": sub,
        "email": email,
        "role": "authenticated",
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    if claims["exp"] is None:
        claims.pop("exp")
    if claims["sub"] is None:
        claims.pop("sub")
    return jwt.encode(claims, _private_pem(private_key), algorithm="ES256")


def _private_pem(private_key) -> bytes:
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_auth_disabled_keeps_routes_open(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "supabase_auth_url", "")
    monkeypatch.setattr(settings, "supabase_auth_jwks_url", "")
    assert settings.auth_enabled is False

    response = client.get("/api/runs")
    assert response.status_code == 200


def test_auth_enabled_rejects_missing_and_malformed_headers(monkeypatch, tmp_path):
    _enable_auth(monkeypatch)

    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/runs", headers={"Authorization": "Token abc"}).status_code == 401
    assert client.get("/api/runs", headers={"Authorization": "Bearer"}).status_code == 401
    assert client.get("/api/runs", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401

    # /api/health stays public even with auth on.
    assert client.get("/api/health").status_code == 200


def test_valid_token_is_accepted_and_scopes_data(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "runs_db", tmp_path / "runs.sqlite3")
    private_key = _enable_auth(monkeypatch)
    runs.initialize()

    runs.save(
        {
            "run_id": "run-a",
            "user_id": "user-111",
            "method": "GET",
            "url": "https://api.example.com/users",
            "status": 404,
            "error_type": "http_404",
            "retrieved_ids": [],
            "provider": "groq",
            "diagnosis": "Not found",
            "patch": None,
        }
    )
    token = _make_token(private_key)
    response = client.get("/api/runs", headers=_auth_header(token))
    assert response.status_code == 200
    assert [row["run_id"] for row in response.json()["runs"]] == ["run-a"]


def test_another_users_token_cannot_see_private_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "runs_db", tmp_path / "runs.sqlite3")
    private_key = _enable_auth(monkeypatch)
    runs.initialize()

    runs.save(
        {
            "run_id": "mine",
            "user_id": "user-111",
            "method": "GET",
            "url": "https://api.example.com/private",
            "status": 500,
            "error_type": "http_500",
            "retrieved_ids": [],
            "provider": None,
            "diagnosis": "x",
            "patch": None,
        }
    )
    other_token = _make_token(private_key, sub="user-222")
    response = client.get("/api/runs", headers=_auth_header(other_token))
    assert response.status_code == 200
    assert response.json()["runs"] == []


def test_legacy_null_rows_stay_visible_to_the_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "runs_db", tmp_path / "runs.sqlite3")
    private_key = _enable_auth(monkeypatch)
    runs.initialize()

    runs.save(
        {
            "run_id": "pre-auth",
            "method": "POST",
            "url": "https://api.example.com/orders",
            "status": 400,
            "error_type": "http_400",
            "retrieved_ids": [],
            "provider": None,
            "diagnosis": "x",
            "patch": None,
        }
    )
    token = _make_token(private_key)
    response = client.get("/api/runs", headers=_auth_header(token))
    assert [row["run_id"] for row in response.json()["runs"]] == ["pre-auth"]


def test_expired_token_is_rejected(monkeypatch, tmp_path):
    _enable_auth(monkeypatch)
    private_key = _enable_auth(monkeypatch)
    expired = _make_token(private_key, exp=int(time.time()) - 60)

    assert client.get("/api/runs", headers=_auth_header(expired)).status_code == 401


def test_wrong_issuer_is_rejected(monkeypatch, tmp_path):
    private_key = _enable_auth(monkeypatch)
    forged = _make_token(private_key, iss="https://evil.example.com/auth/v1")

    assert client.get("/api/runs", headers=_auth_header(forged)).status_code == 401


def test_decode_requires_sub_and_exp(monkeypatch):
    _enable_auth(monkeypatch)
    private_key = _enable_auth(monkeypatch)

    no_sub = _make_token(private_key, sub=None)
    with pytest.raises(jwt.InvalidTokenError):
        _decode_token(no_sub)

    no_exp = _make_token(private_key, exp=None)
    with pytest.raises(jwt.InvalidTokenError):
        _decode_token(no_exp)


def test_ask_turns_are_saved_and_listed_per_user(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "runs_db", tmp_path / "runs.sqlite3")
    ask_chats.initialize()

    ask_chats.save(
        {
            "turn_id": "t1",
            "user_id": "user-111",
            "session_id": "s1",
            "question": "q",
            "answer": "a",
            "provider": None,
            "tool": "decode",
            "tool_label": "jwt decoded",
            "citations": [],
        }
    )
    assert [t["turn_id"] for t in ask_chats.list_turns(user_id="user-111")] == ["t1"]
    assert ask_chats.list_turns(user_id="user-999") == []
    # No filter (auth off) still sees everything.
    assert [t["turn_id"] for t in ask_chats.list_turns()] == ["t1"]


def test_real_jwk_client_is_built_once(monkeypatch):
    monkeypatch.setattr(settings, "supabase_auth_jwks_url", f"{AUTH_URL}/jwks.json")
    import app.core.auth as auth_module

    auth_module._jwk_client = None
    first = auth_module._get_jwk_client()
    second = auth_module._get_jwk_client()
    assert first is second
    assert first.uri == f"{AUTH_URL}/jwks.json"
    auth_module._jwk_client = None
