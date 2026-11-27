import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.ai.prompts import build_context
from app.ai.llm import get_providers, stream_with_fallback
from app.ai.prompts import CITATION_RE, PATCH_RE, SYSTEM_PROMPT
from app.ai.rag.pipeline import retrieve_for_api
from app.ai.rag.query import build_query
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.errors import AllProvidersFailed, ApprovalRequired, StreamInterrupted
from app.core.models import RequestSpec
from app.core.security import require_approval
from app.core.tester import execute
from app.db import runs
from app.observability import (
    current_trace_id,
    hash_user_id,
    sanitize_url,
    traced_stream,
)
from app.routes import sse
from app.utils.ids import generate_uuid

router = APIRouter(tags=["diagnosis"])


@router.post("/diagnose")
async def diagnose(
    spec: RequestSpec, request: Request, user: dict | None = Depends(get_current_user)
):
    run_id = generate_uuid()
    user_id = user["user_id"] if user else None
    trace_summary: dict[str, object] = {}

    async def legacy_events():
        try:
            try:
                require_approval(spec)
            except ApprovalRequired as exc:
                trace_summary["error"] = "approval_required"
                yield sse("error", {"code": "approval_required", "message": str(exc)})
                return

            observation = await execute(spec)
            trace_summary.update(
                {
                    "status": observation.status,
                    "error_type": observation.error_type,
                    "latency_ms": observation.latency_ms,
                }
            )
            yield sse("observation", observation.model_dump(mode="json"))

            try:
                retrieval = await retrieve_for_api(
                    build_query(spec, observation),
                    api_name=None,
                    path=str(spec.url),
                )
            except Exception as exc:
                trace_summary["error"] = "retrieval_failed"
                yield sse(
                    "error",
                    {"code": "retrieval_failed", "message": f"{type(exc).__name__}: {exc}"},
                )
                return

            trace_summary.update(
                {
                    "insufficient": retrieval.insufficient,
                    "version_mismatch": retrieval.version_mismatch,
                    "retrieved_chunk_ids": [
                        chunk.get("chunk_id") for chunk in retrieval.chunks
                    ],
                }
            )

            yield sse(
                "retrieval",
                {
                    "chunks": [
                        {
                            key: chunk.get(key)
                            for key in (
                                "chunk_id",
                                "source_title",
                                "source_url",
                                "section",
                                "page",
                            )
                        }
                        for chunk in retrieval.chunks
                    ],
                    "version_mismatch": retrieval.version_mismatch,
                    "other_versions": retrieval.other_versions,
                    "insufficient": retrieval.insufficient,
                },
            )

            if retrieval.version_mismatch:
                text = (
                    "Version mismatch: the documentation index has no chunks for this "
                    f"API version. Known versions: {', '.join(retrieval.other_versions) or 'unknown'}. "
                    "Add the matching versioned documentation before diagnosing."
                )
                trace_summary["outcome"] = "version_mismatch"
                yield sse(
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
                return

            if retrieval.insufficient:
                text = (
                    "Insufficient evidence: no retrieved documentation supports a "
                    "specific cause. Add the API's versioned documentation or share the "
                    "error code, then retest."
                )
                trace_summary["outcome"] = "insufficient_evidence"
                yield sse(
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
                return

            providers = get_providers()
            if not providers:
                trace_summary["error"] = "no_providers_configured"
                yield sse(
                    "error",
                    {
                        "code": "no_providers_configured",
                        "message": "Set at least one provider key and model in .env.",
                    },
                )
                return

            context = build_context(spec, observation, retrieval)
            buffer: list[str] = []
            provider_name = None
            active_provider = None
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
                            yield sse(
                                "provider_fallback",
                                {"from": name, "reason": reason},
                            )
                    if provider_name != active_provider:
                        active_provider = provider_name
                        yield sse("provider", {"name": provider_name})
                    if await request.is_disconnected():
                        return
                    buffer.append(token)
                    yield sse("token", {"text": token})
            except AllProvidersFailed as exc:
                trace_summary["error"] = "all_providers_failed"
                yield sse("error", {"code": "all_providers_failed", "message": str(exc)})
                return
            except StreamInterrupted as exc:
                trace_summary["error"] = "stream_interrupted"
                yield sse("error", {"code": "stream_interrupted", "message": str(exc)})
                return

            text = "".join(buffer)
            cited = set(CITATION_RE.findall(text))
            valid_ids = {chunk["chunk_id"] for chunk in retrieval.chunks}
            invalid = cited - valid_ids

            patch = None
            match = PATCH_RE.search(text)
            if match:
                try:
                    patch = json.loads(match.group(1)).get("patch")
                except Exception:
                    patch = None

            trace_summary.update(
                {
                    "outcome": "diagnosed",
                    "provider": provider_name,
                    "answer_length": len(text),
                    "citations": sorted(cited & valid_ids),
                    "invalid_citations": sorted(invalid),
                    "patch": patch is not None,
                }
            )

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

            yield sse(
                "done",
                {
                    "run_id": run_id,
                    "diagnosis": text,
                    "citations": sorted(cited & valid_ids),
                    "invalid_citations": sorted(invalid),
                    "patch": patch,
                    "provider": provider_name,
                    "trace_id": current_trace_id(),
                },
            )
        except Exception as exc:
            trace_summary["error"] = type(exc).__name__
            yield sse("error", {"code": type(exc).__name__, "message": str(exc)})

    async def graph_events():
        # Imported lazily so the legacy path never loads LangGraph.
        from app.ai.graphs import get_diagnose_graph

        graph = get_diagnose_graph()
        state = {
            "spec": spec.model_dump(mode="json"),
            "run_id": run_id,
            "user_id": user_id,
            "retrieval_attempts": 0,
            "diagnosis_attempts": 0,
            "trace": {},
        }
        config = {"recursion_limit": settings.langgraph_recursion_limit}
        try:
            async for item in graph.astream(
                state, config=config, stream_mode="custom"
            ):
                if not isinstance(item, dict):
                    continue
                event = item.get("event")
                data = item.get("data") or {}
                if event == "trace":
                    trace_summary.update(data)
                    continue
                if not isinstance(event, str):
                    continue
                if await request.is_disconnected():
                    return
                yield sse(event, data)
        except Exception as exc:
            trace_summary["error"] = type(exc).__name__
            yield sse("error", {"code": type(exc).__name__, "message": str(exc)})

    async def events():
        if settings.use_langgraph_diagnose:
            async for chunk in graph_events():
                yield chunk
            return
        async for chunk in legacy_events():
            yield chunk

    return StreamingResponse(
        traced_stream(
            "diagnose",
            events(),
            inputs={
                "method": spec.method,
                "url": sanitize_url(str(spec.url)),
                "has_body": bool(spec.body),
            },
            tags=["diagnose"],
            metadata={
                "user_id_hash": hash_user_id(user_id),
                "run_id": run_id,
            },
            summary=trace_summary,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
