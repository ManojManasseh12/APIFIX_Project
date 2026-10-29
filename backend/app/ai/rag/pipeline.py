from pathlib import Path
from typing import Any, Protocol

from app.ai.rag.data_loader import data_process
from app.ai.rag.embedding import embedding
from app.ai.rag.models import Chunk, SearchHit
from app.ai.rag.vector_store import vector_store
from app.rag.retriever import RetrievalResult, detect_version, get_retriever


class VectorStoreInterface(Protocol):
    async def insert(
        self, chunks: list[Chunk], vectors: list[list[float]]
    ) -> int: ...

    async def retrieve(
        self,
        query_vector: list[float],
        query_text: str,
        k: int = 5,
        mode: str = "hybrid",
        where: dict[str, Any] | None = None,
    ) -> list[SearchHit]: ...


class RAGPipeline:
    def __init__(
        self,
        store: VectorStoreInterface = vector_store,
    ):
        self.store = store

    async def ingest(
        self, folder: str | Path, source_files: list[str] | None = None
    ) -> dict[str, Any]:
        chunks = await data_process.process_folder(folder, source_files)
        vectors = await embedding.embed_chunks(chunks)
        count = await self.store.insert(chunks, vectors)
        return {
            "chunks_added": count,
            "sources": sorted(
                {str(chunk.metadata.get("source") or "") for chunk in chunks}
            ),
        }

    async def retrieve(
        self,
        question: str,
        mode: str = "hybrid",
        k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        query_vector = await embedding.embed_prompt(question)
        return await self.store.retrieve(
            query_vector, question, k=k, mode=mode, where=where
        )


async def retrieve_for_api(
    question: str, api_name: str | None = None, path: str = ""
) -> RetrievalResult:
    if (await vector_store.stats())["chunk_count"] == 0:
        return get_retriever().search(question, api_name=api_name, path=path)

    where = {"api_name": api_name} if api_name else None
    query_vector = await embedding.embed_prompt(question)
    dense = await vector_store.retrieve(
        query_vector, question, k=20, mode="dense", where=where
    )
    requested_version = detect_version(path)
    versions = {
        detect_version(str(hit.metadata.get("path") or ""))
        for hit in dense
        if detect_version(str(hit.metadata.get("path") or ""))
    }
    if requested_version and versions and requested_version not in versions:
        return RetrievalResult(
            version_mismatch=True,
            other_versions=sorted(versions),
            query=question,
        )
    if not dense or dense[0].score < 0.15:
        return RetrievalResult(insufficient=True, query=question)

    hits = await vector_store.retrieve(
        query_vector, question, k=6, mode="hybrid", where=where
    )
    chunks = [
        {
            "chunk_id": hit.chunk_id,
            "chunk_text": hit.content,
            "source_title": hit.metadata.get("source_title")
            or hit.metadata.get("source")
            or "",
            "source_url": hit.metadata.get("source_url") or "",
            "section": hit.metadata.get("section") or "",
            "page": hit.metadata.get("page", ""),
            "api_name": hit.metadata.get("api_name") or "",
            "method": hit.metadata.get("method") or "",
            "path": hit.metadata.get("path") or "",
            "score": hit.score,
        }
        for hit in hits
    ]
    return RetrievalResult(
        chunks=chunks,
        insufficient=not chunks,
        query=question,
    )


pipeline = RAGPipeline()


async def ingest(
    folder: str | Path, source_files: list[str] | None = None
) -> dict[str, Any]:
    return await pipeline.ingest(folder, source_files)


async def retrieve(
    question: str,
    mode: str = "hybrid",
    k: int = 5,
    where: dict[str, Any] | None = None,
) -> list[SearchHit]:
    return await pipeline.retrieve(question, mode, k, where)
