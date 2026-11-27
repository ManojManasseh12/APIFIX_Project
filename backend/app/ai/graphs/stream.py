"""Bridge between graph nodes and the SSE route.

Nodes call :func:`emit`; the route consumes ``graph.astream(..., stream_mode="custom")``
and forwards each event as SSE. Outside a streaming run (unit tests calling
``ainvoke``) the writer is unavailable, so emission is a no-op.
"""

from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer


def emit(event: str, data: dict[str, Any]) -> None:
    """Emit one UI event from inside a graph node. Never raises."""
    try:
        writer = get_stream_writer()
    except Exception:
        # No streaming context (ainvoke / direct node call), or a future
        # LangGraph version raised something else: emission is a side channel.
        return
    try:
        writer({"event": event, "data": data})
    except Exception:
        # A closed stream must not fail the node.
        return
