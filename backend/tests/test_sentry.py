"""Sentry connector: status, read tools, routing, and honest errors."""

import httpx
import pytest
from fastapi.testclient import TestClient

# Captured before monkeypatching: the factory must build REAL clients.
_RealAsyncClient = httpx.AsyncClient

from app.ai.router import route_question
from app.ai.tools import ToolError, format_tool_result
from app.connectors import sentry
from app.core.config import settings
from app.main import app

ORG = "acme"
PROJECT = "checkout-api"
BASE = f"/projects/{ORG}/{PROJECT}"


@pytest.fixture(autouse=True)
def clean_sentry(monkeypatch):
    """Every test starts unconfigured, with no cached status."""
    sentry.reset_status_cache()
    monkeypatch.setattr(settings, "sentry_auth_token", "")
    monkeypatch.setattr(settings, "sentry_org", "")
    monkeypatch.setattr(settings, "sentry_project", "")
    monkeypatch.setattr(settings, "sentry_api_url", "https://sentry.test")
    yield
    sentry.reset_status_cache()


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "sentry_auth_token", "sntrys_test")
    monkeypatch.setattr(settings, "sentry_org", ORG)
    monkeypatch.setattr(settings, "sentry_project", PROJECT)


def _patch_http(monkeypatch, handler):
    """Send the connector's requests to a MockTransport instead of Sentry."""

    def factory(**kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(sentry.httpx, "AsyncClient", factory)


def _issue(**overrides):
    issue = {
        "id": "1234567890",
        "shortId": "CHECKOUT-1A",
        "title": "ZeroDivisionError: division by zero",
        "culprit": "checkout.views.create_order",
        "level": "error",
        "status": "unresolved",
        "count": "42",
        "userCount": "7",
        "firstSeen": "2026-02-01T10:00:00Z",
        "lastSeen": "2026-02-02T10:00:00Z",
        "permalink": "https://sentry.io/organizations/acme/issues/1234567890/",
        "metadata": {"type": "ZeroDivisionError", "value": "division by zero"},
    }
    issue.update(overrides)
    return issue


def _event():
    return {
        "eventID": "event-abc",
        "dateCreated": "2026-02-02T10:00:00Z",
        "title": "ZeroDivisionError: division by zero",
        "release": "checkout@1.4.2",
        "tags": [
            {"key": "environment", "value": "production"},
            {"key": "url", "value": "https://shop.test/api/orders"},
            {"key": "method", "value": "POST"},
            {"key": "status_code", "value": "500"},
        ],
        "entries": [
            {
                "type": "exception",
                "data": {
                    "values": [
                        {
                            "type": "ZeroDivisionError",
                            "value": "division by zero",
                            "stacktrace": {
                                "frames": [
                                    {
                                        "filename": "checkout/app.py",
                                        "function": "handle",
                                        "lineNo": 12,
                                        "inApp": True,
                                    },
                                    {
                                        "filename": "checkout/views.py",
                                        "function": "create_order",
                                        "lineNo": 88,
                                        "inApp": True,
                                    },
                                ]
                            },
                        }
                    ]
                },
            },
            {
                "type": "request",
                "data": {
                    "url": "https://shop.test/api/orders",
                    "method": "POST",
                    "headers": [
                        ["Authorization", "Bearer secret-token"],
                        ["Cookie", "session=abc"],
                        ["Content-Type", "application/json"],
                        ["X-Api-Key", "also-secret"],
                        ["User-Agent", "curl/8.0"],
                    ],
                },
            },
        ],
    }


@pytest.fixture
def sentry_http(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "auth": request.headers.get("authorization"),
            }
        )
        path = request.url.path
        if path == f"/organizations/{ORG}/":
            return httpx.Response(200, json={"slug": ORG, "name": "Acme Inc"})
        if path == f"{BASE}/issues/":
            return httpx.Response(200, json=[_issue()])
        if path == "/issues/1234567890/":
            return httpx.Response(200, json=_issue())
        if path == "/issues/1234567890/events/latest/":
            return httpx.Response(200, json=_event())
        if path == f"{BASE}/events/":
            return httpx.Response(
                200,
                json=[
                    {
                        "eventID": "event-abc",
                        "dateCreated": "2026-02-02T10:00:00Z",
                        "title": "ZeroDivisionError: division by zero",
                        "culprit": "checkout.views.create_order",
                        "tags": [
                            {"key": "url", "value": "https://shop.test/api/orders"},
                            {"key": "method", "value": "POST"},
                            {"key": "status_code", "value": "500"},
                            {"key": "environment", "value": "production"},
                            {"key": "release", "value": "checkout@1.4.2"},
                        ],
                    }
                ],
            )
        return httpx.Response(404, json={"detail": "Not found"})

    _patch_http(monkeypatch, handler)
    return calls


# ── Status ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_is_honest_when_not_configured():
    status = await sentry.connection_status()
    assert status["configured"] is False
    assert status["connected"] is False
    assert status["account"] is None
    assert status["error"] is None
    names = {tool["name"] for tool in status["tools"]}
    assert names == {"list_issues", "get_issue_event", "search_events"}
    assert all(not tool["write"] for tool in status["tools"])
    assert all(tool["available"] for tool in status["tools"])


@pytest.mark.asyncio
async def test_status_reports_the_real_org(monkeypatch, sentry_http):
    _configure(monkeypatch)
    status = await sentry.connection_status()

    assert status["connected"] is True
    assert status["account"] == ORG
    assert status["org"] == ORG
    assert status["project"] == PROJECT
    assert status["error"] is None
    assert sentry_http[0]["path"] == f"/organizations/{ORG}/"
    assert sentry_http[0]["auth"] == "Bearer sntrys_test"


@pytest.mark.asyncio
async def test_status_success_is_cached(monkeypatch, sentry_http):
    _configure(monkeypatch)
    await sentry.connection_status()
    await sentry.connection_status()
    assert len(sentry_http) == 1


@pytest.mark.asyncio
async def test_bad_token_is_explained_not_hidden(monkeypatch):
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Invalid token"})

    _patch_http(monkeypatch, handler)
    status = await sentry.connection_status()

    assert status["configured"] is True
    assert status["connected"] is False
    assert "SENTRY_AUTH_TOKEN" in status["error"]


@pytest.mark.asyncio
async def test_missing_scopes_are_explained(monkeypatch):
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "You do not have permission"})

    _patch_http(monkeypatch, handler)
    status = await sentry.connection_status()

    assert status["connected"] is False
    assert "org:read" in status["error"]
    assert "event:read" in status["error"]


# ── Read tools ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_issues_passes_params_and_summarizes(monkeypatch, sentry_http):
    _configure(monkeypatch)
    result = await sentry.list_issues("is:unresolved", "7d", 5)

    assert result["org"] == ORG
    assert result["project"] == PROJECT
    assert result["count"] == 1
    issue = result["issues"][0]
    assert issue["id"] == "1234567890"
    assert issue["short_id"] == "CHECKOUT-1A"
    # Sentry sends counts as strings; the connector keeps them numeric.
    assert issue["count"] == 42
    assert issue["users"] == 7

    call = sentry_http[0]
    assert call["path"] == f"{BASE}/issues/"
    assert call["params"]["query"] == "is:unresolved"
    assert call["params"]["statsPeriod"] == "7d"
    assert call["params"]["limit"] == "5"


@pytest.mark.asyncio
async def test_list_issues_defaults_to_unresolved(monkeypatch, sentry_http):
    _configure(monkeypatch)
    result = await sentry.list_issues("", "", 10)
    assert result["query"] == "is:unresolved"
    assert result["period"] == "14d"


@pytest.mark.asyncio
async def test_get_issue_event_extracts_crash_site_and_redacts_headers(
    monkeypatch, sentry_http
):
    _configure(monkeypatch)
    result = await sentry.get_issue_event("1234567890")

    assert result["issue"]["id"] == "1234567890"
    event = result["event"]
    assert event["exception"]["type"] == "ZeroDivisionError"
    assert event["environment"] == "production"
    assert event["release"] == "checkout@1.4.2"
    # Sentry sends frames oldest → newest; the crash site must come first.
    assert event["frames"][0]["filename"] == "checkout/views.py"
    assert event["frames"][0]["line"] == 88
    assert event["request"]["url"] == "https://shop.test/api/orders"
    assert event["request"]["status_code"] == "500"
    # Credentials never reach the model.
    headers = event["request"]["headers"]
    assert "Authorization" not in headers
    assert "Cookie" not in headers
    assert "X-Api-Key" not in headers
    assert headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_get_issue_event_rejects_a_non_numeric_id(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(sentry.SentryError, match="issue id"):
        await sentry.get_issue_event("../../etc/passwd")


@pytest.mark.asyncio
async def test_issue_without_events_says_so(monkeypatch):
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/issues/1234567890/":
            return httpx.Response(200, json=_issue())
        return httpx.Response(404, json={"detail": "Not found"})

    _patch_http(monkeypatch, handler)
    with pytest.raises(sentry.SentryError, match="no events"):
        await sentry.get_issue_event("1234567890")


@pytest.mark.asyncio
async def test_unknown_issue_is_explained(monkeypatch):
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not found"})

    _patch_http(monkeypatch, handler)
    with pytest.raises(sentry.SentryError, match="SENTRY_ORG"):
        await sentry.get_issue_event("1234567890")


@pytest.mark.asyncio
async def test_search_events_builds_a_url_query(monkeypatch, sentry_http):
    _configure(monkeypatch)
    result = await sentry.search_events("/api/orders", "", "7d", 5)

    assert result["endpoint"] == "/api/orders"
    assert result["count"] == 1
    event = result["events"][0]
    assert event["event_id"] == "event-abc"
    assert event["url"] == "https://shop.test/api/orders"
    assert event["status_code"] == "500"
    assert event["environment"] == "production"

    call = sentry_http[0]
    assert call["path"] == f"{BASE}/events/"
    assert call["params"]["query"] == 'url:"*/api/orders*"'
    assert call["params"]["statsPeriod"] == "7d"


@pytest.mark.asyncio
async def test_search_events_accepts_an_explicit_query(monkeypatch, sentry_http):
    _configure(monkeypatch)
    await sentry.search_events("", "is:unresolved level:error", "14d", 5)
    assert sentry_http[0]["params"]["query"] == "is:unresolved level:error"


# ── Router ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_named_sentry_question_lists_issues(monkeypatch):
    _configure(monkeypatch)
    decision = await route_question("show me sentry issues")
    assert decision.route == "tool:sentry_issues"
    assert decision.via == "rule"
    assert decision.args["query"] == "is:unresolved"
    assert decision.args["period"] == "14d"


@pytest.mark.asyncio
async def test_sentry_period_words_are_honored(monkeypatch):
    _configure(monkeypatch)
    decision = await route_question("show sentry errors from today")
    assert decision.route == "tool:sentry_issues"
    assert decision.args["period"] == "24h"


@pytest.mark.asyncio
async def test_sentry_issue_id_routes_to_the_event(monkeypatch):
    _configure(monkeypatch)
    decision = await route_question("show me sentry issue 1234567890")
    assert decision.route == "tool:sentry_event"
    assert decision.args == {"issue_id": "1234567890"}


@pytest.mark.asyncio
async def test_sentry_endpoint_routes_to_event_search(monkeypatch):
    _configure(monkeypatch)
    decision = await route_question("show sentry errors for /api/orders")
    assert decision.route == "tool:sentry_events"
    assert decision.args["endpoint"] == "/api/orders"


@pytest.mark.asyncio
async def test_production_failure_with_endpoint_routes_without_the_word_sentry(
    monkeypatch,
):
    _configure(monkeypatch)
    decision = await route_question("why did /api/orders fail in production?")
    assert decision.route == "tool:sentry_events"
    assert decision.args["endpoint"] == "/api/orders"


@pytest.mark.asyncio
async def test_recent_production_errors_route_to_issues(monkeypatch):
    _configure(monkeypatch)
    decision = await route_question("any recent errors in production?")
    assert decision.route == "tool:sentry_issues"


@pytest.mark.asyncio
async def test_sentry_definition_stays_in_rag(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    decision = await route_question("what is Sentry?")
    assert decision.route == "rag"


@pytest.mark.asyncio
async def test_stack_trace_definition_stays_in_rag(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    decision = await route_question("what is a stack trace?")
    assert decision.route == "rag"


@pytest.mark.asyncio
async def test_sentry_questions_are_not_routed_when_unconfigured(monkeypatch):
    # No token: only an explicit "sentry" question is routed, so it can
    # produce the actionable setup error. A vague prod question stays RAG.
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    decision = await route_question("why did /api/orders fail in production?")
    assert decision.route != "tool:sentry_events"


# ── Executors, labels, and formatting ────────────────────────────────────────

@pytest.mark.asyncio
async def test_connector_errors_become_clean_tool_errors(monkeypatch):
    import app.routes.ask as ask_module

    async def rejected(*args, **kwargs):
        raise sentry.SentryError("Sentry rejected the token (401).")

    monkeypatch.setattr(sentry, "list_issues", rejected)
    with pytest.raises(ToolError, match="401"):
        await ask_module._execute_tool("sentry_issues", {})


def test_sentry_labels_are_short_and_real():
    import app.routes.ask as ask_module

    assert (
        ask_module._tool_label(
            "sentry_issues", {"count": 3, "project": PROJECT}
        )
        == "Sentry · 3 issues · checkout-api"
    )
    assert (
        ask_module._tool_label(
            "sentry_event",
            {
                "issue": {"short_id": "CHECKOUT-1A", "id": "123"},
                "event": {"exception": {"type": "ZeroDivisionError"}},
            },
        )
        == "Sentry · CHECKOUT-1A · ZeroDivisionError"
    )
    assert (
        ask_module._tool_label(
            "sentry_events", {"count": 2, "endpoint": "/api/orders"}
        )
        == "Sentry · 2 events · /api/orders"
    )
    assert (
        ask_module._tool_preview_label(
            "sentry_events", {"endpoint": "/api/orders"}
        )
        == "sentry · /api/orders"
    )


def test_sentry_results_read_like_a_sentence():
    issues = format_tool_result(
        "sentry_issues",
        {
            "org": ORG,
            "project": PROJECT,
            "period": "14d",
            "issues": [
                {
                    "id": "1234567890",
                    "short_id": "CHECKOUT-1A",
                    "title": "ZeroDivisionError",
                    "count": 42,
                }
            ],
        },
    )
    assert "1 unresolved issue" in issues
    assert "CHECKOUT-1A" in issues
    assert "acme/checkout-api" in issues

    event = format_tool_result(
        "sentry_event",
        {
            "issue": {"short_id": "CHECKOUT-1A", "id": "123"},
            "event": {
                "exception": {
                    "type": "ZeroDivisionError",
                    "value": "division by zero",
                },
                "frames": [
                    {"filename": "checkout/views.py", "line": 88, "function": "create_order"}
                ],
                "request": {
                    "url": "https://shop.test/api/orders",
                    "method": "POST",
                    "status_code": "500",
                },
            },
        },
    )
    assert "ZeroDivisionError" in event
    assert "checkout/views.py:88" in event
    assert "POST https://shop.test/api/orders" in event
    assert "500" in event

    events = format_tool_result(
        "sentry_events",
        {
            "org": ORG,
            "project": PROJECT,
            "endpoint": "/api/orders",
            "events": [
                {"timestamp": "2026-02-02T10:00:00Z", "title": "ZeroDivisionError"}
            ],
        },
    )
    assert "1 event for /api/orders" in events
    assert "ZeroDivisionError" in events


def test_connections_endpoint_includes_sentry():
    client = TestClient(app)
    response = client.get("/api/connections")
    assert response.status_code == 200

    payload = response.json()["sentry"]
    assert payload["configured"] is False
    assert payload["connected"] is False
    assert payload["source"] == "auth_token"
    assert payload["tools"]
