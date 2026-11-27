"""LangSmith observability: sanitization, disabled-by-default behaviour, and
the health flag.

These tests never contact LangSmith. They pin the safety rules that make
tracing safe to ship: no key means no client, no network, and the stream is
passed through untouched.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.observability import (
    current_trace_id,
    flush_traces,
    get_client,
    hash_user_id,
    langsmith_enabled,
    reset_client,
    sanitize_for_trace,
    sanitize_url,
    shutdown_tracing,
    traced,
    traced_stream,
)
from app.observability import langsmith as tracing


@pytest.fixture(autouse=True)
def tracing_off(monkeypatch):
    """Every test starts with tracing disabled, even if the dev .env has it on."""
    monkeypatch.setattr(settings, "langsmith_tracing", False)
    monkeypatch.setattr(settings, "langsmith_api_key", "")
    reset_client()
    yield
    reset_client()


# ------------------------------------------------------------------ sanitizing


def test_sanitize_redacts_headers_and_secret_keys():
    payload = {
        "headers": {
            "Authorization": "Bearer abcdefghijklmnop",
            "Cookie": "session=abc",
            "Accept": "application/json",
        },
        "token": "should-not-leak",
        "api_key": "sk-live-should-not-leak",
        "nested": {"password": "hunter2", "safe": "visible"},
    }

    clean = sanitize_for_trace(payload)

    assert clean["headers"]["Authorization"] == "<REDACTED>"
    assert clean["headers"]["Cookie"] == "<REDACTED>"
    assert clean["headers"]["Accept"] == "application/json"
    assert clean["token"] == "<REDACTED>"
    assert clean["api_key"] == "<REDACTED>"
    assert clean["nested"]["password"] == "<REDACTED>"
    assert clean["nested"]["safe"] == "visible"


def test_sanitize_redacts_secret_shapes_inside_strings():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl"
    github_token = "ghp_" + "a" * 36
    text = f"retry with Bearer abcdefghijkl and {jwt} and {github_token}"

    clean = sanitize_for_trace(text)

    assert jwt not in clean
    assert github_token not in clean
    assert "Bearer abcdefghijkl" not in clean
    assert "<REDACTED>" in clean


def test_sanitize_drops_gmail_snippets_and_calendar_attendees():
    payload = {
        "messages": [
            {
                "subject": "Invoice 42",
                "snippet": "your card ending 4242 was charged",
                "attendees": ["a@example.com", "b@example.com"],
            }
        ]
    }

    clean = sanitize_for_trace(payload)
    message = clean["messages"][0]

    assert message["subject"] == "Invoice 42"
    assert message["snippet"] == "<REDACTED>"
    assert message["attendees"] == "<REDACTED>"


def test_sanitize_truncates_long_strings_and_caps_lists():
    clean = sanitize_for_trace({"body": "x" * 5_000, "items": list(range(500))})

    assert clean["body"].endswith("…<truncated>")
    assert len(clean["body"]) < 5_000
    assert len(clean["items"]) == 50


def test_sanitize_handles_pydantic_and_plain_objects():
    from app.ai.router import RouteDecision
    from app.core.models import Observation

    observation = Observation(
        request_id="req-1",
        status=401,
        latency_ms=12,
        headers={"Authorization": "Bearer abcdefghijkl"},
        error_type="auth",
    )
    clean = sanitize_for_trace(observation)

    assert clean["status"] == 401
    assert clean["headers"]["Authorization"] == "<REDACTED>"

    plain = sanitize_for_trace(
        RouteDecision(route="rag", via="rule", args={"city": "Tokyo"})
    )
    assert plain == {"route": "rag", "via": "rule", "args": {"city": "Tokyo"}}


def test_sanitize_url_redacts_query_secrets():
    url = "https://api.example.com/v1/orders?token=abc123&api_key=sk-live&page=2"

    clean = sanitize_url(url)

    assert "abc123" not in clean
    assert "sk-live" not in clean
    assert "page=2" in clean
    assert "<REDACTED>" in clean


def test_hash_user_id_is_stable_and_salted(monkeypatch):
    monkeypatch.setattr(settings, "langsmith_user_salt", "salt-a")
    first = hash_user_id("user-123")
    assert first == hash_user_id("user-123")
    assert first != "user-123"
    assert len(first) == 16

    monkeypatch.setattr(settings, "langsmith_user_salt", "salt-b")
    assert hash_user_id("user-123") != first
    assert hash_user_id(None) is None
    assert hash_user_id("") is None


# ------------------------------------------------------- disabled means no-op


def test_tracing_is_off_by_default():
    assert langsmith_enabled() is False
    assert get_client() is None
    assert current_trace_id() is None
    # Must not raise or attempt a network call.
    flush_traces()
    shutdown_tracing()


def test_traced_decorator_returns_the_original_function_when_off():
    async def handler(value: int) -> int:
        return value * 2

    wrapped = traced("test.span")(handler)

    assert wrapped is handler
    assert asyncio.run(wrapped(3)) == 6


def test_traced_stream_passes_every_item_through_when_off():
    async def source():
        for token in ["a", "b", "c"]:
            yield token

    async def collect():
        return [item async for item in traced_stream("ask", source())]

    assert asyncio.run(collect()) == ["a", "b", "c"]


def test_traced_stream_propagates_errors_when_off():
    async def source():
        yield "first"
        raise RuntimeError("boom")

    async def collect():
        items = []
        with pytest.raises(RuntimeError):
            async for item in traced_stream("ask", source()):
                items.append(item)
        return items

    assert asyncio.run(collect()) == ["first"]


def test_enabled_without_a_working_client_still_streams(monkeypatch):
    """A tracing failure must never break the user's request."""
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test_key")

    def explode():
        raise RuntimeError("LangSmith is down")

    monkeypatch.setattr(tracing, "get_client", explode)

    async def source():
        yield "hello"

    async def collect():
        return [item async for item in traced_stream("ask", source())]

    assert langsmith_enabled() is True
    assert asyncio.run(collect()) == ["hello"]


def test_client_hides_inputs_and_outputs_for_graph_runs(monkeypatch):
    """LangGraph sends raw state to LangSmith; the client must sanitize it."""
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test_key")

    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import langsmith

    monkeypatch.setattr(langsmith, "Client", FakeClient)
    reset_client()

    client = get_client()

    assert isinstance(client, FakeClient)
    assert captured["hide_inputs"] is sanitize_for_trace
    assert captured["hide_outputs"] is sanitize_for_trace

    hidden = captured["hide_inputs"](
        {"headers": {"Authorization": "Bearer top-secret"}}
    )
    assert hidden["headers"]["Authorization"] == "<REDACTED>"
    assert "top-secret" not in str(hidden)


def test_enabled_decorator_wraps_but_still_calls_through(monkeypatch):
    """The wrapper must preserve behaviour; tracing is a side channel."""
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test_key")
    monkeypatch.setattr(tracing, "get_client", lambda: None)

    async def handler(value: int) -> int:
        return value + 1

    wrapped = traced("test.span")(handler)

    assert asyncio.run(wrapped(1)) == 2


def test_zero_sample_rate_skips_the_stream_without_a_client(monkeypatch):
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test_key")
    monkeypatch.setattr(settings, "langsmith_sample_rate", 0.0)

    def explode():
        raise AssertionError("sampled-out requests must not create a client")

    monkeypatch.setattr(tracing, "get_client", explode)

    async def source():
        yield "still here"

    async def collect():
        return [item async for item in traced_stream("ask", source())]

    assert asyncio.run(collect()) == ["still here"]


# ------------------------------------------------------------------- health


def test_health_reports_langsmith_disabled():
    client = TestClient(app)
    body = client.get("/api/health").json()

    assert body["ok"] is True
    assert body["langsmith"] == {"enabled": False, "project": None}


def test_health_reports_the_project_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test_key")
    monkeypatch.setattr(settings, "langsmith_project", "apifix-test")

    client = TestClient(app)
    body = client.get("/api/health").json()

    assert body["langsmith"] == {"enabled": True, "project": "apifix-test"}
