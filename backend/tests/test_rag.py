import asyncio

import pytest
from fastapi.testclient import TestClient

from app.ai.rag.data_loader import DataProcess
from app.ai.rag.embedding import embedding
from app.ai.rag.models import Chunk, SearchHit
from app.ai.rag.pipeline import retrieve_for_api
from app.ai.rag.vector_store import VectorStore
from app.core.config import settings
from app.main import app


class WordTokenizer:
    def __init__(self):
        self.words: list[str] = []

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        values = text.split()
        self.words.extend(values)
        return list(range(len(self.words) - len(values), len(self.words)))

    def decode(self, tokens: list[int], skip_special_tokens: bool = True) -> str:
        return " ".join(self.words[token] for token in tokens)


class FakeModel:
    def __init__(self):
        self.tokenizer = WordTokenizer()


def test_chunk_sizes_and_overlap(monkeypatch):
    from scripts import chunk_text

    monkeypatch.setattr(chunk_text, "get_model", lambda model_name=None: FakeModel())
    processor = DataProcess(target_tokens=500, overlap_tokens=80)
    content = " ".join(f"word{index}" for index in range(1100))
    chunks = asyncio.run(
        processor.chunk(
            [
                {
                    "title": "guide",
                    "content": content,
                    "metadata": {"source": "guide.txt", "path": "guide.txt"},
                }
            ]
        )
    )

    token_chunks = [chunk.content.split() for chunk in chunks]
    assert [len(tokens) for tokens in token_chunks] == [500, 500, 260]
    assert chunks[0].id.startswith("KB-")
    assert token_chunks[0][-80:] == token_chunks[1][:80]
    assert token_chunks[1][-80:] == token_chunks[2][:80]
    assert all(chunk.metadata["source"] == "guide.txt" for chunk in chunks)


def test_code_fences_are_not_split(monkeypatch):
    from scripts import chunk_text

    monkeypatch.setattr(chunk_text, "get_model", lambda model_name=None: FakeModel())
    fenced = "```python\none two three four five six\n```"
    chunks = chunk_text.chunk_by_tokens(
        f"before\n{fenced}\nafter", target=4, overlap=1
    )

    assert fenced in chunks
    assert all("```python" not in chunk or "```" in chunk[3:] for chunk in chunks)


def test_headings_inside_code_blocks_do_not_split_sections():
    sections = DataProcess._sections(
        "Intro\n```python\n# not a heading\nprint('hi')\n```\n## Real heading\nText"
    )

    assert len(sections) == 2
    assert sections[0][0] == ""
    assert "# not a heading" in sections[0][1]
    assert sections[1][0] == "Real heading"


def test_yaml_openapi_is_loaded_as_one_chunk_per_operation(tmp_path, monkeypatch):
    from scripts import chunk_text

    monkeypatch.setattr(chunk_text, "get_model", lambda model_name=None: FakeModel())
    spec_path = tmp_path / "api.yaml"
    spec_path.write_text(
        "openapi: 3.0.0\n"
        "info:\n  title: Example API\n  version: '1'\n"
        "paths:\n  /items:\n    get:\n      summary: List items\n"
        "    post:\n      summary: Create item\n",
        encoding="utf-8",
    )

    chunks = asyncio.run(DataProcess().load_openapi(spec_path))

    assert len(chunks) == 2
    assert {chunk.metadata["method"] for chunk in chunks} == {"GET", "POST"}
    assert all(chunk.metadata["source"] == "api.yaml" for chunk in chunks)
    assert all(chunk.metadata["path"] == "/items" for chunk in chunks)
    assert all(chunk.metadata["file_path"] == str(spec_path) for chunk in chunks)


def test_pdf_without_text_reports_no_extractable_text(tmp_path):
    from pypdf import PdfWriter

    pdf_path = tmp_path / "scanned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with pdf_path.open("wb") as file:
        writer.write(file)

    with pytest.raises(ValueError, match="no extractable text"):
        asyncio.run(DataProcess().load_pdf(pdf_path))


class MemoryCollection:
    def __init__(self):
        self.rows = {}

    def upsert(self, ids, documents, metadatas, embeddings):
        for chunk_id, document, metadata, vector in zip(
            ids, documents, metadatas, embeddings
        ):
            self.rows[chunk_id] = {
                "document": document,
                "metadata": metadata,
                "embedding": vector,
            }

    def get(self, where=None, include=None):
        ids = list(self.rows)
        if where:
            ids = [
                chunk_id
                for chunk_id in ids
                if all(
                    self.rows[chunk_id]["metadata"].get(key) == value
                    for key, value in where.items()
                )
            ]
        return {
            "ids": ids,
            "documents": [self.rows[chunk_id]["document"] for chunk_id in ids],
            "metadatas": [self.rows[chunk_id]["metadata"] for chunk_id in ids],
        }

    def count(self):
        return len(self.rows)


def test_insert_is_idempotent_and_normalizes_chroma_metadata():
    collection = MemoryCollection()
    store = VectorStore(collection=collection)
    chunk = Chunk(
        id="stable-id",
        title="API docs",
        content="HTTP 401 unauthorized",
        metadata={"source": "guide.jsonl", "tags": ["auth", "http"]},
    )

    assert asyncio.run(store.insert([chunk], [[0.1, 0.2]])) == 1
    assert asyncio.run(store.insert([chunk], [[0.3, 0.4]])) == 1
    assert collection.count() == 1
    assert collection.rows["stable-id"]["metadata"]["tags"] == "auth, http"


def test_vector_store_uses_installed_chroma_collection_api(tmp_path):
    import chromadb

    client = chromadb.PersistentClient(path=str(tmp_path))
    collection = client.get_or_create_collection(
        name="apifix_docs_test",
        configuration={"hnsw": {"space": "cosine"}},
    )
    store = VectorStore(collection=collection)
    chunk = Chunk(
        id="persisted-id",
        title="Auth docs",
        content="HTTP 401 requires valid authentication credentials",
        metadata={"source": "auth.jsonl", "tags": ["auth"]},
    )

    assert asyncio.run(store.insert([chunk], [[1.0, 0.0]])) == 1
    dense = asyncio.run(
        store.retrieve([1.0, 0.0], "authentication credentials", mode="dense")
    )
    keyword = asyncio.run(
        store.retrieve([1.0, 0.0], "authentication credentials", mode="keyword")
    )
    stats = asyncio.run(store.stats())

    assert dense[0].chunk_id == "persisted-id"
    assert keyword[0].chunk_id == "persisted-id"
    assert stats == {
        "chunk_count": 1,
        "sources": [{"source": "auth.jsonl", "count": 1}],
    }
    assert asyncio.run(store.delete_source("auth.jsonl")) == 1
    assert collection.count() == 0
    assert asyncio.run(store.stats()) == {"chunk_count": 0, "sources": []}


def test_existing_api_retrieval_uses_retrieved_chunk_ids(monkeypatch):
    async def stats():
        return {"chunk_count": 1, "sources": [{"source": "guide.md", "count": 1}]}

    async def embed_prompt(question):
        return [1.0, 0.0]

    async def retrieve(query_vector, query_text, k, mode, where):
        return [
            SearchHit(
                chunk_id="retrieved-only",
                content="Evidence",
                metadata={
                    "source": "guide.md",
                    "source_title": "Guide",
                    "path": "/v1/items",
                },
                score=0.8,
            )
        ]

    monkeypatch.setattr(VectorStore, "stats", lambda self: stats())
    monkeypatch.setattr(embedding, "embed_prompt", embed_prompt)
    monkeypatch.setattr(VectorStore, "retrieve", lambda self, *args, **kwargs: retrieve(*args, **kwargs))

    result = asyncio.run(
        retrieve_for_api("question", path="https://api.test/v1/items")
    )

    assert [chunk["chunk_id"] for chunk in result.chunks] == ["retrieved-only"]
    assert result.chunks[0]["chunk_text"] == "Evidence"


@pytest.mark.parametrize(
    "filename",
    ["../../outside.txt", r"..\outside.txt", "payload.exe"],
)
def test_upload_rejects_path_traversal_and_bad_extensions(
    tmp_path, monkeypatch, filename
):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    client = TestClient(app)

    response = client.post(
        "/upload",
        files=[("files", (filename, b"not saved"))],
    )

    assert response.status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_upload_enforces_maximum_size(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    client = TestClient(app)

    response = client.post(
        "/upload",
        files=[("files", ("large.txt", b"x" * (1024 * 1024 + 1)))],
    )

    assert response.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_upload_list_delete_and_api_aliases(tmp_path, monkeypatch):
    from app.routes import upload as upload_routes

    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    async def delete_source(source):
        assert source.endswith(".txt")
        return 2

    monkeypatch.setattr(upload_routes.vector_store, "delete_source", delete_source)
    client = TestClient(app)
    response = client.post(
        "/api/upload",
        files=[("files", ("guide.txt", b"RAG upload", "text/plain"))],
    )
    assert response.status_code == 200
    filename = response.json()["files"][0]["name"]
    assert client.get("/files").json()["files"][0]["name"] == filename
    assert client.delete(f"/api/files/{filename}").json()["chunks_deleted"] == 2
    assert not (tmp_path / filename).exists()
    assert client.get("/api/health").json()["ok"] is True
    cors_response = client.options(
        f"/api/files/{filename}",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "DELETE",
        },
    )
    assert cors_response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_ingest_and_search_routes_return_expected_shapes(tmp_path, monkeypatch):
    from app.routes import ingest as ingest_routes
    from app.routes import search as search_routes

    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    (tmp_path / "guide.txt").write_text("API request docs", encoding="utf-8")

    async def ingest(folder, source_files=None):
        return {"chunks_added": 1, "sources": source_files or ["guide.txt"]}

    async def retrieve(question, mode, k):
        return [
            SearchHit(
                chunk_id="match-1",
                content=question,
                metadata={"source": "guide.txt"},
                score=0.9,
            )
        ]

    async def stats():
        return {
            "chunk_count": 1,
            "sources": [{"source": "guide.txt", "count": 1}],
        }

    monkeypatch.setattr(ingest_routes.pipeline, "ingest", ingest)
    monkeypatch.setattr(ingest_routes, "ensure_seeded", stats)
    monkeypatch.setattr(search_routes, "ensure_seeded", stats)
    monkeypatch.setattr(search_routes.pipeline, "retrieve", retrieve)
    client = TestClient(app)

    ingest_response = client.post("/api/ingest", json={"files": ["guide.txt"]})
    search_response = client.post(
        "/search", json={"query": "API request", "mode": "keyword", "k": 3}
    )
    stats_response = client.get("/api/kb/stats")

    assert ingest_response.json() == {
        "chunks_added": 1,
        "sources": ["guide.txt"],
    }
    assert search_response.json()["hits"][0]["chunk_id"] == "match-1"
    assert search_response.json()["hits"][0]["source"] == "guide.txt"
    assert stats_response.json()["chunk_count"] == 1


def test_diagnose_stream_only_accepts_retrieved_citations(monkeypatch):
    from app.core.db import runs
    from app.core.models import Observation
    from app.rag.retriever import RetrievalResult
    from app.routes import routes_diagnose

    async def execute(spec):
        return Observation(request_id="test", status=401)

    async def retrieve_for_api(question, api_name=None, path=""):
        return RetrievalResult(
            chunks=[{"chunk_id": "doc-123", "chunk_text": "Evidence"}],
            query=question,
        )

    async def stream(*args, **kwargs):
        yield "test-provider", "Evidence [doc-123]. Unsupported [KB-INVENTED]."

    monkeypatch.setattr(routes_diagnose, "execute", execute)
    monkeypatch.setattr(routes_diagnose, "retrieve_for_api", retrieve_for_api)
    monkeypatch.setattr(routes_diagnose, "get_providers", lambda: [object()])
    monkeypatch.setattr(routes_diagnose, "stream_with_fallback", stream)
    monkeypatch.setattr(runs, "save", lambda record: None)
    client = TestClient(app)

    response = client.post(
        "/api/diagnose",
        json={"method": "GET", "url": "http://localhost:8001/v1/profile"},
    )

    assert response.status_code == 200
    assert '"citations": ["doc-123"]' in response.text
    assert '"invalid_citations": ["KB-INVENTED"]' in response.text
