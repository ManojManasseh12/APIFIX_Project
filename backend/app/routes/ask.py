from fastapi import APIRouter, Depends, Request
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
from app.ai.tools import (
    ToolError,
    decode_payload,
    dns_lookup,
    format_tool_result,
    get_time,
    ip_lookup,
    convert_epoch,
)
from app.core.auth import get_current_user
from app.connectors import github, sentry
from app.connectors.github import GitHubError
from app.connectors.sentry import SentryError
from app.mcp import google
from app.mcp.google import GoogleError
from app.core.errors import AllProvidersFailed, StreamInterrupted
from app.core.models import AskRequest
from app.db import ask_chats
from app.routes import sse
from app.utils.ids import generate_uuid

router = APIRouter(tags=["assistant"])


@router.get("/ask/history")
async def ask_history(
    limit: int = 10, user: dict | None = Depends(get_current_user)
) -> dict[str, list[dict]]:
    """Recent Ask turns (newest first) for the Ask history panel."""
    user_id = user["user_id"] if user else None
    return {"turns": ask_chats.list_turns(limit=min(limit, 50), user_id=user_id)}

# Every routed tool: an executor taking the args dict, plus a label builder.
_TOOL_EXECUTORS = {
    "time": lambda args: get_time(args.get("city", "")),
    "epoch": lambda args: convert_epoch(args.get("value", "")),
    "decode": lambda args: decode_payload(args.get("payload", "")),
    "dns": lambda args: dns_lookup(args.get("host", "")),
    "ip": lambda args: ip_lookup(args.get("ip", "")),
    "github_issues": lambda args: github.list_issues(
        args.get("repo", ""), args.get("state", "open"), args.get("limit", 10)
    ),
    "github_prs": lambda args: github.list_pull_requests(
        args.get("repo", ""), args.get("state", "open"), args.get("limit", 10)
    ),
    "github_repo": lambda args: github.repo_summary(args.get("repo", "")),
    "github_repos": lambda args: github.list_repos(args.get("limit", 10)),
    "github_file": lambda args: github.read_file(
        args.get("repo", ""), args.get("path", ""), args.get("ref", "")
    ),
    "github_spec": lambda args: github.openapi_spec(
        args.get("repo", ""), args.get("path", "")
    ),
    "github_commits": lambda args: github.recent_commits(
        args.get("repo", ""), args.get("path", ""), args.get("limit", 10)
    ),
    "github_ci": lambda args: github.workflow_runs(
        args.get("repo", ""), args.get("branch", ""), args.get("limit", 5)
    ),
    "sentry_issues": lambda args: sentry.list_issues(
        args.get("query", "is:unresolved"),
        args.get("period", "14d"),
        args.get("limit", 10),
    ),
    "sentry_event": lambda args: sentry.get_issue_event(
        args.get("issue_id", "")
    ),
    "sentry_events": lambda args: sentry.search_events(
        args.get("endpoint", ""),
        args.get("query", ""),
        args.get("period", "14d"),
        args.get("limit", 5),
    ),
    "github_write": lambda args: _github_write_blocked(args),
}


def _github_write_blocked(args: dict) -> dict:
    """Write tools stay off until Ask has an explicit approval step."""
    action = (args.get("action") or "write").replace("_", " ")
    raise ToolError(
        f"GitHub {action} is not enabled yet. Ask tools are read-only for now — "
        "write actions will use the same explicit approval gate as POST/PATCH "
        "in the request tester."
    )


# Google tools are per user, so they take (user_id, args) instead of args.
_GOOGLE_EXECUTORS = {
    "gcal_events": google.list_events,
    "gcal_search": google.search_events,
    "gmail_search": google.search_threads,
}


async def _execute_tool(tool_name: str, args: dict, user_id: str | None = None) -> dict:
    """Run one routed tool; raises ToolError on a clean, explainable failure."""
    if tool_name == "google_write":
        action = args.get("action") or "write"
        raise ToolError(
            f"Google Calendar/Gmail {action} actions are not enabled yet. Ask "
            "tools are read-only for now — write actions will use the same "
            "explicit approval gate as POST/PATCH in the request tester."
        )

    google_executor = _GOOGLE_EXECUTORS.get(tool_name)
    if google_executor is not None:
        if not user_id:
            raise ToolError("Sign in to use Google Calendar or Gmail.")
        try:
            return await google_executor(user_id, args)
        except GoogleError as exc:
            raise ToolError(str(exc)) from exc

    executor = _TOOL_EXECUTORS.get(tool_name)
    if executor is None:
        raise ToolError(f"Unknown tool '{tool_name}'.")
    try:
        result = executor(args)
        if hasattr(result, "__await__"):
            result = await result
    except (GitHubError, SentryError) as exc:
        # The connector's messages are already user-facing and actionable.
        raise ToolError(str(exc)) from exc
    return result


def _count(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _tool_label(tool_name: str, result: dict) -> str:
    """Short chip text for the UI, built from the tool's own result."""
    if tool_name == "time":
        return f"{result.get('time_12h')} · {result.get('city')}"
    if tool_name == "epoch":
        return f"{result.get('epoch')} → {result.get('date_utc')}"
    if tool_name == "decode":
        return f"{result.get('kind')} decoded"
    if tool_name == "dns":
        return f"{result.get('host')} → {len(result.get('addresses', []))} A record(s)"
    if tool_name == "ip":
        return f"{result.get('ip')} · {result.get('country') or '?'}"
    if tool_name == "github_issues":
        return (
            f"GitHub · {result.get('repo')} · "
            f"{_count(int(result.get('count', 0)), result.get('state', 'open') + ' issue')}"
        )
    if tool_name == "github_prs":
        return (
            f"GitHub · {result.get('repo')} · "
            f"{_count(int(result.get('count', 0)), result.get('state', 'open') + ' PR')}"
        )
    if tool_name == "github_repo":
        return f"GitHub · {result.get('repo')} · ★ {result.get('stars')}"
    if tool_name == "github_repos":
        return f"GitHub · {_count(int(result.get('count', 0)), 'repository')}"
    if tool_name == "github_file":
        if result.get("kind") == "directory":
            return (
                f"GitHub · {result.get('path')}/ · "
                f"{_count(int(result.get('count', 0)), 'entry')}"
            )
        suffix = " (truncated)" if result.get("truncated") else ""
        return (
            f"GitHub · {result.get('path')} · "
            f"{_count(int(result.get('lines') or 0), 'line')}{suffix}"
        )
    if tool_name == "github_spec":
        return (
            f"GitHub · {result.get('path')} · "
            f"{_count(int(result.get('count', 0)), 'endpoint')}"
        )
    if tool_name == "github_commits":
        where = f" · {result['path']}" if result.get("path") else ""
        return (
            f"GitHub · {_count(int(result.get('count', 0)), 'commit')}{where}"
        )
    if tool_name == "github_ci":
        where = f" · {result['branch']}" if result.get("branch") else ""
        return (
            f"GitHub · {_count(int(result.get('count', 0)), 'run')}{where} · "
            f"{int(result.get('failed') or 0)} failed"
        )
    if tool_name == "sentry_issues":
        return (
            f"Sentry · {_count(int(result.get('count', 0)), 'issue')} · "
            f"{result.get('project')}"
        )
    if tool_name == "sentry_event":
        issue = result.get("issue") or {}
        event = result.get("event") or {}
        kind = (event.get("exception") or {}).get("type") or "event"
        return f"Sentry · {issue.get('short_id') or issue.get('id')} · {kind}"
    if tool_name == "sentry_events":
        where = f" · {result['endpoint']}" if result.get("endpoint") else ""
        return (
            f"Sentry · {_count(int(result.get('count', 0)), 'event')}{where}"
        )
    if tool_name in ("gcal_events", "gcal_search"):
        when = {
            "today": "today",
            "tomorrow": "tomorrow",
            "next_week": "next week",
            "month": "next 30 days",
        }.get(str(result.get("range")), "this week")
        extra = f' · “{result["query"]}”' if result.get("query") else ""
        return f"Google Calendar · {_count(int(result.get('count', 0)), 'event')} · {when}{extra}"
    if tool_name == "gmail_search":
        return (
            f"Gmail · {_count(int(result.get('count', 0)), 'thread')} · "
            f"{result.get('query')}"
        )
    return tool_name


def _tool_preview_label(tool_name: str, args: dict) -> str:
    """Chip text emitted before the tool runs, from its arguments alone."""
    if tool_name == "time":
        return f"clock · {args.get('city') or 'lookup'}"
    if tool_name == "epoch":
        return f"timestamp · {args.get('value') or '?'}"
    if tool_name == "decode":
        return "decoder · JWT/base64/URL"
    if tool_name == "dns":
        return f"dns · {args.get('host') or 'lookup'}"
    if tool_name == "ip":
        return f"ip · {args.get('ip') or '?'}"
    if tool_name == "github_issues":
        return f"github · issues · {args.get('repo') or 'default repo'}"
    if tool_name == "github_prs":
        return f"github · pull requests · {args.get('repo') or 'default repo'}"
    if tool_name == "github_repo":
        return f"github · {args.get('repo') or 'repository'}"
    if tool_name == "github_repos":
        return "github · repositories"
    if tool_name == "github_file":
        return f"github · {args.get('path') or 'file'}"
    if tool_name == "github_spec":
        return f"github · {args.get('path') or 'openapi spec'}"
    if tool_name == "github_commits":
        return f"github · {args.get('path') or args.get('repo') or 'default repo'}"
    if tool_name == "github_ci":
        return f"github · {args.get('branch') or args.get('repo') or 'default repo'}"
    if tool_name == "sentry_issues":
        return f"sentry · issues · {args.get('period') or '14d'}"
    if tool_name == "sentry_event":
        return f"sentry · issue {args.get('issue_id') or '?'}"
    if tool_name == "sentry_events":
        return f"sentry · {args.get('endpoint') or 'events'}"
    if tool_name == "github_write":
        return f"github · {str(args.get('action') or 'write').replace('_', ' ')}"
    if tool_name == "gcal_events":
        return f"google calendar · {str(args.get('range') or 'week').replace('_', ' ')}"
    if tool_name == "gcal_search":
        return f"google calendar · “{args.get('query') or ''}”"
    if tool_name == "gmail_search":
        return f"gmail · {args.get('query') or 'in:inbox'}"
    if tool_name == "google_write":
        return f"google · {args.get('action') or 'write'}"
    return tool_name


def _require_providers() -> tuple:
    providers = get_providers()
    if not providers:
        return ()
    return providers


@router.post("/ask")
async def ask(
    payload: AskRequest, request: Request, user: dict | None = Depends(get_current_user)
):
    user_id = user["user_id"] if user else None

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
                        "label": _tool_preview_label(tool_name, decision.args),
                        "args": decision.args,
                        "via": decision.via,
                    },
                )
                try:
                    result = await _execute_tool(tool_name, decision.args, user_id)
                except ToolError as exc:
                    yield sse(
                        "error",
                        {"code": "tool_failed", "message": str(exc)},
                    )
                    return
                label = _tool_label(tool_name, result)

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
                    # The tool already produced the facts; present them
                    # readably instead of losing them to an LLM outage.
                    answer = format_tool_result(tool_name, result)
                    ask_chats.save(
                        {
                            "turn_id": generate_uuid(),
                            "user_id": user_id,
                            "session_id": payload.session_id,
                            "question": question,
                            "answer": answer,
                            "provider": None,
                            "tool": tool_name,
                            "tool_label": label,
                            "citations": [],
                        }
                    )
                    yield sse(
                        "done",
                        {
                            "answer": answer,
                            "citations": [],
                            "provider": None,
                            "tool": tool_name,
                            "tool_label": label,
                        },
                    )
                    return
                except StreamInterrupted:
                    pass

                answer = "".join(buffer) or format_tool_result(tool_name, result)
                ask_chats.save(
                    {
                        "turn_id": generate_uuid(),
                        "user_id": user_id,
                        "session_id": payload.session_id,
                        "question": question,
                        "answer": answer,
                        "provider": provider_name,
                        "tool": tool_name,
                        "tool_label": label,
                        "citations": [],
                    }
                )
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

            # Persist the turn (same history store as diagnosis runs). The
            # citations saved are the full chunk metadata so the history
            # panel can restore the source chips exactly.
            saved_citations = [
                {
                    key: chunk.get(key)
                    for key in ("chunk_id", "source_title", "source_url", "section", "page")
                }
                for chunk in retrieval.chunks
                if chunk["chunk_id"] in cited
            ]
            ask_chats.save(
                {
                    "turn_id": generate_uuid(),
                    "user_id": user_id,
                    "session_id": payload.session_id,
                    "question": question,
                    "answer": answer,
                    "provider": provider_name,
                    "tool": None,
                    "tool_label": None,
                    "citations": saved_citations,
                }
            )

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
