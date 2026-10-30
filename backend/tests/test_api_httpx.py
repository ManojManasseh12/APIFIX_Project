import socket
import threading
import time
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn

from app.ai.rag.vector_store import vector_store
from app.core.config import settings
from app.core.models import Observation
from app.core.security import ssrf
from app.main import app
from app.rag.retriever import RetrievalResult
from mock_api.main import app as mock_app

pytestmark = pytest.mark.asyncio


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def mock_api():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(mock_app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("mock API did not start")

    host = f"127.0.0.1:{port}"
    original_hosts = settings.allowed_hosts
    original_loopback = ssrf.LOOPBACK_ALLOW
    settings.allowed_hosts = host
    ssrf.LOOPBACK_ALLOW = {host}
    try:
        yield f"http://{host}"
    finally:
        settings.allowed_hosts = original_hosts
        ssrf.LOOPBACK_ALLOW = original_loopback
        server.should_exit = True
        thread.join(timeout=5)


@asynccontextmanager
async def api_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=30
    ) as client:
        yield client


async def reset_mock(mock_api: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f"{mock_api}/__reset")


async def read_sse(response: httpx.Response) -> str:
    chunks = []
    async for chunk in response.aiter_text():
        chunks.append(chunk)
    return "".join(chunks)


async def test_health_over_httpx():
    async with api_client() as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "apifix-ai"}


async def test_test_endpoint_observes_mock_and_redacts_headers(mock_api):
    spec = {
        "method": "GET",
        "url": f"{mock_api}/v1/profile",
        "headers": {"Authorization": "Bearer expired-token"},
        "approved": True,
    }

    async with api_client() as client:
        response = await client.post("/api/test", json=spec)

    body = response.json()
    assert response.status_code == 200
    assert body["observation"]["status"] == 401
    assert body["observation"]["body_preview"] == '{"error":"token_expired"}'
    assert body["request"]["headers"]["Authorization"] == "<REDACTED>"


async def test_mutation_requires_approval_over_httpx(mock_api):
    await reset_mock(mock_api)
    spec = {
        "method": "POST",
        "url": f"{mock_api}/v1/orders",
        "headers": {"Authorization": "Bearer valid-user-token"},
        "body": {"items": [{"sku": "A1", "quantity": 2}]},
    }

    async with api_client() as client:
        denied = await client.post("/api/test", json=spec)
        allowed = await client.post("/api/test", json={**spec, "approved": True})

    assert denied.status_code == 403
    assert "approved=true" in denied.json()["detail"]
    assert allowed.status_code == 200
    assert allowed.json()["observation"]["status"] == 201


async def test_retest_returns_a_real_diff(mock_api):
    before = {
        "request_id": "before",
        "status": 401,
        "headers": {},
        "body_preview": '{"error":"token_expired"}',
        "latency_ms": 10,
    }
    payload = {
        "spec": {
            "method": "GET",
            "url": f"{mock_api}/v1/profile",
            "headers": {"Authorization": "Bearer expired-token"},
        },
        "patch": {"set_headers": {"Authorization": "Bearer valid-user-token"}},
        "before": before,
    }

    async with api_client() as client:
        response = await client.post("/api/retest", json=payload)

    body = response.json()
    assert response.status_code == 200
    assert body["observation"]["status"] == 200
    assert body["diff"]["status_before"] == 401
    assert body["diff"]["status_after"] == 200
    assert body["diff"]["verdict"] == "changed"


async def test_upload_ingest_search_and_delete_over_httpx(
    tmp_path, monkeypatch
):
    import chromadb

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.get_or_create_collection(
        name="httpx_upload_test",
        configuration={"hnsw": {"space": "cosine"}},
    )
    monkeypatch.setattr(vector_store, "_collection", collection)
    monkeypatch.setattr(vector_store, "_bm25", None)
    monkeypatch.setattr(vector_store, "_ids", [])
    monkeypatch.setattr(vector_store, "_documents", [])
    monkeypatch.setattr(vector_store, "_metadatas", [])
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "seed_kb", False)

    async with api_client() as client:
        upload = await client.post(
            "/api/upload",
            files={
                "files": (
                    "guide.md",
                    b"# Rate limits\n\nA 429 response includes Retry-After.",
                    "text/markdown",
                )
            },
        )
        name = upload.json()["files"][0]["name"]
        ingest = await client.post("/api/ingest", json={"files": [name]})
        stats = await client.get("/api/kb/stats")
        search = await client.post(
            "/api/search",
            json={"query": "429 retry-after", "mode": "hybrid", "k": 3},
        )
        deleted = await client.delete(f"/api/files/{name}")

    assert upload.status_code == 200
    assert ingest.json()["chunks_added"] == 1
    assert stats.json()["chunk_count"] == 1
    assert search.json()["hits"][0]["source"] == name
    assert deleted.json()["chunks_deleted"] == 1


async def test_diagnose_streams_sse_over_httpx(monkeypatch):
    from app.core.db import runs
    from app.routes import routes_diagnose

    async def execute(spec):
        return Observation(request_id="test", status=401)

    async def retrieve_for_api(question, api_name=None, path=""):
        return RetrievalResult(
            chunks=[{"chunk_id": "doc-123", "chunk_text": "Evidence"}],
            query=question,
        )

    async def stream(*args, **kwargs):
        yield "test:model", "Evidence [doc-123]."

    monkeypatch.setattr(routes_diagnose, "execute", execute)
    monkeypatch.setattr(routes_diagnose, "retrieve_for_api", retrieve_for_api)
    monkeypatch.setattr(routes_diagnose, "get_providers", lambda: [object()])
    monkeypatch.setattr(routes_diagnose, "stream_with_fallback", stream)
    monkeypatch.setattr(runs, "save", lambda record: None)

    async with api_client() as client:
        async with client.stream(
            "POST",
            "/api/diagnose",
            json={"method": "GET", "url": "http://localhost:8001/v1/profile"},
        ) as response:
            body = await read_sse(response)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: observation" in body
    assert "event: done" in body
    assert '"citations": ["doc-123"]' in body


async def test_ask_streams_sse_over_httpx(monkeypatch):
    from app.routes import routes_ask

    async def retrieve_for_api(question, api_name=None, path=""):
        return RetrievalResult(
            chunks=[
                {
                    "chunk_id": "doc-1",
                    "chunk_text": "Use idempotency keys.",
                    "source_title": "Idempotency",
                    "source_url": "https://example.test/idempotency",
                    "section": "Idempotency",
                    "page": "",
                }
            ],
            query=question,
        )

    async def stream(*args, **kwargs):
        yield "test:model", "Use idempotency keys [doc-1]."

    monkeypatch.setattr(routes_ask, "retrieve_for_api", retrieve_for_api)
    monkeypatch.setattr(routes_ask, "get_providers", lambda: [object()])
    monkeypatch.setattr(routes_ask, "stream_with_fallback", stream)

    async with api_client() as client:
        async with client.stream(
            "POST", "/api/ask", json={"question": "How do I avoid duplicates?"}
        ) as response:
            body = await read_sse(response)

    assert response.status_code == 200
    assert "event: retrieval" in body
    assert "event: done" in body
    assert '"citations": ["doc-1"]' in body


async def test_httpx_client_is_using_the_real_app():
    # Keep a guard against accidentally swapping the ASGI transport for a mock
    # that never executes the route handlers.
    async with api_client() as client:
        response = await client.get("/openapi.json")

    paths = response.json()["paths"]
    assert app.title == "APIFix AI"
    assert {"/api/test", "/api/diagnose", "/api/ask"} <= set(paths)
