"""Diagnose flow as a LangGraph state machine.

Nodes wrap the existing pipeline functions; the route translates emitted
events into the same SSE stream the UI already consumes. Loop caps:

- retrieval: at most 2 attempts (the second drops the version hint)
- diagnosis: at most 2 generations (one citation repair)
"""

from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph

from app.ai.graphs.state import (
    DiagnoseState,
    observation_from_state,
    retrieval_from_state,
    spec_from_state,
)
from app.ai.graphs.stream import emit
from app.ai.llm import get_providers, stream_with_fallback
from app.ai.prompts import CITATION_RE, PATCH_RE, SYSTEM_PROMPT, build_context
from app.ai.rag.pipeline import retrieve_for_api
from app.ai.rag.query import build_query
from app.core.errors import AllProvidersFailed, ApprovalRequired, StreamInterrupted
from app.core.security import require_approval
from app.core.tester import execute
from app.db import runs
from app.observability import current_trace_id

MAX_RETRIEVALS = 2
MAX_DIAGNOSES = 2


# ── helpers ───────────────────────────────────────────────────────────────────


def build_retry_query(spec, observation) -> str:
    """A broader query for the second retrieval attempt.

    The first attempt uses the full ``build_query`` (including the preset
    question). If that returns insufficient evidence, retry without the
    version filter and with documentation intent instead.
    """
    parts = [spec.method, urlparse(str(spec.url)).path]
    if observation.status:
        parts.append(f"HTTP {observation.status}")
    if observation.error_type:
        parts.append(observation.error_type.replace("_", " "))
    parts.append("API documentation")
    return " ".join(part for part in parts if part)


def repair_note(chunks: list[dict[str, Any]]) -> str:
    ids = ", ".join(chunk["chunk_id"] for chunk in chunks)
    return (
        "\n\nCITATION REPAIR: your previous answer cited unknown ids. "
        f"Use only these ids: {ids}. Remove any other citation."
    )


def _retrieval_event(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("chunk_id", "source_title", "source_url", "section", "page")
    return [{key: chunk.get(key) for key in keys} for chunk in chunks]


# ── nodes ─────────────────────────────────────────────────────────────────────


async def approve_node(state: DiagnoseState) -> dict[str, Any]:
    spec = spec_from_state(state)
    try:
        require_approval(spec)
    except ApprovalRequired as exc:
        return {
            "approved": False,
            "outcome": "approval_required",
            "error_code": "approval_required",
            "error_message": str(exc),
        }
    return {"approved": True}


async def observe_node(state: DiagnoseState) -> dict[str, Any]:
    spec = spec_from_state(state)
    try:
        observation = await execute(spec)
    except Exception as exc:
        return {"error_code": type(exc).__name__, "error_message": str(exc)}

    payload = observation.model_dump(mode="json")
    emit("observation", payload)
    trace = {
        **state.get("trace", {}),
        "status": observation.status,
        "error_type": observation.error_type,
        "latency_ms": observation.latency_ms,
    }
    return {"observation": payload, "trace": trace}


async def retrieve_node(state: DiagnoseState) -> dict[str, Any]:
    spec = spec_from_state(state)
    observation = observation_from_state(state)
    attempt = state.get("retrieval_attempts", 0) + 1

    if attempt <= 1:
        query = build_query(spec, observation)
        path = str(spec.url)
    else:
        query = build_retry_query(spec, observation)
        # Empty path disables the version-mismatch guard for the retry.
        path = ""

    try:
        retrieval = await retrieve_for_api(query, api_name=None, path=path)
    except Exception as exc:
        return {
            "error_code": "retrieval_failed",
            "error_message": f"{type(exc).__name__}: {exc}",
        }

    chunks = list(retrieval.chunks)
    emit(
        "retrieval",
        {
            "chunks": _retrieval_event(chunks),
            "version_mismatch": retrieval.version_mismatch,
            "other_versions": retrieval.other_versions,
            "insufficient": retrieval.insufficient,
        },
    )
    trace = {
        **state.get("trace", {}),
        "insufficient": retrieval.insufficient,
        "version_mismatch": retrieval.version_mismatch,
        "retrieved_chunk_ids": [chunk.get("chunk_id") for chunk in chunks],
    }
    return {
        "query": query,
        "chunks": chunks,
        "insufficient": retrieval.insufficient,
        "version_mismatch": retrieval.version_mismatch,
        "other_versions": retrieval.other_versions,
        "retrieval_attempts": attempt,
        "trace": trace,
    }


async def diagnose_node(state: DiagnoseState) -> dict[str, Any]:
    spec = spec_from_state(state)
    observation = observation_from_state(state)
    retrieval = retrieval_from_state(state)

    providers = get_providers()
    if not providers:
        return {
            "error_code": "no_providers_configured",
            "error_message": "Set at least one provider key and model in .env.",
        }

    context = build_context(spec, observation, retrieval)
    if state.get("repair"):
        context += repair_note(state.get("chunks", []))

    buffer: list[str] = []
    provider_name: str | None = None
    active_provider: str | None = None
    skipped: list[tuple[str, str, bool]] = []

    def on_skip(
        name: str, reason: str, has_next: bool, _output_started: bool
    ) -> None:
        skipped.append((name, reason, has_next))

    try:
        async for provider_name, token in stream_with_fallback(
            providers, SYSTEM_PROMPT, context, on_skip=on_skip
        ):
            while skipped:
                name, reason, has_next = skipped.pop(0)
                if has_next:
                    emit("provider_fallback", {"from": name, "reason": reason})
            if provider_name != active_provider:
                active_provider = provider_name
                emit("provider", {"name": provider_name})
            buffer.append(token)
            emit("token", {"text": token})
    except AllProvidersFailed as exc:
        return {"error_code": "all_providers_failed", "error_message": str(exc)}
    except StreamInterrupted as exc:
        return {"error_code": "stream_interrupted", "error_message": str(exc)}

    return {
        "diagnosis": "".join(buffer),
        "provider": provider_name,
        "diagnosis_attempts": state.get("diagnosis_attempts", 0) + 1,
    }


def validate_node(state: DiagnoseState) -> dict[str, Any]:
    text = state.get("diagnosis", "")
    chunks = state.get("chunks", [])
    cited = set(CITATION_RE.findall(text))
    valid_ids = {chunk["chunk_id"] for chunk in chunks}
    invalid = cited - valid_ids

    patch = None
    match = PATCH_RE.search(text)
    if match:
        try:
            patch = json.loads(match.group(1)).get("patch")
        except Exception:
            patch = None

    attempts = state.get("diagnosis_attempts", 0)
    return {
        "citations": sorted(cited & valid_ids),
        "invalid_citations": sorted(invalid),
        "patch": patch,
        "repair": bool(invalid and chunks and attempts < MAX_DIAGNOSES),
    }


def save_node(state: DiagnoseState) -> dict[str, Any]:
    spec = spec_from_state(state)
    observation = observation_from_state(state)
    chunks = state.get("chunks", [])
    valid_ids = {chunk["chunk_id"] for chunk in chunks}
    text = state.get("diagnosis", "")
    patch = state.get("patch")
    provider_name = state.get("provider")
    run_id = state.get("run_id", "")
    user_id = state.get("user_id")

    runs.save(
        {
            "run_id": run_id,
            "user_id": user_id,
            "method": spec.method,
            "url": str(spec.url),
            "status": observation.status,
            "error_type": observation.error_type,
            "retrieved_ids": sorted(valid_ids),
            "provider": provider_name,
            "diagnosis": text,
            "patch": patch,
        }
    )

    citations = state.get("citations", [])
    invalid_citations = state.get("invalid_citations", [])
    emit(
        "done",
        {
            "run_id": run_id,
            "diagnosis": text,
            "citations": citations,
            "invalid_citations": invalid_citations,
            "patch": patch,
            "provider": provider_name,
            "trace_id": current_trace_id(),
        },
    )
    trace = {
        **state.get("trace", {}),
        "outcome": "diagnosed",
        "provider": provider_name,
        "answer_length": len(text),
        "citations": citations,
        "invalid_citations": invalid_citations,
        "patch": patch is not None,
    }
    emit("trace", trace)
    return {"outcome": "diagnosed", "trace": trace}


def low_confidence_node(state: DiagnoseState) -> dict[str, Any]:
    run_id = state.get("run_id", "")
    if state.get("version_mismatch"):
        outcome = "version_mismatch"
        text = (
            "Version mismatch: the documentation index has no chunks for this "
            "API version. Known versions: "
            f"{', '.join(state.get('other_versions', [])) or 'unknown'}. "
            "Add the matching versioned documentation before diagnosing."
        )
    else:
        outcome = "insufficient_evidence"
        text = (
            "Insufficient evidence: no retrieved documentation supports a "
            "specific cause. Add the API's versioned documentation or share the "
            "error code, then retest."
        )

    emit(
        "done",
        {
            "run_id": run_id,
            "diagnosis": text,
            "citations": [],
            "invalid_citations": [],
            "patch": None,
            "confidence": "low",
            "trace_id": current_trace_id(),
        },
    )
    trace = {
        **state.get("trace", {}),
        "outcome": outcome,
    }
    emit("trace", trace)
    return {"outcome": outcome, "trace": trace}


def error_node(state: DiagnoseState) -> dict[str, Any]:
    code = state.get("error_code") or "error"
    message = state.get("error_message") or "Diagnosis failed."
    emit("error", {"code": code, "message": message})
    trace = {**state.get("trace", {}), "error": code}
    emit("trace", trace)
    return {"outcome": "error", "trace": trace}


# ── conditional edges ─────────────────────────────────────────────────────────


def after_approve(state: DiagnoseState) -> Literal["ok", "blocked"]:
    return "ok" if state.get("approved") else "blocked"


def after_observe(state: DiagnoseState) -> Literal["ok", "error"]:
    return "error" if state.get("error_code") else "ok"


def after_retrieve(
    state: DiagnoseState,
) -> Literal["ok", "retry", "version_mismatch", "insufficient", "error"]:
    if state.get("error_code"):
        return "error"
    if state.get("version_mismatch"):
        return "version_mismatch"
    if state.get("insufficient"):
        if state.get("retrieval_attempts", 0) < MAX_RETRIEVALS:
            return "retry"
        return "insufficient"
    return "ok"


def after_diagnose(state: DiagnoseState) -> Literal["ok", "error"]:
    return "error" if state.get("error_code") else "ok"


def after_validate(state: DiagnoseState) -> Literal["ok", "repair"]:
    return "repair" if state.get("repair") else "ok"


# ── graph ─────────────────────────────────────────────────────────────────────


def build_diagnose_graph():
    builder = StateGraph(DiagnoseState)

    builder.add_node("approve", approve_node)
    builder.add_node("observe", observe_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("diagnose", diagnose_node)
    builder.add_node("validate", validate_node)
    builder.add_node("save", save_node)
    builder.add_node("low_confidence", low_confidence_node)
    builder.add_node("error", error_node)

    builder.add_edge(START, "approve")
    builder.add_conditional_edges(
        "approve", after_approve, {"ok": "observe", "blocked": "error"}
    )
    builder.add_conditional_edges(
        "observe", after_observe, {"ok": "retrieve", "error": "error"}
    )
    builder.add_conditional_edges(
        "retrieve",
        after_retrieve,
        {
            "ok": "diagnose",
            "retry": "retrieve",
            "version_mismatch": "low_confidence",
            "insufficient": "low_confidence",
            "error": "error",
        },
    )
    builder.add_conditional_edges(
        "diagnose", after_diagnose, {"ok": "validate", "error": "error"}
    )
    builder.add_conditional_edges(
        "validate", after_validate, {"ok": "save", "repair": "diagnose"}
    )
    builder.add_edge("save", END)
    builder.add_edge("low_confidence", END)
    builder.add_edge("error", END)
    return builder.compile()


_GRAPH = None


def get_diagnose_graph():
    """Compile once; nodes are stateless, so the graph is safe to reuse."""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_diagnose_graph()
    return _GRAPH
