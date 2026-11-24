"""Thin, optional wrapper around LangSmith.

Design goals:
- **Off by default.** No key, no tracing, no network.
- **Never break a request.** Any tracing failure falls back to running the
  original function/generator untouched.
- **One root run per user request.** ``traced_stream`` wraps the SSE
  generators, because FastAPI returns the ``StreamingResponse`` before the
  work actually runs. Child spans attach through ``tracing_context``.
- **No token floods.** Generator spans use ``reduce_fn`` or the caller's
  summary dict instead of logging every streamed token.
"""

from __future__ import annotations

import logging
import random
from typing import Any, AsyncIterator, Callable, TypeVar

from app.core.config import settings
from app.observability.sanitize import sanitize_for_trace

logger = logging.getLogger(__name__)

T = TypeVar("T")

_CLIENT: Any | None = None


def langsmith_enabled() -> bool:
    """True only when tracing is explicitly on and a key is present."""
    return bool(
        getattr(settings, "langsmith_tracing", False)
        and (getattr(settings, "langsmith_api_key", "") or "").strip()
    )


def project_name() -> str:
    return (
        (getattr(settings, "langsmith_project", "") or "").strip()
        or "apifix-ai"
    )


def _sampled_in() -> bool:
    """Honour LANGSMITH_SAMPLE_RATE for streaming roots."""
    try:
        rate = float(getattr(settings, "langsmith_sample_rate", 1.0))
    except (TypeError, ValueError):
        return True
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate


def get_client() -> Any | None:
    """Lazy singleton LangSmith client (sync; the SDK batches in a thread)."""
    global _CLIENT
    if not langsmith_enabled():
        return None
    if _CLIENT is None:
        from langsmith import Client

        kwargs: dict[str, Any] = {
            "api_key": settings.langsmith_api_key.strip(),
        }
        endpoint = (getattr(settings, "langsmith_endpoint", "") or "").strip()
        if endpoint:
            kwargs["api_url"] = endpoint
        _CLIENT = Client(**kwargs)
    return _CLIENT


def reset_client() -> None:
    """Drop the cached client (tests / credential rotation)."""
    global _CLIENT
    _CLIENT = None


def flush_traces() -> None:
    """Send any queued runs. Safe to call when tracing is off."""
    client = _CLIENT
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        logger.warning("Could not flush LangSmith traces", exc_info=True)


def shutdown_tracing() -> None:
    """Lifespan-shutdown hook: flush and release the client."""
    flush_traces()
    reset_client()


def current_trace_id() -> str | None:
    """The active trace id, for attaching feedback later. None when off."""
    if not langsmith_enabled():
        return None
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        trace_id = getattr(run, "trace_id", None) if run is not None else None
        return str(trace_id) if trace_id else None
    except Exception:
        return None


def traced(
    name: str,
    run_type: str = "chain",
    *,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    process_inputs: Callable[[dict], dict] | None = None,
    process_outputs: Callable[[Any], Any] | None = None,
    reduce_fn: Callable[[list[Any]], Any] | None = None,
):
    """Decorator factory. Becomes a no-op when tracing is disabled."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        if not langsmith_enabled():
            return func
        try:
            from langsmith import traceable

            kwargs: dict[str, Any] = {
                "name": name,
                "run_type": run_type,
                "tags": tags,
                "metadata": sanitize_for_trace(metadata or {}),
                "process_inputs": process_inputs or sanitize_for_trace,
                "process_outputs": process_outputs or sanitize_for_trace,
            }
            if reduce_fn is not None:
                kwargs["reduce_fn"] = reduce_fn
            return traceable(func, **kwargs)
        except Exception:
            logger.warning(
                "LangSmith unavailable; %s will run untraced", name, exc_info=True
            )
            return func

    return decorator


def trace_call(
    name: str,
    func: Callable[..., T],
    *,
    run_type: str = "chain",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    process_inputs: Callable[[dict], dict] | None = None,
    process_outputs: Callable[[Any], Any] | None = None,
) -> Callable[..., T]:
    """Wrap one callable at run time (for dynamic span names such as tools)."""
    if not langsmith_enabled():
        return func
    try:
        from langsmith import traceable

        return traceable(
            func,
            name=name,
            run_type=run_type,
            tags=tags,
            metadata=sanitize_for_trace(metadata or {}),
            process_inputs=process_inputs or sanitize_for_trace,
            process_outputs=process_outputs or sanitize_for_trace,
        )
    except Exception:
        logger.warning(
            "LangSmith unavailable; %s will run untraced", name, exc_info=True
        )
        return func


def _start_run(
    name: str,
    *,
    inputs: dict[str, Any] | None,
    tags: list[str] | None,
    metadata: dict[str, Any] | None,
):
    from langsmith.run_trees import RunTree

    client = get_client()
    if client is None:
        return None, None
    run = RunTree(
        name=name,
        run_type="chain",
        inputs=sanitize_for_trace(inputs or {}),
        tags=list(tags or []),
        extra={"metadata": sanitize_for_trace(metadata or {})},
        ls_client=client,
        session_name=project_name(),
    )
    run.post()
    return run, client


async def traced_stream(
    name: str,
    source: AsyncIterator[str],
    *,
    inputs: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Yield ``source`` inside one root run, ending it on every exit path.

    ``summary`` is a mutable dict the caller updates while streaming (route,
    tool, provider, citations, errors). It becomes the run's outputs, so the
    trace stays useful without logging every SSE token.
    """
    if not langsmith_enabled():
        async for item in source:
            yield item
        return

    if not _sampled_in():
        # Disable the tracing context too, otherwise child @traced spans would
        # create their own root runs for a request we meant to sample out.
        from langsmith.run_helpers import tracing_context

        with tracing_context(enabled=False):
            async for item in source:
                yield item
        return

    try:
        run, client = _start_run(
            name, inputs=inputs, tags=tags, metadata=metadata
        )
    except Exception:
        logger.warning(
            "Could not start LangSmith trace %s; continuing untraced",
            name,
            exc_info=True,
        )
        async for item in source:
            yield item
        return

    if run is None or client is None:
        async for item in source:
            yield item
        return

    from langsmith.run_helpers import tracing_context

    finished = False
    try:
        with tracing_context(
            parent=run,
            client=client,
            project_name=project_name(),
            tags=list(tags or []),
            metadata=sanitize_for_trace(metadata or {}),
            enabled=True,
        ):
            async for item in source:
                yield item
        finished = True
    except GeneratorExit:
        run.end(outputs={"cancelled": True})
        run.patch()
        raise
    except asyncio.CancelledError:
        run.end(error="cancelled")
        run.patch()
        raise
    except Exception as exc:
        run.end(error=f"{type(exc).__name__}: {exc}")
        run.patch()
        raise
    finally:
        if finished:
            run.end(outputs=sanitize_for_trace(summary or {"ok": True}))
            run.patch()
