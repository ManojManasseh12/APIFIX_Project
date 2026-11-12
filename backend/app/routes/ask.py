from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.ai.prompts import build_ask_context
from app.ai.llm import get_providers, stream_with_fallback
from app.ai.prompts import (
    ASK_SYSTEM_PROMPT,
    CITATION_RE,
    TOOL_SYSTEM_PROMPT,
    build_tool_context,
)
from app.ai.router import route_question
from app.ai.rag.pipeline import retrieve_for_api
from app.ai.tools import ToolError, convert_currency, get_weather
from app.core.errors import AllProvidersFailed, StreamInterrupted
from app.core.models import AskRequest
from app.routes import sse

router = APIRouter(tags=["assistant"])


def _require_providers() -> tuple:
    providers = get_providers()
    if not providers:
        return ()
    return providers


@router.post("/ask")
async def ask(payload: AskRequest, request: Request):
    async def events():
        try:
            question = payload.question.strip()
            if not question:
                yield sse("error", {"code": "empty_question", "message": "Ask a question first."})
                return

            # ── Router: RAG for API questions, real tools for the rest ──
            decision = await route_question(question)
            if decision.route != "rag":
                tool_name = decision.route.split(":", 1)[1]
                yield sse(
                    "tool",
                    {
                        "name": tool_name,
                        "args": decision.args,
                        "via": decision.via,
                    },
                )
                try:
                    if tool_name == "weather":
                        result = await get_weather(decision.args.get("city", ""))
                        label = result.get("city", tool_name)
                    else:
                        result = await convert_currency(
                            decision.args.get("amount", 1.0),
                            decision.args.get("base", "INR"),
                            decision.args.get("target", "USD"),
                        )
                        label = f"{result['amount']} {result['base']} → {result['target']}"
                except ToolError as exc:
                    yield sse(
                        "error",
                        {"code": "tool_failed", "message": str(exc)},
                    )
                    return

                yield sse("tool_result", {"name": tool_name, "result": result})

                providers = _require_providers()
                if not providers:
                    yield sse(
                        "error",
                        {
                            "code": "no_providers_configured",
                            "message": "Set at least one provider key in .env.",
                        },
                    )
                    return

                buffer: list[str] = []
                provider_name = None
                try:
                    async for provider_name, token in stream_with_fallback(
                        providers,
                        TOOL_SYSTEM_PROMPT,
                        build_tool_context(tool_name, decision.args, result),
                    ):
                        yield sse("token", {"text": token})
                        if await request.is_disconnected():
                            return
                except AllProvidersFailed as exc:
                    # The tool already produced the facts; report them raw
                    # instead of losing the result to an LLM outage.
                    yield sse("done", {"answer": str(result), "citations": [], "provider": None, "tool": tool_name, "tool_label": label})
                    return
                except StreamInterrupted:
                    pass

                answer = "".join(buffer) or str(result)
                yield sse(
                    "done",
                    {
                        "answer": answer,
                        "citations": [],
                        "provider": provider_name,
                        "tool": tool_name,
                        "tool_label": label,
                        "tool_result": result,
                    },
                )
                return

            # ── RAG path (unchanged) ──
            try:
                retrieval = await retrieve_for_api(question, api_name=None, path="")
            except Exception as exc:
                yield sse(
                    "error",
                    {
                        "code": "retrieval_failed",
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                )
                return

            # Fewer chunks keeps Q&A cheap while still giving the model
            # enough grounded evidence (diagnosis still uses six).
            retrieval.chunks = retrieval.chunks[:4]

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
                    "insufficient": retrieval.insufficient,
                },
            )

            providers = get_providers()
            if not providers:
                yield sse(
                    "error",
                    {
                        "code": "no_providers_configured",
                        "message": "Set at least one provider key in .env.",
                    },
                )
                return

            context = build_ask_context(question, payload.history, retrieval)
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
                    providers,
                    ASK_SYSTEM_PROMPT,
                    context,
                    on_skip=on_skip,
                    restart_after_partial=True,
                ):
                    while skipped:
                        name, reason, has_next, output_started = skipped.pop(0)
                        if output_started:
                            buffer.clear()
                            yield sse("reset", {"reason": reason})
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

            answer = "".join(buffer)
            cited = set(CITATION_RE.findall(answer))
            valid_ids = {chunk["chunk_id"] for chunk in retrieval.chunks}
            invalid = cited - valid_ids

            yield sse(
                "done",
                {
                    "answer": answer,
                    "citations": sorted(cited & valid_ids),
                    "invalid_citations": sorted(invalid),
                    "provider": provider_name,
                    "insufficient": retrieval.insufficient,
                },
            )
        except Exception as exc:
            yield sse("error", {"code": type(exc).__name__, "message": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
