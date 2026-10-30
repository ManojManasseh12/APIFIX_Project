import asyncio

from app.ai.rag import pipeline as pipeline_module
from app.core.config import settings


def test_ensure_seeded_ingests_seed_once_when_empty(tmp_path, monkeypatch):
    seed = tmp_path / "kb_chunks.jsonl"
    seed.write_text('{"chunk_id": "KB-1", "chunk_text": "auth"}\n', encoding="utf-8")

    calls: list[tuple] = []
    state = {"count": 0}

    async def stats():
        return {"chunk_count": state["count"], "sources": []}

    async def ingest(folder, source_files=None):
        calls.append((folder, source_files))
        state["count"] = 1
        return {"chunks_added": 1, "sources": ["kb_chunks.jsonl"]}

    monkeypatch.setattr(settings, "seed_kb", True)
    monkeypatch.setattr(settings, "seed_kb_file", seed)
    monkeypatch.setattr(pipeline_module.vector_store, "stats", stats)
    monkeypatch.setattr(pipeline_module.pipeline, "ingest", ingest)

    async def run():
        first = await pipeline_module.ensure_seeded()
        second = await pipeline_module.ensure_seeded()
        return first, second

    first, second = asyncio.run(run())

    assert first["chunk_count"] == 1
    assert second["chunk_count"] == 1
    assert calls == [(seed.parent, [seed.name])]


def test_ensure_seeded_respects_disabled_flag(tmp_path, monkeypatch):
    seed = tmp_path / "kb_chunks.jsonl"
    seed.write_text("{}\n", encoding="utf-8")
    calls: list[tuple] = []

    async def stats():
        return {"chunk_count": 0, "sources": []}

    async def ingest(folder, source_files=None):
        calls.append((folder, source_files))

    monkeypatch.setattr(settings, "seed_kb", False)
    monkeypatch.setattr(settings, "seed_kb_file", seed)
    monkeypatch.setattr(pipeline_module.vector_store, "stats", stats)
    monkeypatch.setattr(pipeline_module.pipeline, "ingest", ingest)

    result = asyncio.run(pipeline_module.ensure_seeded())

    assert result["chunk_count"] == 0
    assert calls == []
