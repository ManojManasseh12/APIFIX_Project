"""Sentry connector for the Ask tab.

This closes the loop the product is named after: Diagnose explains *your test
request*, and Sentry explains what is failing in *real traffic*.

The auth token lives in ``SENTRY_AUTH_TOKEN`` in the backend ``.env`` and never
leaves the server — the browser only sees connection status.

Scope is **read-only**: listing issues, reading one issue's latest event, and
searching events. Nothing here can resolve, assign, or delete anything.

Requests are fixed-path calls to the Sentry API with a server-side token, so
there is no user-supplied URL and no SSRF surface. Request headers from an
event are filtered before they reach a model: ``authorization`` and ``cookie``
are never forwarded.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from app.core.config import settings
from app.observability import traced

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_MAX_ISSUES = 20
_MAX_EVENTS = 20
_MAX_FRAMES = 12

# Cache the org lookup so opening the Ask tab does not hit Sentry every time.
_STATUS_TTL_S = 60.0
_status_cache: dict[str, Any] | None = None
_status_cached_at = 0.0


class SentryError(Exception):
    """Sentry could not answer (bad token, wrong org/project, network down)."""


class SentryNotConfigured(SentryError):
    """No SENTRY_AUTH_TOKEN / SENTRY_ORG / SENTRY_PROJECT is set."""


# What the sidebar shows. Every tool is read-only and available once the
# token is configured.
SENTRY_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_issues",
        "description": "Unresolved issues in the project, newest first",
        "write": False,
        "available": True,
    },
    {
        "name": "get_issue_event",
        "description": "Latest event for one issue: exception, stack, request",
        "write": False,
        "available": True,
    },
    {
        "name": "search_events",
        "description": "Events matching an endpoint URL or search query",
        "write": False,
        "available": True,
    },
]


def _token() -> str:
    return (settings.sentry_auth_token or "").strip()


def _org() -> str:
    return (settings.sentry_org or "").strip()


def _project() -> str:
    return (settings.sentry_project or "").strip()


def is_configured() -> bool:
    """True when a token, org, and project are all present."""
    return bool(_token() and _org() and _project())


def reset_status_cache() -> None:
    """Drop the cached org result (tests and token changes)."""
    global _status_cache, _status_cached_at
    _status_cache = None
    _status_cached_at = 0.0


def _require_config() -> None:
    if not is_configured():
        raise SentryNotConfigured(
            "Sentry is not connected. Add SENTRY_AUTH_TOKEN, SENTRY_ORG, and "
            "SENTRY_PROJECT to the backend .env, then restart the backend."
        )


@traced("sentry.get", run_type="tool")
async def _get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    missing_ok: bool = False,
) -> Any:
    """One authenticated GET against the Sentry API."""
    _require_config()

    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
        "User-Agent": "apifix-ai",
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.sentry_api_url.rstrip("/"),
            timeout=_TIMEOUT,
            trust_env=False,
        ) as client:
            response = await client.get(path, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise SentryError(
            f"Sentry API is unreachable ({type(exc).__name__})."
        ) from exc

    if response.status_code == 401:
        raise SentryError(
            "Sentry rejected the token (401). Check SENTRY_AUTH_TOKEN — it may "
            "be expired or revoked — then restart the backend."
        )
    if response.status_code == 403:
        raise SentryError(
            "Sentry denied this request (403). The token needs the org:read, "
            "project:read, and event:read scopes."
        )
    if response.status_code == 404:
        if missing_ok:
            return None
        raise SentryError(
            "Sentry could not find that resource (404). Check SENTRY_ORG and "
            "SENTRY_PROJECT, and that the token can see them."
        )
    if response.status_code == 429:
        raise SentryError(
            "Sentry rate limit reached for this token. Try again in a minute."
        )
    if response.status_code >= 400:
        raise SentryError(f"Sentry returned HTTP {response.status_code}.")

    try:
        return response.json()
    except ValueError as exc:
        raise SentryError("Sentry returned a response that was not JSON.") from exc


async def connection_status() -> dict[str, Any]:
    """Honest status for the sidebar: configured? connected? which org?"""
    global _status_cache, _status_cached_at

    status: dict[str, Any] = {
        "configured": is_configured(),
        "connected": False,
        "account": None,
        "org": _org() or None,
        "project": _project() or None,
        "source": "auth_token",
        "error": None,
        "tools": SENTRY_TOOLS,
    }
    if not status["configured"]:
        return status

    now = time.monotonic()
    if _status_cache is not None and now - _status_cached_at < _STATUS_TTL_S:
        return _status_cache

    try:
        organization = await _get(f"/organizations/{_org()}/")
    except SentryError as exc:
        status["error"] = str(exc)
        return status

    status.update(
        {
            "connected": True,
            "account": organization.get("slug") or _org(),
        }
    )
    _status_cache = status
    _status_cached_at = now
    return status


def _as_int(value: Any) -> int:
    """Sentry sends counts as strings ("42"); keep the number, never crash."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
    metadata = issue.get("metadata") or {}
    return {
        "id": issue.get("id"),
        "short_id": issue.get("shortId"),
        "title": issue.get("title") or metadata.get("value"),
        "culprit": issue.get("culprit"),
        "level": issue.get("level"),
        "status": issue.get("status"),
        "count": _as_int(issue.get("count")),
        "users": _as_int(issue.get("userCount")),
        "first_seen": issue.get("firstSeen"),
        "last_seen": issue.get("lastSeen"),
        "url": issue.get("permalink"),
    }


async def list_issues(
    query: str = "is:unresolved", period: str = "14d", limit: int = 10
) -> dict[str, Any]:
    """Issues in the configured project, newest first."""
    _require_config()
    limit = max(1, min(int(limit), _MAX_ISSUES))
    query = (query or "").strip() or "is:unresolved"
    period = (period or "14d").strip()

    issues = await _get(
        f"/projects/{_org()}/{_project()}/issues/",
        params={
            "query": query,
            "statsPeriod": period,
            "sort": "date",
            "limit": limit,
        },
    )
    if not isinstance(issues, list):
        raise SentryError("Sentry returned an unexpected answer for the issue list.")

    return {
        "org": _org(),
        "project": _project(),
        "query": query,
        "period": period,
        "count": len(issues),
        "issues": [_issue_summary(issue) for issue in issues],
    }


def _tags_map(event: dict[str, Any]) -> dict[str, str]:
    """Sentry sends tags as [{key, value}] or as a dict, depending on endpoint."""
    tags = event.get("tags") or []
    if isinstance(tags, dict):
        return {str(key): str(value) for key, value in tags.items()}
    result: dict[str, str] = {}
    for tag in tags:
        if isinstance(tag, dict) and tag.get("key") is not None:
            result[str(tag["key"])] = str(tag.get("value") or "")
    return result


def _exception_summary(event: dict[str, Any]) -> dict[str, Any]:
    """The primary exception plus the frames closest to the crash."""
    metadata = event.get("metadata") or {}
    values: list[dict[str, Any]] = []
    request: dict[str, Any] = {}

    for entry in event.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "exception":
            values = ((entry.get("data") or {}).get("values")) or []
        elif entry.get("type") == "request":
            request = entry.get("data") or {}

    chosen = next(
        (value for value in reversed(values) if isinstance(value, dict)), {}
    )
    exception = {
        "type": chosen.get("type") or metadata.get("type"),
        "value": chosen.get("value") or metadata.get("value"),
    }

    frames: list[dict[str, Any]] = []
    stacktrace = chosen.get("stacktrace") or {}
    for frame in (stacktrace.get("frames") or [])[-_MAX_FRAMES:]:
        if not isinstance(frame, dict):
            continue
        frames.append(
            {
                "filename": frame.get("filename") or frame.get("absPath"),
                "function": frame.get("function"),
                "line": frame.get("lineNo"),
                "in_app": bool(frame.get("inApp")),
            }
        )
    # Sentry orders frames oldest → newest; the crash site is the last one.
    frames.reverse()

    return {"exception": exception, "frames": frames, "request": request}


def _safe_request(request: dict[str, Any], tags: dict[str, str]) -> dict[str, Any]:
    """Only the parts of a request that help debugging — never credentials."""
    headers: dict[str, str] = {}
    for item in request.get("headers") or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        name = str(item[0])
        if name.lower() in {"authorization", "cookie", "set-cookie", "x-api-key"}:
            continue
        if name.lower() in {"content-type", "accept", "user-agent"}:
            headers[name] = str(item[1])[:120]

    return {
        "url": request.get("url") or tags.get("url"),
        "method": request.get("method") or tags.get("method"),
        "status_code": tags.get("status_code") or tags.get("http.status_code"),
        "headers": headers,
    }


async def get_issue_event(issue_id: str) -> dict[str, Any]:
    """One issue plus its latest event: exception, stack, and request."""
    _require_config()
    issue_id = str(issue_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", issue_id):
        raise SentryError(
            "Tell me the Sentry issue id (the number in the issue URL), for "
            'example "show issue 1234567890".'
        )

    issue = await _get(f"/issues/{issue_id}/")
    event = await _get(f"/issues/{issue_id}/events/latest/", missing_ok=True)
    if event is None:
        raise SentryError(
            f"Issue {issue_id} has no events yet, so there is nothing to read."
        )

    tags = _tags_map(event)
    details = _exception_summary(event)
    return {
        "issue": _issue_summary(issue),
        "event": {
            "event_id": event.get("eventID") or event.get("id"),
            "timestamp": event.get("dateCreated") or event.get("timestamp"),
            "title": event.get("title") or event.get("message"),
            "environment": tags.get("environment"),
            "release": tags.get("release") or event.get("release"),
            "transaction": tags.get("transaction"),
            "exception": details["exception"],
            "frames": details["frames"],
            "request": _safe_request(details["request"], tags),
        },
    }


async def search_events(
    endpoint: str = "",
    query: str = "",
    period: str = "14d",
    limit: int = 5,
) -> dict[str, Any]:
    """Events matching an endpoint URL (or an explicit Sentry query)."""
    _require_config()
    limit = max(1, min(int(limit), _MAX_EVENTS))
    endpoint = (endpoint or "").strip()
    query = (query or "").strip()

    if not query:
        if endpoint:
            # Sentry's search syntax: url:"*<fragment>*" matches any URL.
            query = f'url:"*{endpoint}*"'
        else:
            query = "is:unresolved"

    events = await _get(
        f"/projects/{_org()}/{_project()}/events/",
        params={"query": query, "statsPeriod": (period or "14d").strip()},
    )
    if not isinstance(events, list):
        raise SentryError("Sentry returned an unexpected answer for the event search.")

    summaries: list[dict[str, Any]] = []
    for event in events[:limit]:
        if not isinstance(event, dict):
            continue
        tags = _tags_map(event)
        summaries.append(
            {
                "event_id": event.get("eventID") or event.get("id"),
                "timestamp": event.get("dateCreated") or event.get("timestamp"),
                "title": event.get("title") or event.get("message"),
                "culprit": event.get("culprit"),
                "url": tags.get("url"),
                "method": tags.get("method"),
                "status_code": tags.get("status_code")
                or tags.get("http.status_code"),
                "environment": tags.get("environment"),
                "release": tags.get("release"),
            }
        )

    return {
        "org": _org(),
        "project": _project(),
        "query": query,
        "endpoint": endpoint or None,
        "period": (period or "14d").strip(),
        "count": len(summaries),
        "events": summaries,
    }
