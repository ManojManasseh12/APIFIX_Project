"""Redact secrets and shrink payloads before they are sent to LangSmith.

This app handles auth headers, cookies, OAuth tokens, GitHub/Sentry payloads,
emails and calendar data. A trace is a third-party copy of that data, so it is
sanitized here — the model and the UI still receive the real values.

Rules:
- header names in ``SENSITIVE_HEADERS`` and keys in ``SENSITIVE_KEYS`` become
  ``<REDACTED>``;
- Gmail snippets and calendar attendee lists are dropped (trace-only rule);
- known token shapes (JWTs, ``ghp_``/``sk-`` keys, ``Bearer ...``) are redacted
  anywhere in a string via ``redact_text``;
- strings are truncated, lists/dicts are capped, recursion is bounded.

Nothing here is used on the response path; it only builds trace inputs/outputs.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.config import settings
from app.core.security import (
    SENSITIVE_HEADERS,
    SENSITIVE_KEYS,
    redact_text,
)

MAX_STRING_CHARS = 2_000
MAX_ITEMS = 50
MAX_DEPTH = 6

# Trace-only additions: these keys never need to reach LangSmith even though
# the app itself uses them (Gmail previews, calendar attendees).
_TRACE_ONLY_KEYS = {
    "snippet",
    "attendees",
    "set-cookie",
    "proxy-authorization",
    "x-auth-token",
}

# Bound-method receivers are never useful in a trace and can hold clients or
# caches, so drop them instead of serializing them.
_DROPPED_KEYS = {"self", "cls"}

_SENSITIVE_KEYS = {key.lower() for key in SENSITIVE_KEYS} | {
    key.lower() for key in SENSITIVE_HEADERS
} | _TRACE_ONLY_KEYS

_QUERY_SECRET = re.compile(
    r"(?i)\b(?:token|access_token|refresh_token|id_token|api_key|apikey|"
    r"password|secret|signature|sig|key)=([^&\s]+)"
)


def _hash_salt() -> str:
    return (settings.langsmith_user_salt or "apifix").strip() or "apifix"


def hash_user_id(user_id: str | None) -> str | None:
    """Stable, non-reversible user marker for trace metadata.

    LangSmith is a third party; the raw Supabase user id (or email) must not
    appear in a trace. The salt comes from ``LANGSMITH_USER_SALT``.
    """
    if not user_id:
        return None
    digest = hashlib.sha256(f"{_hash_salt()}:{user_id}".encode("utf-8")).hexdigest()
    return digest[:16]


def _truncate(value: str) -> str:
    if len(value) <= MAX_STRING_CHARS:
        return value
    return value[:MAX_STRING_CHARS] + "…<truncated>"


def sanitize_url(url: str) -> str:
    """Keep scheme/host/path, redact secret-looking query values."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return redact_text(_truncate(url))
    query = [
        (
            key,
            "<REDACTED>"
            if key.lower() in _SENSITIVE_KEYS
            else redact_text(_truncate(value)),
        )
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    redacted = _QUERY_SECRET.sub(lambda match: f"{match.group(0).split('=')[0]}=<REDACTED>", parts.query)
    # Prefer the structured rewrite; fall back to the regex for malformed input.
    safe_query = urlencode(query, safe="<>") if query else redacted
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, safe_query, parts.fragment)
    )


def _coerce(value: Any, depth: int) -> Any:
    if depth > MAX_DEPTH:
        return "<truncated:depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return _truncate(value.decode("utf-8", errors="replace"))
    if isinstance(value, str):
        return _truncate(redact_text(value))
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                result["<truncated:keys>"] = True
                break
            key = str(raw_key)
            if key in _DROPPED_KEYS:
                continue
            if key.lower() in _SENSITIVE_KEYS:
                result[key] = "<REDACTED>"
            else:
                result[key] = _coerce(item, depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        return [_coerce(item, depth + 1) for item in items[:MAX_ITEMS]]
    # Pydantic v2 models (Observation, RequestSpec, ...).
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _coerce(model_dump(mode="json"), depth + 1)
        except Exception:
            pass
    # Dataclasses and plain objects (RetrievalResult, RouteDecision, ...).
    if hasattr(value, "__dict__"):
        public = {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
        if public:
            return _coerce(public, depth + 1)
    return _truncate(redact_text(str(value)))


def sanitize_for_trace(value: Any) -> Any:
    """Return a JSON-safe, redacted copy suitable for a LangSmith run."""
    return _coerce(value, 0)
