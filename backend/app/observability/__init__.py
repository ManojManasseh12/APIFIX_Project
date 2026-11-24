"""LangSmith tracing, sanitization, and lifecycle helpers.

Tracing is **off by default**. It only activates when ``LANGSMITH_TRACING=true``
and ``LANGSMITH_API_KEY`` is set, so local development and the test suite never
send data to a third party.

The rest of the app imports from this package instead of importing
``langsmith`` directly. That keeps the optional dependency isolated and gives
one place to redact secrets before a trace leaves the server.
"""

from app.observability.langsmith import (
    current_trace_id,
    flush_traces,
    get_client,
    langsmith_enabled,
    project_name,
    reset_client,
    shutdown_tracing,
    trace_call,
    traced,
    traced_stream,
)
from app.observability.sanitize import (
    hash_user_id,
    sanitize_for_trace,
    sanitize_url,
)

__all__ = [
    "current_trace_id",
    "flush_traces",
    "get_client",
    "hash_user_id",
    "langsmith_enabled",
    "project_name",
    "reset_client",
    "sanitize_for_trace",
    "sanitize_url",
    "shutdown_tracing",
    "trace_call",
    "traced",
    "traced_stream",
]
