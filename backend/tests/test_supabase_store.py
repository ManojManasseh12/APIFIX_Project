import pytest

from app.ai.rag import vector_store as vector_store_module
from app.ai.rag.models import Chunk
from app.ai.rag.supabase_store import SupabaseStore
from app.core.config import settings


def bare_store() -> SupabaseStore:
    # Build the object without opening a database connection.
    return SupabaseStore.__new__(SupabaseStore)


def test_row_maps_chunk_metadata_to_kb_chunks():
    store = bare_store()
    chunk = Chunk(
        id="KB-1",
        title="Guide",
        content="A 429 response includes Retry-After.",
        metadata={
            "source": "guide.md",
            "source_title": "Rate limits",
            "source_url": "https://example.test/rate-limits",
            "section": "429",
            "page": "3",
            "api_name": "Example",
            "api_version": "v1",
            "method": "GET",
            "path": "/v1/search",
            "source_type": "upload",
            "license": "MIT",
            "retrieved_at": "2026-10-30",
            "token_count": 8,
        },
    )

    row = store._row(chunk, [0.1] * settings.embed_dim)

    assert row["chunk_id"] == "KB-1"
    assert row["source_file"] == "guide.md"
    assert row["source_title"] == "Rate limits"
    assert row["page"] == 3
    assert row["embedding_dim"] == settings.embed_dim
    assert row["embedding_model"] == settings.embed_model
    assert row["source_type"] == "upload"
    assert row["api_version"] == "v1"


def test_row_defaults_missing_optional_fields():
    store = bare_store()
    chunk = Chunk(id="KB-2", title="Doc", content="text", metadata={})

    row = store._row(chunk, [0.0] * settings.embed_dim)

    assert row["source_file"] == ""
    assert row["source_title"] == "Doc"
    assert row["page"] is None
    assert row["token_count"] is None
    assert row["source_type"] == "upload"


def test_hit_exposes_metadata_for_citations():
    store = bare_store()
    hit = store._hit(
        {
            "chunk_id": "KB-3",
            "chunk_text": "Evidence",
            "source_file": "kb_chunks.jsonl",
            "source_title": "401 Unauthorized",
            "source_url": "https://example.test/401",
            "section": "Status 401",
            "page": None,
            "api_name": "HTTP",
            "api_version": "",
            "method": "",
            "path": "",
        },
        0.75,
    )

    assert hit.chunk_id == "KB-3"
    assert hit.score == 0.75
    assert hit.metadata["source"] == "kb_chunks.jsonl"
    assert hit.metadata["source_title"] == "401 Unauthorized"
    assert hit.metadata["page"] == ""


def test_hybrid_search_fuses_dense_and_keyword_ranks():
    store = bare_store()
    store._search_dense = lambda vector, limit, api_name: [
        {"chunk_id": "a", "chunk_text": "A"},
        {"chunk_id": "b", "chunk_text": "B"},
    ]
    store._search_keyword = lambda query, limit, api_name: [
        {"chunk_id": "b", "chunk_text": "B"},
        {"chunk_id": "c", "chunk_text": "C"},
    ]

    hits = store._retrieve_sync(
        [0.1] * settings.embed_dim, "query", 3, "hybrid", None
    )

    # The best dense results keep their order; keyword then fills the tail.
    assert [hit.chunk_id for hit in hits] == ["a", "b", "c"]
    assert hits[0].score >= hits[1].score


def test_hybrid_search_lets_keyword_rescue_missing_dense_result(monkeypatch):
    store = bare_store()
    store._search_dense = lambda vector, limit, api_name: [
        {"chunk_id": f"dense-{index}", "chunk_text": "D"} for index in range(6)
    ]
    store._search_keyword = lambda query, limit, api_name: [
        {"chunk_id": "dense-5", "chunk_text": "D"},
        {"chunk_id": "dense-0", "chunk_text": "D"},
    ]
    monkeypatch.setattr(settings, "rrf_dense_anchor", 2)
    monkeypatch.setattr(settings, "rrf_keyword_weight", 0.05)

    hits = store._retrieve_sync(
        [0.1] * settings.embed_dim, "query", 5, "hybrid", None
    )

    assert [hit.chunk_id for hit in hits[:2]] == ["dense-0", "dense-1"]
    # dense-5 was outside the dense top 5 but wins a slot from its BM25 rank.
    assert "dense-5" in [hit.chunk_id for hit in hits]


def test_get_store_uses_supabase_when_configured(monkeypatch):
    class FakeStore:
        pass

    monkeypatch.setattr(settings, "retrieval_backend", "supabase")
    monkeypatch.setattr(settings, "database_url", "postgresql://example.test/db")
    monkeypatch.setattr(
        "app.ai.rag.supabase_store.SupabaseStore", FakeStore
    )

    assert isinstance(vector_store_module.get_store(), FakeStore)


def test_get_store_falls_back_to_chroma_on_connection_error(monkeypatch):
    def unavailable():
        raise RuntimeError("database is down")

    monkeypatch.setattr(settings, "retrieval_backend", "supabase")
    monkeypatch.setattr(settings, "database_url", "postgresql://example.test/db")
    monkeypatch.setattr(
        "app.ai.rag.supabase_store.SupabaseStore", unavailable
    )

    assert vector_store_module.get_store() is vector_store_module.vector_store


def test_get_store_stays_local_by_default(monkeypatch):
    monkeypatch.setattr(settings, "retrieval_backend", "local")
    monkeypatch.setattr(settings, "database_url", "postgresql://example.test/db")
    monkeypatch.setattr(
        "app.ai.rag.supabase_store.SupabaseStore",
        lambda: pytest.fail("Supabase should not be constructed"),
    )

    assert vector_store_module.get_store() is vector_store_module.vector_store
