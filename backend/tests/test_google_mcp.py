"""Google Workspace MCP: OAuth, token storage, routing, and honest errors."""

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.ai.router import route_question
from app.ai.tools import ToolError, format_tool_result
from app.core.config import settings
from app.main import app
from app.mcp import client as mcp_client
from app.mcp import crypto, google, store


@pytest.fixture(autouse=True)
def clean_google(monkeypatch, tmp_path):
    """Every test starts unconfigured, with its own SQLite file."""
    monkeypatch.setattr(settings, "google_client_id", "")
    monkeypatch.setattr(settings, "google_client_secret", "")
    monkeypatch.setattr(settings, "mcp_token_encryption_key", "")
    monkeypatch.setattr(settings, "runs_db", tmp_path / "runs.sqlite3")
    # REST is the production default; MCP tests opt in explicitly.
    monkeypatch.setattr(settings, "google_connector_mode", "rest")
    monkeypatch.setattr(
        settings,
        "google_redirect_uri",
        "http://localhost:8000/api/connections/google/callback",
    )
    yield


def configure(monkeypatch, key: str | None = None):
    """A valid client id/secret plus a fresh Fernet key."""
    monkeypatch.setattr(settings, "google_client_id", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "GOCSPX-test")
    monkeypatch.setattr(
        settings,
        "mcp_token_encryption_key",
        key or Fernet.generate_key().decode(),
    )


# ── Configuration + OAuth ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_is_honest_when_not_configured():
    status = await google.status("user-1")
    assert status["configured"] is False
    assert status["connected"] is False
    assert status["account"] is None
    assert "GOOGLE_CLIENT_ID" in status["error"]
    names = {tool["name"] for tool in status["tools"]}
    assert {"list_events", "search_threads"} <= names
    write = {tool["name"] for tool in status["tools"] if tool["write"]}
    assert "create_event" in write
    assert all(not tool["available"] for tool in status["tools"] if tool["write"])


def test_invalid_encryption_key_is_explained(monkeypatch):
    monkeypatch.setattr(settings, "mcp_token_encryption_key", "too-short")
    assert crypto.is_valid_key() is False
    with pytest.raises(crypto.CryptoNotConfigured, match="Fernet"):
        crypto.encrypt("secret")


def test_authorization_url_requests_offline_access(monkeypatch):
    configure(monkeypatch)
    url = google.authorization_url("user-1", "http://localhost:5173")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "calendar.events.readonly" in url
    assert "gmail.readonly" in url
    assert "state=" in url


def test_state_round_trip_and_tampering():
    state = google.sign_state("user-1", "http://localhost:5173")
    payload = google.verify_state(state)
    assert payload["u"] == "user-1"
    assert payload["o"] == "http://localhost:5173"

    with pytest.raises(google.GoogleError, match="verification"):
        google.verify_state(state[:-1] + ("0" if state[-1] != "0" else "1"))


def test_expired_state_is_rejected(monkeypatch):
    import time as time_module

    state = google.sign_state("user-1", "http://localhost:5173")
    real_time = time_module.time
    monkeypatch.setattr(
        google.time, "time", lambda: real_time() + google.STATE_TTL_S + 5
    )
    with pytest.raises(google.GoogleError, match="expired"):
        google.verify_state(state)


# ── Token storage ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tokens_are_stored_encrypted_and_read_back(monkeypatch):
    configure(monkeypatch)
    await google.save_connection(
        "user-1",
        {
            "access_token": "ya29.access",
            "refresh_token": "1//refresh",
            "expires_in": 3600,
            "scope": " ".join(google.SCOPES),
        },
    )

    record = await asyncio.to_thread(store.get, "user-1", "google")
    assert record["access_token"] == "ya29.access"
    assert record["refresh_token"] == "1//refresh"

    # The database itself must never contain the plaintext token.
    with store._sqlite() as connection:
        raw = connection.execute(
            "select access_token, refresh_token from mcp_connections"
        ).fetchone()
    assert "ya29.access" not in raw[0]
    assert "1//refresh" not in raw[1]


@pytest.mark.asyncio
async def test_account_email_survives_a_token_refresh(monkeypatch):
    """A refresh response has no id_token, so the email must be kept."""
    configure(monkeypatch)
    payload = base64.urlsafe_b64encode(
        json.dumps({"email": "me@example.com"}).encode()
    ).decode().rstrip("=")

    await google.save_connection(
        "user-1",
        {
            "access_token": "first",
            "refresh_token": "1//refresh",
            "expires_in": 3600,
            "id_token": f"header.{payload}.signature",
        },
    )
    # Simulates what _refresh sends: new access token, no id_token.
    await google.save_connection(
        "user-1",
        {
            "access_token": "second",
            "refresh_token": "1//refresh",
            "expires_in": 3600,
        },
    )

    record = await asyncio.to_thread(store.get, "user-1", "google")
    assert record["account_email"] == "me@example.com"
    assert record["access_token"] == "second"


@pytest.mark.asyncio
async def test_expired_access_token_is_refreshed(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(
        store,
        "save",
        lambda record: _store_saved.append(record),
    )
    _store_saved.clear()
    record = {
        "user_id": "user-1",
        "provider": "google",
        "account_email": "me@example.com",
        "scopes": google.SCOPES,
        "access_token": "old",
        "refresh_token": "1//refresh",
        "expires_at": datetime.now(timezone.utc) - timedelta(minutes=5),
    }
    monkeypatch.setattr(store, "get", lambda user_id, provider: record)

    async def fake_token_request(data):
        assert data["grant_type"] == "refresh_token"
        return {"access_token": "new-token", "expires_in": 3600}

    monkeypatch.setattr(google, "_token_request", fake_token_request)

    assert await google.access_token("user-1") == "new-token"
    assert _store_saved and _store_saved[0]["access_token"] == "new-token"


_store_saved: list[dict] = []


@pytest.mark.asyncio
async def test_not_connected_is_a_clear_error(monkeypatch):
    configure(monkeypatch)
    with pytest.raises(google.GoogleNotConnected, match="Connect with Google"):
        await google.list_events("user-1", {"range": "week"})


# ── Tool calls ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_events_normalises_the_mcp_payload(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(settings, "google_connector_mode", "mcp")
    calls = []

    async def fake_call(server_url, token, tool_name, arguments):
        calls.append((server_url, tool_name, arguments))
        return {
            "content": [{"type": "text", "text": "{}"}],
            "structuredContent": {
                "summary": "Manoj",
                "events": [
                    {
                        "summary": "Standup",
                        "start": {"dateTime": "2026-11-21T09:00:00"},
                        "end": {"dateTime": "2026-11-21T09:15:00"},
                        "location": "Zoom",
                        "attendees": [{"email": "a@example.com"}],
                        "htmlLink": "https://calendar.google.com/x",
                    }
                ],
            },
        }

    monkeypatch.setattr(google.client, "call_tool", fake_call)
    monkeypatch.setattr(google, "access_token", lambda user_id, force_refresh=False: _token())

    result = await google.list_events("user-1", {"range": "tomorrow"})

    assert result["count"] == 1
    assert result["events"][0]["title"] == "Standup"
    assert result["events"][0]["location"] == "Zoom"
    assert result["calendar"] == "Manoj"
    assert calls[0][1] == "list_events"
    assert calls[0][2]["calendarId"] == "primary"
    assert calls[0][2]["startTime"].startswith("2026-")
    assert calls[0][2]["orderBy"] == "startTime"


async def _token() -> str:
    return "ya29.test"


@pytest.mark.asyncio
async def test_search_threads_uses_gmail_query(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(settings, "google_connector_mode", "mcp")
    calls = []

    async def fake_call(server_url, token, tool_name, arguments):
        calls.append((server_url, tool_name, arguments))
        return {
            "structuredContent": {
                "threads": [
                    {
                        "id": "t1",
                        "messages": [
                            {
                                "subject": "Invoice",
                                "sender": "billing@stripe.com",
                                "date": "2026-11-19",
                                "snippet": "Your invoice is ready",
                            }
                        ],
                    }
                ]
            }
        }

    monkeypatch.setattr(google.client, "call_tool", fake_call)
    monkeypatch.setattr(google, "access_token", lambda user_id, force_refresh=False: _token())

    result = await google.search_threads(
        "user-1", {"query": "from:billing@stripe.com is:unread", "limit": 5}
    )

    assert result["count"] == 1
    assert result["threads"][0]["subject"] == "Invoice"
    assert result["threads"][0]["from"] == "billing@stripe.com"
    assert calls[0][1] == "search_threads"
    assert calls[0][2] == {"query": "from:billing@stripe.com is:unread", "pageSize": 5}
    assert calls[0][0] == settings.google_gmail_mcp_url


@pytest.mark.asyncio
async def test_write_tool_is_blocked_before_any_network_call(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(settings, "google_connector_mode", "mcp")

    async def explode(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("write tool reached the network")

    monkeypatch.setattr(google.client, "call_tool", explode)
    with pytest.raises(google.GoogleError, match="read-only"):
        await google._call("user-1", settings.google_calendar_mcp_url, "create_event", {})


# ── REST transport (the default) ─────────────────────────────────────────────

def test_unknown_transport_falls_back_to_rest(monkeypatch):
    monkeypatch.setattr(settings, "google_connector_mode", "banana")
    assert google.connector_mode() == "rest"


def test_only_implemented_tools_are_marked_live(monkeypatch):
    monkeypatch.setattr(settings, "google_connector_mode", "rest")
    rest = {tool["name"]: tool["available"] for tool in google.available_tools()}
    assert rest["list_events"] is True
    assert rest["search_threads"] is True
    # Listed, but not implemented over REST yet — must not claim "live".
    assert rest["suggest_time"] is False
    assert rest["list_labels"] is False
    # Write tools are never available.
    assert rest["create_event"] is False

    monkeypatch.setattr(settings, "google_connector_mode", "mcp")
    mcp = {tool["name"]: tool["available"] for tool in google.available_tools()}
    assert mcp["suggest_time"] is True
    assert mcp["create_event"] is False


@pytest.mark.asyncio
async def test_rest_events_use_the_calendar_api(monkeypatch):
    configure(monkeypatch)
    calls = []

    async def fake_get(url, token, params, api):
        calls.append((url, token, params, api))
        return {
            "summary": "Manoj",
            "items": [
                {
                    "summary": "Standup",
                    "start": {"dateTime": "2026-11-23T09:00:00+05:30"},
                    "end": {"dateTime": "2026-11-23T09:15:00+05:30"},
                    "location": "Zoom",
                    "attendees": [{"email": "a@example.com"}],
                    "htmlLink": "https://calendar.google.com/x",
                }
            ],
        }

    monkeypatch.setattr(google, "_api_get", fake_get)
    monkeypatch.setattr(
        google, "access_token", lambda user_id, force_refresh=False: _token()
    )

    result = await google.list_events("user-1", {"range": "today"})

    assert result["count"] == 1
    assert result["calendar"] == "Manoj"
    assert result["events"][0]["title"] == "Standup"
    assert result["events"][0]["start"] == "2026-11-23T09:00:00+05:30"
    url, token, params, api = calls[0]
    assert url.endswith("/calendars/primary/events")
    assert api == "Calendar"
    assert params["singleEvents"] == "true"
    assert params["orderBy"] == "startTime"
    # The REST API requires an offset; the MCP server does not.
    assert params["timeMin"].endswith(("+05:30", "Z")) or "+" in params["timeMin"]


@pytest.mark.asyncio
async def test_rest_threads_read_metadata_headers(monkeypatch):
    configure(monkeypatch)
    calls = []

    async def fake_get(url, token, params, api):
        calls.append((url, params))
        if url.endswith("/threads"):
            return {"threads": [{"id": "t1"}, {"id": "t2"}], "resultSizeEstimate": 2}
        thread_id = url.rsplit("/", 1)[-1]
        return {
            "snippet": "hello",
            "messages": [
                {
                    "snippet": "hello",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": f"Subject {thread_id}"},
                            {"name": "From", "value": "billing@stripe.com"},
                            {"name": "Date", "value": "Mon, 23 Nov 2026 09:00:00 +0530"},
                        ]
                    },
                }
            ],
        }

    monkeypatch.setattr(google, "_api_get", fake_get)
    monkeypatch.setattr(
        google, "access_token", lambda user_id, force_refresh=False: _token()
    )

    result = await google.search_threads(
        "user-1", {"query": "is:unread in:inbox", "limit": 5}
    )

    assert result["count"] == 2
    assert result["estimate"] == 2
    subjects = {thread["subject"] for thread in result["threads"]}
    assert subjects == {"Subject t1", "Subject t2"}
    assert result["threads"][0]["from"] == "billing@stripe.com"
    url, params = calls[0]
    assert url.endswith("/users/me/threads")
    assert params == {"q": "is:unread in:inbox", "maxResults": 5}
    # Thread details ask for metadata only — no message bodies.
    assert calls[1][1]["format"] == "metadata"
    assert "Subject" in calls[1][1]["metadataHeaders"]


@pytest.mark.asyncio
async def test_rest_401_triggers_one_refresh(monkeypatch):
    configure(monkeypatch)
    tokens = []

    async def fake_get(url, token, params, api):
        tokens.append(token)
        if token == "stale":
            raise google.GoogleAuthError("401")
        return {"items": []}

    async def fake_access(user_id, force_refresh=False):
        return "fresh" if force_refresh else "stale"

    monkeypatch.setattr(google, "_api_get", fake_get)
    monkeypatch.setattr(google, "access_token", fake_access)

    result = await google.list_events("user-1", {"range": "week"})

    assert result["count"] == 0
    assert tokens == ["stale", "fresh"]


def test_disabled_api_error_is_actionable():
    response = httpx.Response(
        403,
        json={
            "error": {
                "message": "Gmail API has not been used in project 1 before or it is disabled.",
                "details": [{"reason": "accessNotConfigured"}],
            }
        },
    )
    error = google._api_error(response, "Gmail")
    assert "not enabled" in str(error)
    assert "Gmail" in str(error)


# ── Router ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calendar_question_routes_to_events():
    decision = await route_question("what's on my calendar this week?")
    assert decision.route == "tool:gcal_events"
    assert decision.via == "rule"
    assert decision.args["range"] == "week"


@pytest.mark.asyncio
async def test_calendar_meeting_with_person_becomes_a_search():
    decision = await route_question("do I have any meetings with Ariel tomorrow?")
    assert decision.route == "tool:gcal_search"
    assert decision.args["query"] == "Ariel"
    assert decision.args["range"] == "tomorrow"


@pytest.mark.asyncio
async def test_gmail_question_builds_gmail_syntax():
    decision = await route_question("show my unread emails from billing@stripe.com")
    assert decision.route == "tool:gmail_search"
    query = decision.args["query"]
    assert "is:unread" in query
    assert "from:billing@stripe.com" in query


@pytest.mark.asyncio
async def test_plain_mail_wording_routes_to_gmail():
    # "mail" alone must count — this is how people actually ask.
    decision = await route_question("check my mail that comes today")
    assert decision.route == "tool:gmail_search"
    query = decision.args["query"]
    assert "newer_than:1d" in query
    assert "in:inbox" in query


@pytest.mark.asyncio
async def test_sent_mail_uses_the_sent_folder():
    decision = await route_question("show my sent emails")
    assert decision.route == "tool:gmail_search"
    assert "in:sent" in decision.args["query"]


@pytest.mark.asyncio
async def test_product_api_wording_stays_in_rag():
    for question in (
        "how do I send mail with the Gmail API?",
        "what does the Google Calendar API do?",
        "which OAuth scopes does Gmail need?",
    ):
        decision = await route_question(question)
        assert decision.route == "rag", question


@pytest.mark.asyncio
async def test_third_party_api_mentioned_in_a_mail_question_is_still_mail():
    decision = await route_question("find emails about the Stripe API")
    assert decision.route == "tool:gmail_search"
    assert decision.args["query"] == '"Stripe API"'


@pytest.mark.asyncio
async def test_google_api_questions_stay_in_rag():
    for question in (
        "how do I integrate the Gmail API?",
        "what does the Google Calendar API do?",
        "which OAuth scopes does Gmail need?",
    ):
        decision = await route_question(question)
        assert decision.route == "rag", question


@pytest.mark.asyncio
async def test_google_write_request_is_routed_then_blocked():
    import app.routes.ask as ask_module

    decision = await route_question("schedule a meeting with Ariel tomorrow")
    assert decision.route == "tool:google_write"

    with pytest.raises(ToolError, match="read-only"):
        await ask_module._execute_tool("google_write", decision.args, "user-1")


@pytest.mark.asyncio
async def test_google_tools_need_a_signed_in_user():
    import app.routes.ask as ask_module

    with pytest.raises(ToolError, match="Sign in"):
        await ask_module._execute_tool("gcal_events", {"range": "week"}, None)


# ── Status endpoint + readable fallbacks ────────────────────────────────────

def test_connections_endpoint_includes_google():
    client = TestClient(app)
    response = client.get("/api/connections")
    assert response.status_code == 200

    payload = response.json()["google"]
    assert payload["configured"] is False
    assert payload["connected"] is False
    assert payload["apps"] == ["calendar", "gmail"]
    assert payload["tools"]


def test_calendar_results_read_like_a_sentence():
    text = format_tool_result(
        "gcal_events",
        {
            "calendar": "Manoj",
            "range": "tomorrow",
            "count": 1,
            "events": [
                {
                    "title": "Standup",
                    "start": "2026-11-21T09:00:00",
                    "location": "Zoom",
                }
            ],
        },
    )
    assert "1 event" in text
    assert "Standup" in text
    assert "2026-11-21 09:00" in text


def test_gmail_results_read_like_a_sentence():
    text = format_tool_result(
        "gmail_search",
        {
            "query": "is:unread",
            "count": 1,
            "threads": [
                {
                    "subject": "Invoice",
                    "from": "billing@stripe.com",
                    "date": "2026-11-19",
                }
            ],
        },
    )
    assert "Invoice" in text
    assert "billing@stripe.com" in text
