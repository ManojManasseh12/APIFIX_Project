import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.sse import sse
from app.core.errors import AllProvidersFailed, ApprovalRequired, StreamInterrupted
from app.db import runs
from app.models import RequestSpec
from app.security.approval import require_approval
from app.services.context import build_context
from app.services.llm import get_providers, stream_with_fallback
from app.services.prompts import CITATION_RE, PATCH_RE, SYSTEM_PROMPT
from app.services.query import build_query
from app.services.retriever import get_retriever
from app.services.tester import execute

router = APIRouter()


@router.post("/diagnose")
async def diagnose(spec: RequestSpec, request: Request):
    run_id = str(uuid.uuid4())

    async def events():
        try:
            try:
                require_approval(spec)
            except ApprovalRequired as exc:
                yield sse("error", {"code": "approval_required", "message": str(exc)})
                return

            observation = await execute(spec)
            yield sse("observation", observation.model_dump(mode="json"))

            try:
                retrieval = get_retriever().search(
                    build_query(spec, observation), api_name=None, path=str(spec.url)
                )
            except Exception as exc:
                yield sse(
                    "error",
                    {"code": "retrieval_failed", "message": f"{type(exc).__name__}: {exc}"},
                )
                return

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
                yield sse(
                    "done",
                    {
                        "run_id": run_id,
                        "diagnosis": text,
                        "citations": [],
                        "invalid_citations": [],
                        "patch": None,
                        "confidence": "low",
                    },
                )
                return

            if retrieval.insufficient:
                text = (
                    "Insufficient evidence: no retrieved documentation supports a "
                    "specific cause. Add the API's versioned documentation or share the "
                    "error code, then retest."
                )
                yield sse(
                    "done",
                    {
                        "run_id": run_id,
                        "diagnosis": text,
                        "citations": [],
                        "invalid_citations": [],
                        "patch": None,
                        "confidence": "low",
                    },
                )
                return

            providers = get_providers()
            if not providers:
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
            skipped: list[tuple[str, str, bool, bool]] = []

            def on_skip(
                name: str, reason: str, has_next: bool, output_started: bool
            ) -> None:
                skipped.append((name, reason, has_next, output_started))

            try:
                async for provider_name, token in stream_with_fallback(
                    providers, SYSTEM_PROMPT, context, on_skip=on_skip
                ):
                    while skipped:
                        name, reason, has_next, output_started = skipped.pop(0)
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
                yield sse("error", {"code": "all_providers_failed", "message": str(exc)})
                return
            except StreamInterrupted as exc:
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

            runs.save(
                {
                    "run_id": run_id,
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
                },
            )
        except Exception as exc:
            yield sse("error", {"code": type(exc).__name__, "message": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
