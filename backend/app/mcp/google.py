"""Google Calendar + Gmail through Google's official remote MCP servers.

Google hosts the MCP servers; this module owns the OAuth 2.0 web flow,
per-user token refresh, and the read-only tool allowlist.

Security posture:

- The MCP server URLs are settings constants. Nothing the browser sends
  can change them, so there is no SSRF surface.
- Only allowlisted tool names are ever called.
- Write tools are listed (so the UI is honest) but blocked, exactly like
  GitHub's ``create_issue``: a model must not mutate a user's calendar or
  mail without an explicit approval step.
- Gmail output is untrusted text. Ask presents it as data and never lets
  an email instruct the model to call another tool.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.mcp import client, crypto, store
from app.observability import hash_user_id, traced

PROVIDER = "google"

SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# OAuth state is signed with the same secret used for token encryption and
# expires quickly: it only has to survive one consent-screen round trip.
STATE_TTL_S = 900
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

CALENDAR_TOOLS: list[dict[str, Any]] = [
    {
        "app": "calendar",
        "name": "list_calendars",
        "description": "List the calendars in the account",
        "write": False,
        "available": True,
    },
    {
        "app": "calendar",
        "name": "list_events",
        "description": "List events in a date range",
        "write": False,
        "available": True,
    },
    {
        "app": "calendar",
        "name": "search_events",
        "description": "Search events by title, description, or attendee",
        "write": False,
        "available": True,
    },
    {
        "app": "calendar",
        "name": "suggest_time",
        "description": "Suggest free slots for a meeting",
        "write": False,
        "available": True,
    },
    {
        "app": "calendar",
        "name": "create_event",
        "description": "Schedule a new event",
        "write": True,
        "available": False,
    },
    {
        "app": "calendar",
        "name": "update_event",
        "description": "Change an existing event",
        "write": True,
        "available": False,
    },
    {
        "app": "calendar",
        "name": "delete_event",
        "description": "Delete an event",
        "write": True,
        "available": False,
    },
    {
        "app": "calendar",
        "name": "respond_to_event",
        "description": "Accept or decline an invitation",
        "write": True,
        "available": False,
    },
]

GMAIL_TOOLS: list[dict[str, Any]] = [
    {
        "app": "gmail",
        "name": "search_threads",
        "description": "Search mail with Gmail query syntax",
        "write": False,
        "available": True,
    },
    {
        "app": "gmail",
        "name": "get_message",
        "description": "Read one message",
        "write": False,
        "available": True,
    },
    {
        "app": "gmail",
        "name": "get_thread",
        "description": "Read a whole conversation",
        "write": False,
        "available": True,
    },
    {
        "app": "gmail",
        "name": "list_labels",
        "description": "List labels in the mailbox",
        "write": False,
        "available": True,
    },
    {
        "app": "gmail",
        "name": "create_draft",
        "description": "Create a draft email",
        "write": True,
        "available": False,
    },
    {
        "app": "gmail",
        "name": "label_message",
        "description": "Apply a label to a message",
        "write": True,
        "available": False,
    },
    {
        "app": "gmail",
        "name": "label_thread",
        "description": "Apply a label to a conversation",
        "write": True,
        "available": False,
    },
]

TOOLS = CALENDAR_TOOLS + GMAIL_TOOLS

# The only tools Ask is allowed to call today.
READ_TOOLS = {
    tool["name"] for tool in TOOLS if tool["available"] and not tool["write"]
}

# Read tools that the REST transport actually implements. Everything else is
# still listed (so the UI stays honest about what exists) but marked as not
# available, because calling it would fail.
_REST_TOOLS = {"list_events", "search_events", "search_threads"}


def available_tools() -> list[dict[str, Any]]:
    """Tool catalog with ``available`` reflecting the active transport."""
    mode = connector_mode()
    tools: list[dict[str, Any]] = []
    for tool in TOOLS:
        item = dict(tool)
        if not item["write"]:
            item["available"] = mode == "mcp" or item["name"] in _REST_TOOLS
        tools.append(item)
    return tools


class GoogleError(Exception):
    """Google could not answer (not configured, not connected, or a failure)."""


class GoogleNotConfigured(GoogleError):
    """OAuth client credentials or the encryption key are missing."""


class GoogleNotConnected(GoogleError):
    """This user has not connected Google yet."""


class GoogleAuthError(GoogleError):
    """Google rejected the access token (401) — refresh or reconnect."""


# ── Configuration ────────────────────────────────────────────────────────────

def configuration_error() -> str | None:
    """What is missing for the Google connectors, or None when ready."""
    missing: list[str] = []
    if not (settings.google_client_id or "").strip():
        missing.append("GOOGLE_CLIENT_ID")
    if not (settings.google_client_secret or "").strip():
        missing.append("GOOGLE_CLIENT_SECRET")
    if not crypto.is_valid_key():
        missing.append("MCP_TOKEN_ENCRYPTION_KEY")
    if missing:
        return (
            "Set " + ", ".join(missing) + " in the backend .env, then restart "
            "the backend."
        )
    return None


def is_configured() -> bool:
    return configuration_error() is None


# ── OAuth state (signed, short-lived) ────────────────────────────────────────

def _state_secret() -> bytes:
    key = (settings.mcp_token_encryption_key or "").strip()
    return key.encode() or b"apifix-oauth-state"


def sign_state(user_id: str, origin: str) -> str:
    """A tamper-proof, expiring state value for the consent round trip."""
    payload = {
        "u": user_id,
        "o": origin,
        "t": int(time.time()),
        "n": secrets.token_urlsafe(8),
    }
    raw = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(_state_secret(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{signature}"


def verify_state(state: str) -> dict[str, Any]:
    """Validate the state from Google's redirect and return its payload."""
    raw, _, signature = (state or "").partition(".")
    if not raw or not signature:
        raise GoogleError("The Google sign-in response is missing its state.")
    expected = hmac.new(_state_secret(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise GoogleError(
            "The Google sign-in response failed verification. Start again "
            "from the Connections sidebar."
        )
    padded = raw + "=" * (-len(raw) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise GoogleError("The Google sign-in state is malformed.") from exc
    if time.time() - int(payload.get("t") or 0) > STATE_TTL_S:
        raise GoogleError("The Google sign-in link expired. Start again.")
    return payload


def authorization_url(user_id: str, origin: str) -> str:
    """Google's consent URL for this user."""
    if not is_configured():
        raise GoogleNotConfigured(configuration_error() or "Google is not configured.")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # offline + consent: without both, Google issues no refresh token and
        # the app would have to ask the user to sign in every hour.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": sign_state(user_id, origin),
    }
    return f"{settings.google_oauth_auth_url}?{urlencode(params)}"


# ── Token exchange and refresh ───────────────────────────────────────────────

def _token_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = str(body.get("error") or "")
    if code == "invalid_grant":
        return (
            "Google rejected the stored authorization (invalid_grant). Click "
            "Connect with Google again."
        )
    if code == "invalid_client":
        return (
            "Google rejected the OAuth client (invalid_client). Check "
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, then restart the backend."
        )
    if code == "redirect_uri_mismatch":
        return (
            "Redirect URI mismatch. Add GOOGLE_REDIRECT_URI to the OAuth "
            "client's authorized redirect URIs in Google Cloud."
        )
    detail = body.get("error_description") or code or f"HTTP {response.status_code}"
    return f"Google OAuth failed: {detail}"


async def _token_request(data: dict[str, str]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client_:
            response = await client_.post(settings.google_oauth_token_url, data=data)
    except httpx.HTTPError as exc:
        raise GoogleError(
            f"Google's token endpoint is unreachable ({type(exc).__name__})."
        ) from exc
    if response.status_code != 200:
        raise GoogleError(_token_error(response))
    try:
        return response.json()
    except ValueError as exc:
        raise GoogleError("Google's token endpoint returned a non-JSON reply.") from exc


def _email_from_id_token(id_token: str | None) -> str | None:
    """The account email from the id_token Google returned over TLS."""
    if not id_token or id_token.count(".") < 2:
        return None
    payload = id_token.split(".")[1]
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        return json.loads(decoded).get("email")
    except (ValueError, json.JSONDecodeError):
        return None


def _expiry(tokens: dict[str, Any]) -> datetime:
    seconds = int(tokens.get("expires_in") or 3600)
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def exchange_code(code: str, redirect_uri: str | None = None) -> dict[str, Any]:
    """Swap the consent code for tokens and store them for the user."""
    tokens = await _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri or settings.google_redirect_uri,
        }
    )
    access_token = tokens.get("access_token")
    if not access_token:
        raise GoogleError("Google did not return an access token.")
    return tokens


async def save_connection(user_id: str, tokens: dict[str, Any]) -> None:
    """Persist a token response (encrypted) for this user."""
    scopes = (tokens.get("scope") or " ".join(SCOPES)).split()
    record = {
        "user_id": user_id,
        "provider": PROVIDER,
        "account_email": _email_from_id_token(tokens.get("id_token")),
        "scopes": scopes,
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": _expiry(tokens),
    }
    try:
        await asyncio.to_thread(store.save, record)
    except crypto.CryptoNotConfigured as exc:
        raise GoogleNotConfigured(str(exc)) from exc


async def _refresh(user_id: str, record: dict[str, Any]) -> str:
    refresh_token = record.get("refresh_token")
    if not refresh_token:
        raise GoogleNotConnected(
            "Google is connected but no refresh token is stored. Click "
            "Connect with Google again to grant offline access."
        )
    tokens = await _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
        }
    )
    access_token = tokens.get("access_token")
    if not access_token:
        raise GoogleError("Google did not return a refreshed access token.")
    await save_connection(
        user_id,
        {
            **tokens,
            # Google omits the refresh token on refresh; keep the stored one.
            "refresh_token": tokens.get("refresh_token") or refresh_token,
            "scope": " ".join(record.get("scopes") or SCOPES),
            "id_token": None,
        },
    )
    return access_token


async def _record(user_id: str) -> dict[str, Any]:
    try:
        record = await asyncio.to_thread(store.get, user_id, PROVIDER)
    except crypto.CryptoNotConfigured as exc:
        raise GoogleNotConfigured(str(exc)) from exc
    except Exception as exc:
        raise GoogleError(
            f"Could not read the stored Google connection ({type(exc).__name__})."
        ) from exc
    if not record:
        raise GoogleNotConnected(
            "Google is not connected for this account. Open Connections in "
            "the Ask tab and click Connect with Google."
        )
    return record


async def access_token(user_id: str, force_refresh: bool = False) -> str:
    """A valid access token, refreshed in the background when needed."""
    record = await _record(user_id)
    if force_refresh or store.is_expired(record):
        return await _refresh(user_id, record)
    return str(record["access_token"])


async def status(user_id: str | None) -> dict[str, Any]:
    """Honest state for the Connections sidebar."""
    info: dict[str, Any] = {
        "configured": is_configured(),
        "connected": False,
        "account": None,
        "expires_at": None,
        "error": configuration_error(),
        "tools": available_tools(),
        "apps": ["calendar", "gmail"],
        "scopes": SCOPES,
        "mode": connector_mode(),
    }
    if not info["configured"]:
        return info
    if not user_id:
        info["error"] = "Sign in to connect Google Calendar or Gmail."
        return info

    try:
        record = await asyncio.to_thread(store.get, user_id, PROVIDER)
    except crypto.CryptoNotConfigured as exc:
        info["error"] = str(exc)
        return info
    except Exception as exc:
        info["error"] = (
            f"Could not read the stored Google connection ({type(exc).__name__})."
        )
        return info

    if not record:
        return info

    info["connected"] = True
    info["account"] = record.get("account_email")
    expires_at = record.get("expires_at")
    info["expires_at"] = expires_at.isoformat() if expires_at else None
    if not record.get("refresh_token"):
        info["error"] = (
            "No refresh token stored — reconnect Google so the connection "
            "survives the one-hour access token."
        )
    return info


async def disconnect(user_id: str) -> bool:
    try:
        return await asyncio.to_thread(store.delete, user_id, PROVIDER)
    except Exception as exc:
        raise GoogleError(
            f"Could not delete the stored Google connection ({type(exc).__name__})."
        ) from exc


# ── Tool calls ───────────────────────────────────────────────────────────────

def _hash_user_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Trace inputs for Google calls: never log the raw user id."""
    return {
        **{key: value for key, value in inputs.items() if key != "user_id"},
        "user_id_hash": hash_user_id(inputs.get("user_id")),
    }


@traced("google.call", run_type="tool", process_inputs=_hash_user_inputs)
async def _call(
    user_id: str, server_url: str, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    if tool_name not in READ_TOOLS:
        raise GoogleError(
            f"'{tool_name}' is a write tool. Ask tools are read-only for now — "
            "write actions will use the same explicit approval gate as "
            "POST/PATCH in the request tester."
        )
    token = await access_token(user_id)
    try:
        return await client.call_tool(server_url, token, tool_name, arguments)
    except client.MCPAuthError:
        # The token may have been revoked early; one forced refresh, then
        # let the error surface with an honest message.
        token = await access_token(user_id, force_refresh=True)
        return await client.call_tool(server_url, token, tool_name, arguments)


def _range_datetimes(label: str) -> tuple[datetime, datetime]:
    """Start/end of a named range in the user's local time zone."""
    now = datetime.now().astimezone()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    label = (label or "week").lower()

    if label == "today":
        start, end = today, today + timedelta(days=1)
    elif label == "tomorrow":
        start, end = today + timedelta(days=1), today + timedelta(days=2)
    elif label == "next_week":
        start = today + timedelta(days=7 - today.weekday())
        end = start + timedelta(days=7)
    elif label == "month":
        start, end = today, today + timedelta(days=30)
    else:  # "week" — the default for "what's coming up"
        start, end = now, today + timedelta(days=7)
    return start, end


def range_bounds(label: str) -> tuple[str, str]:
    """Local ISO timestamps (no offset) for a named range.

    Google's MCP docs are explicit: send local time without a UTC offset and
    let the server resolve the user's time zone. The REST Calendar API is the
    opposite (it requires an offset), so it uses ``_range_datetimes`` directly.
    """
    start, end = _range_datetimes(label)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt)


def _find_list(payload: Any, key: str) -> list[Any]:
    """Depth-first search for a list stored under ``key``."""
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        for child in payload.values():
            found = _find_list(child, key)
            if found:
                return found
    elif isinstance(payload, list):
        for child in payload:
            found = _find_list(child, key)
            if found:
                return found
    return []


def _events_from(payload: Any) -> list[dict[str, Any]]:
    events = []
    for item in _find_list(payload, "events"):
        if not isinstance(item, dict):
            continue
        start = item.get("start") or {}
        end = item.get("end") or {}
        attendees = item.get("attendees") or []
        events.append(
            {
                "title": item.get("summary") or "(no title)",
                "start": start.get("dateTime") or start.get("date"),
                "end": end.get("dateTime") or end.get("date"),
                "location": item.get("location"),
                "attendees": [
                    attendee.get("email")
                    for attendee in attendees
                    if isinstance(attendee, dict) and attendee.get("email")
                ][:8],
                "status": item.get("status"),
                "url": item.get("htmlLink"),
            }
        )
    return events


def _threads_from(payload: Any) -> list[dict[str, Any]]:
    threads = []
    for item in _find_list(payload, "threads"):
        if not isinstance(item, dict):
            continue
        messages = item.get("messages") or []
        last = messages[-1] if messages and isinstance(messages[-1], dict) else {}
        threads.append(
            {
                "id": item.get("id"),
                "subject": last.get("subject"),
                "from": last.get("sender"),
                "date": last.get("date"),
                "snippet": last.get("snippet"),
            }
        )
    return threads


# ── REST transport (no Developer Preview needed) ────────────────────────────
#
# Google's MCP servers are still gated behind the Workspace Developer Preview
# Program. The public Calendar and Gmail REST APIs expose the same read data
# and accept the very same OAuth grant, so they are the default transport.
# Set GOOGLE_CONNECTOR_MODE=mcp once the project is enrolled.

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


def connector_mode() -> str:
    """Which transport the tools use ("rest" unless explicitly set to "mcp")."""
    mode = (settings.google_connector_mode or "rest").strip().lower()
    return mode if mode in {"rest", "mcp"} else "rest"


def _api_error(response: httpx.Response, api: str) -> GoogleError:
    """Map a Google API failure to an actionable message."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    message = ""
    reason = ""
    if isinstance(error, dict):
        message = str(error.get("message") or "")
        details = error.get("details") or []
        for detail in details:
            if isinstance(detail, dict) and detail.get("reason"):
                reason = str(detail["reason"])
                break
        if not reason:
            reason = str(error.get("status") or "")

    if response.status_code == 401:
        return GoogleAuthError(
            "Google rejected the access token (401). Reconnect Google in the "
            "Connections sidebar."
        )
    if response.status_code == 403:
        if reason in {"accessNotConfigured", "SERVICE_DISABLED"} or "has not been used" in message:
            return GoogleError(
                f"The {api} API is not enabled for this Google Cloud project. "
                f"Enable it in APIs & Services → Library, then try again."
            )
        if "insufficient" in message.lower() or reason == "insufficientPermissions":
            return GoogleError(
                f"The Google grant does not include the scope needed for {api}. "
                "Disconnect Google, then reconnect and approve all requested "
                "permissions."
            )
        return GoogleError(
            f"Google denied the {api} request (403): {message or reason or 'forbidden'}."
        )
    if response.status_code == 429:
        return GoogleError(
            f"Google rate-limited the {api} API (429). Wait a moment, then ask again."
        )
    return GoogleError(
        f"The {api} API returned HTTP {response.status_code}"
        + (f": {message}" if message else ".")
    )


async def _api_get(
    url: str, token: str, params: dict[str, Any], api: str
) -> dict[str, Any]:
    """One authenticated GET against a Google REST API."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as http:
            response = await http.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        raise GoogleError(
            f"The {api} API is unreachable ({type(exc).__name__})."
        ) from exc

    if response.status_code >= 400:
        raise _api_error(response, api)
    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleError(f"The {api} API returned a non-JSON reply.") from exc
    return payload if isinstance(payload, dict) else {}


@traced("google.rest_get", run_type="tool", process_inputs=_hash_user_inputs)
async def _rest_get(user_id: str, url: str, params: dict[str, Any], api: str) -> dict[str, Any]:
    """GET with one forced token refresh when Google answers 401."""
    token = await access_token(user_id)
    try:
        return await _api_get(url, token, params, api)
    except GoogleAuthError:
        token = await access_token(user_id, force_refresh=True)
        return await _api_get(url, token, params, api)


def _event_from_rest(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise a Calendar API event to the shape the Ask tab renders."""
    start = item.get("start") or {}
    end = item.get("end") or {}
    attendees = item.get("attendees") or []
    return {
        "title": item.get("summary") or "(no title)",
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "location": item.get("location"),
        "attendees": [
            attendee.get("email")
            for attendee in attendees
            if isinstance(attendee, dict) and attendee.get("email")
        ][:8],
        "status": item.get("status"),
        "url": item.get("htmlLink"),
    }


def _header(message: dict[str, Any], name: str) -> str | None:
    headers = (message.get("payload") or {}).get("headers") or []
    for header in headers:
        if str(header.get("name") or "").lower() == name.lower():
            return header.get("value")
    return None


async def _rest_thread(user_id: str, thread_id: str) -> dict[str, Any]:
    """Subject/from/date for one thread (metadata only, no message bodies)."""
    payload = await _rest_get(
        user_id,
        f"{GMAIL_API}/users/me/threads/{thread_id}",
        {
            "format": "metadata",
            "metadataHeaders": ["Subject", "From", "Date"],
        },
        "Gmail",
    )
    messages = payload.get("messages") or []
    last = messages[-1] if messages else {}
    return {
        "id": thread_id,
        "subject": _header(last, "Subject"),
        "from": _header(last, "From"),
        "date": _header(last, "Date"),
        "snippet": last.get("snippet") or payload.get("snippet"),
    }


async def _rest_events(
    user_id: str, label: str, query: str
) -> dict[str, Any]:
    start, end = _range_datetimes(label)
    params: dict[str, Any] = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 20,
    }
    if query:
        params["q"] = query

    payload = await _rest_get(
        user_id, f"{CALENDAR_API}/calendars/primary/events", params, "Calendar"
    )
    events = [
        _event_from_rest(item)
        for item in payload.get("items") or []
        if isinstance(item, dict)
    ]
    return {
        "range": label,
        "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "calendar": payload.get("summary") or "primary",
        "query": query or None,
        "count": len(events),
        "events": events,
    }


async def _rest_threads(
    user_id: str, query: str, limit: int
) -> dict[str, Any]:
    payload = await _rest_get(
        user_id,
        f"{GMAIL_API}/users/me/threads",
        {"q": query, "maxResults": limit},
        "Gmail",
    )
    ids = [
        str(item["id"])
        for item in payload.get("threads") or []
        if isinstance(item, dict) and item.get("id")
    ][:limit]
    threads = await asyncio.gather(
        *(_rest_thread(user_id, thread_id) for thread_id in ids)
    )
    return {
        "query": query,
        "count": len(threads),
        "estimate": payload.get("resultSizeEstimate"),
        "threads": list(threads),
    }


async def list_events(user_id: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Events in a named range, optionally filtered by free text."""
    args = args or {}
    label = str(args.get("range") or "week")
    query = str(args.get("query") or "").strip()

    if connector_mode() != "mcp":
        return await _rest_events(user_id, label, query)

    start, end = range_bounds(label)
    arguments: dict[str, Any] = {
        "calendarId": "primary",
        "pageSize": 20,
        "startTime": start,
        "endTime": end,
        "orderBy": "startTime",
    }
    if query:
        arguments["fullText"] = query

    result = await _call(
        user_id, settings.google_calendar_mcp_url, "list_events", arguments
    )
    payload = client.payload_of(result)
    events = _events_from(payload)
    calendar = payload.get("summary") if isinstance(payload, dict) else None
    return {
        "range": label,
        "start": start,
        "end": end,
        "calendar": calendar or "primary",
        "query": query or None,
        "count": len(events),
        "events": events,
    }


async def search_events(
    user_id: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Keyword search over a month of events (same tool, fullText filter)."""
    args = dict(args or {})
    args.setdefault("range", "month")
    return await list_events(user_id, args)


async def search_threads(
    user_id: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Gmail search using Gmail query syntax."""
    args = args or {}
    query = str(args.get("query") or "").strip() or "in:inbox"
    limit = max(1, min(int(args.get("limit") or 10), 25))

    if connector_mode() != "mcp":
        return await _rest_threads(user_id, query, limit)

    result = await _call(
        user_id,
        settings.google_gmail_mcp_url,
        "search_threads",
        {"query": query, "pageSize": limit},
    )
    payload = client.payload_of(result)
    threads = _threads_from(payload)
    estimate = payload.get("resultCountEstimate") if isinstance(payload, dict) else None
    return {
        "query": query,
        "count": len(threads),
        "estimate": estimate,
        "threads": threads,
    }
