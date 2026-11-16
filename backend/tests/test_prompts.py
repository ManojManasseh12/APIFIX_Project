"""Prompt templates: overrides, routing, and the /api/prompts endpoints.

The suite runs with DATABASE_URL empty, so the SQLite fallback store is
exercised — the same logic Supabase Postgres serves in production.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db import prompt_templates
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "runs_db", tmp_path / "runs.sqlite3")
    prompt_templates._pg_ready = False
    yield
    prompt_templates._pg_ready = False


def test_no_override_returns_none():
    assert prompt_templates.get_override("diagnosis") is None


def test_save_and_read_override_roundtrip():
    prompt_templates.save_override("diagnosis", "Custom diagnosis rules. " * 3)
    override = prompt_templates.get_override("diagnosis")
    assert override is not None
    assert override["name"] == "custom"
    assert override["template"].startswith("Custom diagnosis rules.")

    # Other purposes are untouched.
    assert prompt_templates.get_override("ask") is None


def test_delete_override_falls_back_to_default():
    prompt_templates.save_override("ask", "Custom ask rules for testing." * 2)
    assert prompt_templates.get_override("ask") is not None

    prompt_templates.delete_override("ask")
    assert prompt_templates.get_override("ask") is None


def test_invalid_purpose_is_rejected():
    with pytest.raises(ValueError):
        prompt_templates.save_override("nope", "whatever text long enough")
    assert prompt_templates.get_override("nope") is None
    assert prompt_templates.validate_text("short") is not None
    assert prompt_templates.validate_text("x" * 20000) is not None
    assert prompt_templates.validate_text("a perfectly reasonable template " * 2) is None


def test_prompts_endpoint_lists_three_slots():
    response = client.get("/api/prompts")
    assert response.status_code == 200
    prompts = response.json()["prompts"]
    assert [p["purpose"] for p in prompts] == ["diagnosis", "ask", "tool"]
    assert all(p["is_custom"] is False for p in prompts)


def test_save_and_reset_via_endpoint():
    saved = client.put(
        "/api/prompts/ask", json={"text": "Endpoint-saved ask template for tests." * 3}
    )
    assert saved.status_code == 200
    assert saved.json()["is_custom"] is True

    listed = client.get("/api/prompts").json()["prompts"]
    ask_slot = next(p for p in listed if p["purpose"] == "ask")
    assert ask_slot["is_custom"] is True
    assert ask_slot["text"].startswith("Endpoint-saved")

    # The resolve path the routes use picks the override up.
    override = prompt_templates.get_override("ask")
    assert override is not None

    reset = client.delete("/api/prompts/ask")
    assert reset.status_code == 200
    assert reset.json()["is_custom"] is False
    listed = client.get("/api/prompts").json()["prompts"]
    assert all(p["is_custom"] is False for p in listed)


def test_endpoint_rejects_unknown_purpose_and_bad_text():
    assert client.put("/api/prompts/bogus", json={"text": "long enough text here"}).status_code == 404
    assert client.put("/api/prompts/ask", json={"text": "tiny"}).status_code == 422
