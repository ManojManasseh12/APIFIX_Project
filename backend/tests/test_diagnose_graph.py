"""Tests for the opt-in LangGraph /api/diagnose flow.

Two layers are covered:

1. Graph nodes/edges: loops, caps, routing, event emission, save behaviour.
2. Route dispatcher: the flag picks legacy vs graph and both produce the same
   SSE contract the frontend already consumes.

Everything is offline: the tester, retrieval, providers and the run store are
monkeypatched. No network, no real LLM, no LangSmith.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx
import pytest

import app.ai.graphs as graphs_module
from app.ai.graphs import diagnose as dg
from app.ai.rag.retriever import RetrievalResult
from app.core.errors import AllProvidersFailed, StreamInterrupted
from app.core.models import Observation
from app.main import app
from app.routes import diagnose as routes_diagnose

pytestmark = pytest.mark.asyncio

CHUNK = {
    "chunk_id": "C1",
    "source_title": "Authentication",
    "source_url": "https://docs.example.com/auth",
    "section": "Tokens",
    "page": 1,
}

SPEC = {
    "method": "GET",
    "url": "https://api.example.com/v1/orders",
    "headers": {},
    "body": None,
    "timeout_s": 10,
    "expected_status": 200,
    "question": "why 401?",
    "approved": True,
}


def make_state(**overrides):
    state = {
        "spec": dict(SPEC),
        "run_id": "run-1",
        "user_id": None,
        "retrieval_attempts": 0,
        "diagnosis_attempts": 0,
        "trace": {},
    }
    state.update(overrides)
    return state


class Fakes:
    """Mutable stand-ins for the graph's external dependencies."""

    def __init__(self) -> None:
        self.saved: list[dict] = []
        self.retrieve_calls: list[tuple[str, str | None, str]] = []
        self.stream_calls = 0
        self.executed = 0
        self.observation = Observation(
            request_id="req-1", status=401, latency_ms=12
        )
        self.retrieval: RetrievalResult | object = RetrievalResult(
            chunks=[dict(CHUNK)], query="q"
        )
        self.tokens: object = ["The token expired [C1]."]
        self.execute_error: Exception | None = None
        self.retrieve_error: Exception | None = None
        self.stream_error: Exception | None = None
        self.provider_list: list[object] = [object()]

    async def execute(self, spec):
        self.executed += 1
        if self.execute_error:
            raise self.execute_error
        return self.observation

    async def retrieve(self, question, api_name=None, path=""):
        self.retrieve_calls.append((question, api_name, path))
        if self.retrieve_error:
            raise self.retrieve_error
        if callable(self.retrieval):
            return self.retrieval(len(self.retrieve_calls))
        return self.retrieval

    def providers(self):
        return self.provider_list

    async def stream(self, providers, system, user, on_skip=None, **kwargs):
        self.stream_calls += 1
        if self.stream_error:
            raise self.stream_error
        tokens = self.tokens(self.stream_calls) if callable(self.tokens) else self.tokens
        for token in tokens:
            yield "groq", token


def install(monkeypatch, fakes: Fakes) -> None:
    monkeypatch.setattr(dg, "execute", fakes.execute)
    monkeypatch.setattr(dg, "retrieve_for_api", fakes.retrieve)
    monkeypatch.setattr(dg, "get_providers", fakes.providers)
    monkeypatch.setattr(dg, "stream_with_fallback", fakes.stream)
    monkeypatch.setattr(dg.runs, "save", lambda row: fakes.saved.append(row))


async def run_graph(state):
    graph = dg.build_diagnose_graph()
    return await graph.ainvoke(state, config={"recursion_limit": 12})


async def collect_events(state):
    graph = dg.build_diagnose_graph()
    return [
        item
        async for item in graph.astream(
            state, config={"recursion_limit": 12}, stream_mode="custom"
        )
    ]


def event_names(events):
    return [event["event"] for event in events]


def event_data(events, name):
    return [event["data"] for event in events if event["event"] == name]


# ── graph: happy path ────────────────────────────────────────────────────────


async def test_happy_path_diagnoses_and_saves_once(monkeypatch):
    fakes = Fakes()
    install(monkeypatch, fakes)

    final = await run_graph(make_state())

    assert final["outcome"] == "diagnosed"
    assert final["citations"] == ["C1"]
    assert final["invalid_citations"] == []
    assert final["provider"] == "groq"
    assert final["retrieval_attempts"] == 1
    assert final["diagnosis_attempts"] == 1
    assert fakes.executed == 1
    assert len(fakes.saved) == 1


async def test_happy_path_emits_legacy_event_sequence(monkeypatch):
    fakes = Fakes()
    install(monkeypatch, fakes)

    events = await collect_events(make_state())

    assert event_names(events) == [
        "observation",
        "retrieval",
        "provider",
        "token",
        "done",
        "trace",
    ]
    assert event_data(events, "token") == [{"text": "The token expired [C1]."}]
    assert event_data(events, "provider") == [{"name": "groq"}]


async def test_saved_record_has_legacy_shape(monkeypatch):
    fakes = Fakes()
    install(monkeypatch, fakes)

    await run_graph(make_state())

    record = fakes.saved[0]
    assert record["run_id"] == "run-1"
    assert record["user_id"] is None
    assert record["method"] == "GET"
    assert record["url"] == str(SPEC["url"])
    assert record["status"] == 401
    assert record["error_type"] is None
    assert record["retrieved_ids"] == ["C1"]
    assert record["provider"] == "groq"
    assert record["diagnosis"] == "The token expired [C1]."
    assert record["patch"] is None


# ── graph: retrieval loops ───────────────────────────────────────────────────


async def test_insufficient_retrieval_retries_once_then_succeeds(monkeypatch):
    fakes = Fakes()

    def retrieval(attempt: int):
        if attempt == 1:
            return RetrievalResult(insufficient=True, query="first")
        return RetrievalResult(chunks=[dict(CHUNK)], query="second")

    fakes.retrieval = retrieval
    install(monkeypatch, fakes)

    final = await run_graph(make_state())

    assert final["outcome"] == "diagnosed"
    assert final["retrieval_attempts"] == 2
    assert len(fakes.retrieve_calls) == 2
    # The retry drops the version path and broadens the query.
    assert fakes.retrieve_calls[0][2] == str(SPEC["url"])
    assert fakes.retrieve_calls[1][2] == ""
    assert "API documentation" in fakes.retrieve_calls[1][0]


async def test_insufficient_after_two_attempts_is_low_confidence(monkeypatch):
    fakes = Fakes()
    fakes.retrieval = RetrievalResult(insufficient=True)
    install(monkeypatch, fakes)

    events = await collect_events(make_state())

    assert len(fakes.retrieve_calls) == 2
    assert fakes.stream_calls == 0
    assert fakes.saved == []
    done = event_data(events, "done")[0]
    assert done["confidence"] == "low"
    assert done["citations"] == []

    fresh = Fakes()
    fresh.retrieval = RetrievalResult(insufficient=True)
    install(monkeypatch, fresh)
    final = await run_graph(make_state())
    assert final["outcome"] == "insufficient_evidence"
    assert final["retrieval_attempts"] == 2


async def test_version_mismatch_short_circuits(monkeypatch):
    fakes = Fakes()
    fakes.retrieval = RetrievalResult(
        version_mismatch=True, other_versions=["v2", "v3"]
    )
    install(monkeypatch, fakes)

    events = await collect_events(make_state())

    assert len(fakes.retrieve_calls) == 1
    assert fakes.stream_calls == 0
    assert fakes.saved == []
    done = event_data(events, "done")[0]
    assert "v2" in done["diagnosis"]

    fresh = Fakes()
    fresh.retrieval = RetrievalResult(version_mismatch=True, other_versions=["v2"])
    install(monkeypatch, fresh)
    final = await run_graph(make_state())
    assert final["outcome"] == "version_mismatch"


# ── graph: approval and failures ─────────────────────────────────────────────


async def test_approval_required_blocks_execution(monkeypatch):
    fakes = Fakes()
    install(monkeypatch, fakes)
    spec = dict(SPEC, method="POST", approved=False)

    events = await collect_events(make_state(spec=spec))
    final = await run_graph(make_state(spec=spec))

    assert final["outcome"] == "error"
    assert final["error_code"] == "approval_required"
    assert fakes.executed == 0
    assert fakes.saved == []
    assert event_names(events) == ["error", "trace"]
    assert event_data(events, "error")[0]["code"] == "approval_required"


async def test_no_providers_returns_error(monkeypatch):
    fakes = Fakes()
    fakes.provider_list = []
    install(monkeypatch, fakes)

    events = await collect_events(make_state())
    final = await run_graph(make_state())

    assert final["outcome"] == "error"
    assert final["error_code"] == "no_providers_configured"
    assert fakes.saved == []
    assert event_data(events, "error")[0]["code"] == "no_providers_configured"


async def test_all_providers_failed_returns_error(monkeypatch):
    fakes = Fakes()
    fakes.stream_error = AllProvidersFailed("every provider failed")
    install(monkeypatch, fakes)

    final = await run_graph(make_state())

    assert final["outcome"] == "error"
    assert final["error_code"] == "all_providers_failed"
    assert final["error_message"] == "every provider failed"
    assert fakes.saved == []


async def test_stream_interrupted_returns_error(monkeypatch):
    fakes = Fakes()
    fakes.stream_error = StreamInterrupted("connection dropped")
    install(monkeypatch, fakes)

    final = await run_graph(make_state())

    assert final["outcome"] == "error"
    assert final["error_code"] == "stream_interrupted"
    assert fakes.saved == []


async def test_observe_failure_returns_error_without_retrieval(monkeypatch):
    fakes = Fakes()
    fakes.execute_error = RuntimeError("boom")
    install(monkeypatch, fakes)

    events = await collect_events(make_state())
    final = await run_graph(make_state())

    assert final["outcome"] == "error"
    assert final["error_code"] == "RuntimeError"
    assert fakes.retrieve_calls == []
    assert fakes.saved == []
    assert event_names(events) == ["error", "trace"]


async def test_retrieval_failure_returns_error_without_generation(monkeypatch):
    fakes = Fakes()
    fakes.retrieve_error = ConnectionError("vector store down")
    install(monkeypatch, fakes)

    events = await collect_events(make_state())
    final = await run_graph(make_state())

    assert final["outcome"] == "error"
    assert final["error_code"] == "retrieval_failed"
    assert fakes.stream_calls == 0
    assert fakes.saved == []
    assert "vector store down" in event_data(events, "error")[0]["message"]


async def test_policy_error_observation_still_continues(monkeypatch):
    fakes = Fakes()
    fakes.observation = Observation(
        request_id="req-1",
        error_type="blocked_by_policy",
        error_message="host not allowed",
    )
    install(monkeypatch, fakes)

    final = await run_graph(make_state())

    assert final["outcome"] == "diagnosed"
    assert fakes.saved[0]["status"] is None
    assert fakes.saved[0]["error_type"] == "blocked_by_policy"


# ── graph: generation repair ─────────────────────────────────────────────────


async def test_invalid_citation_triggers_one_repair(monkeypatch):
    fakes = Fakes()

    def tokens(attempt: int):
        return ["Bad [UNKNOWN]."] if attempt == 1 else ["Fixed [C1]."]

    fakes.tokens = tokens
    install(monkeypatch, fakes)

    final = await run_graph(make_state())

    assert final["outcome"] == "diagnosed"
    assert final["diagnosis_attempts"] == 2
    assert final["citations"] == ["C1"]
    assert final["invalid_citations"] == []
    assert fakes.stream_calls == 2
    assert len(fakes.saved) == 1


async def test_invalid_citations_after_repair_are_reported(monkeypatch):
    fakes = Fakes()
    fakes.tokens = ["Still wrong [UNKNOWN]."]
    install(monkeypatch, fakes)

    events = await collect_events(make_state())

    assert fakes.stream_calls == 2
    assert len(fakes.saved) == 1
    assert event_data(events, "done")[0]["invalid_citations"] == ["UNKNOWN"]

    fresh = Fakes()
    fresh.tokens = ["Still wrong [UNKNOWN]."]
    install(monkeypatch, fresh)
    final = await run_graph(make_state())
    assert final["outcome"] == "diagnosed"
    assert final["diagnosis_attempts"] == 2
    assert final["citations"] == []
    assert final["invalid_citations"] == ["UNKNOWN"]


async def test_patch_is_extracted_and_saved(monkeypatch):
    fakes = Fakes()
    fakes.tokens = [
        'Retry with a fresh token [C1].\n'
        "```json\n"
        '{"patch":{"set_headers":{"Authorization":"Bearer <REDACTED>"},'
        '"remove_headers":[],"set_body":null,"set_url":null},'
        '"confidence":"high","next_checks":[]}\n'
        "```"
    ]
    install(monkeypatch, fakes)

    final = await run_graph(make_state())

    expected = {
        "set_headers": {"Authorization": "Bearer <REDACTED>"},
        "remove_headers": [],
        "set_body": None,
        "set_url": None,
    }
    assert final["patch"] == expected
    assert fakes.saved[0]["patch"] == expected


async def test_provider_fallback_event_precedes_tokens(monkeypatch):
    fakes = Fakes()

    async def stream(providers, system, user, on_skip=None, **kwargs):
        if on_skip:
            on_skip("broken", "rate limited", True, False)
        yield "groq", "Answer [C1]."

    fakes.stream = stream
    install(monkeypatch, fakes)

    events = await collect_events(make_state())

    names = event_names(events)
    assert names.index("provider_fallback") < names.index("provider")
    assert event_data(events, "provider_fallback") == [
        {"from": "broken", "reason": "rate limited"}
    ]


# ── route dispatcher ─────────────────────────────────────────────────────────


@asynccontextmanager
async def api_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=30
    ) as client:
        yield client


async def read_sse(response) -> str:
    return "".join([chunk async for chunk in response.aiter_text()])


def parse_sse(body: str):
    events = []
    for block in body.strip().split("\n\n"):
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if name:
            events.append((name, data))
    return events


async def test_flag_off_uses_legacy_path(monkeypatch):
    from app.db import runs

    monkeypatch.setattr(routes_diagnose.settings, "use_langgraph_diagnose", False)

    async def execute(spec):
        return Observation(request_id="req", status=401)

    async def retrieve(question, api_name=None, path=""):
        return RetrievalResult(chunks=[dict(CHUNK)], query=question)

    async def stream(*args, **kwargs):
        yield "test:model", "Evidence [C1]."

    monkeypatch.setattr(routes_diagnose, "execute", execute)
    monkeypatch.setattr(routes_diagnose, "retrieve_for_api", retrieve)
    monkeypatch.setattr(routes_diagnose, "get_providers", lambda: [object()])
    monkeypatch.setattr(routes_diagnose, "stream_with_fallback", stream)
    monkeypatch.setattr(runs, "save", lambda record: None)

    def explode():
        raise AssertionError("legacy path must not build the graph")

    monkeypatch.setattr(graphs_module, "get_diagnose_graph", explode)

    async with api_client() as client:
        async with client.stream(
            "POST", "/api/diagnose", json={"method": "GET", "url": SPEC["url"]}
        ) as response:
            body = await read_sse(response)

    assert response.status_code == 200
    names = [name for name, _ in parse_sse(body)]
    assert names == ["observation", "retrieval", "provider", "token", "done"]


async def test_flag_on_streams_graph_events_and_hides_trace(monkeypatch):
    class FakeGraph:
        def __init__(self):
            self.calls = []

        async def astream(self, state, config=None, stream_mode=None):
            self.calls.append((state, config, stream_mode))
            yield {"event": "observation", "data": {"status": 401}}
            yield {"event": "retrieval", "data": {"chunks": []}}
            yield {"event": "token", "data": {"text": "hi"}}
            yield {
                "event": "done",
                "data": {"run_id": "run-1", "diagnosis": "hi", "citations": []},
            }
            yield {"event": "trace", "data": {"outcome": "diagnosed"}}

    fake = FakeGraph()
    monkeypatch.setattr(routes_diagnose.settings, "use_langgraph_diagnose", True)
    monkeypatch.setattr(routes_diagnose.settings, "langgraph_recursion_limit", 12)
    monkeypatch.setattr(graphs_module, "get_diagnose_graph", lambda: fake)

    async with api_client() as client:
        async with client.stream(
            "POST",
            "/api/diagnose",
            json={"method": "GET", "url": SPEC["url"], "question": "why 401?"},
        ) as response:
            body = await read_sse(response)

    names = [name for name, _ in parse_sse(body)]
    assert names == ["observation", "retrieval", "token", "done"]
    assert "event: trace" not in body

    state, config, stream_mode = fake.calls[0]
    assert stream_mode == "custom"
    assert config == {"recursion_limit": 12}
    assert state["spec"]["method"] == "GET"
    assert state["spec"]["question"] == "why 401?"
    assert state["retrieval_attempts"] == 0
    assert state["diagnosis_attempts"] == 0


async def test_flag_on_graph_failure_becomes_error_event(monkeypatch):
    class BrokenGraph:
        async def astream(self, state, config=None, stream_mode=None):
            yield {"event": "token", "data": {"text": "partial"}}
            raise RuntimeError("graph exploded")

    monkeypatch.setattr(routes_diagnose.settings, "use_langgraph_diagnose", True)
    monkeypatch.setattr(
        graphs_module, "get_diagnose_graph", lambda: BrokenGraph()
    )

    async with api_client() as client:
        async with client.stream(
            "POST", "/api/diagnose", json={"method": "GET", "url": SPEC["url"]}
        ) as response:
            body = await read_sse(response)

    events = parse_sse(body)
    assert [name for name, _ in events] == ["token", "error"]
    assert events[-1][1]["code"] == "RuntimeError"
    assert "graph exploded" in events[-1][1]["message"]


async def test_flag_on_matches_legacy_event_contract(monkeypatch):
    """Same fakes through both paths must yield the same SSE event names."""

    async def execute(spec):
        return Observation(request_id="req", status=401)

    async def retrieve(question, api_name=None, path=""):
        return RetrievalResult(chunks=[dict(CHUNK)], query=question)

    async def stream(*args, **kwargs):
        yield "test:model", "Evidence [C1]."

    from app.db import runs

    monkeypatch.setattr(runs, "save", lambda record: None)
    monkeypatch.setattr(routes_diagnose, "execute", execute)
    monkeypatch.setattr(routes_diagnose, "retrieve_for_api", retrieve)
    monkeypatch.setattr(routes_diagnose, "get_providers", lambda: [object()])
    monkeypatch.setattr(routes_diagnose, "stream_with_fallback", stream)

    monkeypatch.setattr(dg, "execute", execute)
    monkeypatch.setattr(dg, "retrieve_for_api", retrieve)
    monkeypatch.setattr(dg, "get_providers", lambda: [object()])
    monkeypatch.setattr(dg, "stream_with_fallback", stream)

    payload = {"method": "GET", "url": SPEC["url"]}

    monkeypatch.setattr(routes_diagnose.settings, "use_langgraph_diagnose", False)
    async with api_client() as client:
        async with client.stream("POST", "/api/diagnose", json=payload) as response:
            legacy_body = await read_sse(response)

    monkeypatch.setattr(routes_diagnose.settings, "use_langgraph_diagnose", True)
    async with api_client() as client:
        async with client.stream("POST", "/api/diagnose", json=payload) as response:
            graph_body = await read_sse(response)

    legacy_names = [name for name, _ in parse_sse(legacy_body)]
    graph_names = [name for name, _ in parse_sse(graph_body)]
    assert legacy_names == graph_names == [
        "observation",
        "retrieval",
        "provider",
        "token",
        "done",
    ]
