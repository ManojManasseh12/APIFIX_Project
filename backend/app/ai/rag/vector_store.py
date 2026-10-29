import asyncio
import json
import re
import threading
from collections import Counter
from typing import Any

from app.ai.rag.models import Chunk, SearchHit
from app.core.config import settings

_WORD = re.compile(r"\w+", flags=re.UNICODE)


class VectorStore:
    def __init__(self, collection: Any | None = None):
        self._collection = collection
        self._init_lock = threading.Lock()
        self._index_lock = threading.RLock()
        self._bm25 = None
        self._ids: list[str] = []
        self._documents: list[str] = []
        self._metadatas: list[dict[str, Any]] = []

    @property
    def collection(self):
        if self._collection is None:
            with self._init_lock:
                if self._collection is None:
                    import chromadb

                    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
                    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
                    self._collection = client.get_or_create_collection(
                        name="apifix_docs",
                        configuration={"hnsw": {"space": "cosine"}},
                    )
        return self._collection

    async def insert(
        self, chunks: list[Chunk], vectors: list[list[float]]
    ) -> int:
        return await asyncio.to_thread(self._insert_sync, chunks, vectors)

    def _insert_sync(
        self, chunks: list[Chunk], vectors: list[list[float]]
    ) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("Each chunk must have exactly one embedding vector")
        if not chunks:
            return 0

        self.collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            metadatas=[self._chroma_metadata(chunk.metadata) for chunk in chunks],
            embeddings=vectors,
        )
        self._rebuild_keyword_index()
        return len(chunks)

    async def retrieve(
        self,
        query_vector: list[float],
        query_text: str,
        k: int = 5,
        mode: str = "hybrid",
        where: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        return await asyncio.to_thread(
            self._retrieve_sync, query_vector, query_text, k, mode, where
        )

    def _retrieve_sync(
        self,
        query_vector: list[float],
        query_text: str,
        k: int,
        mode: str,
        where: dict[str, Any] | None,
    ) -> list[SearchHit]:
        if k < 1:
            raise ValueError("k must be at least 1")
        if mode not in {"dense", "keyword", "hybrid"}:
            raise ValueError(f"Unsupported retrieval mode: {mode}")

        if mode == "dense":
            return self._dense_search(query_vector, k, where)[:k]
        if mode == "keyword":
            return self._keyword_search(query_text, k, where)[:k]
        dense = self._dense_search(query_vector, k, where)
        keyword = self._keyword_search(query_text, k, where)

        dense_rank = [hit.chunk_id for hit in dense]
        keyword_rank = [hit.chunk_id for hit in keyword]
        fused: dict[str, float] = {}
        for ranking in (dense_rank, keyword_rank):
            for rank, chunk_id in enumerate(ranking, start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (60 + rank)
        records = {hit.chunk_id: hit for hit in dense + keyword}
        return [
            records[chunk_id].model_copy(update={"score": score})
            for chunk_id, score in sorted(
                fused.items(), key=lambda item: (-item[1], item[0])
            )[:k]
        ]

    def _dense_search(
        self, query_vector: list[float], k: int, where: dict[str, Any] | None
    ) -> list[SearchHit]:
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_vector],
            "n_results": k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        result = self.collection.query(**kwargs)
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            SearchHit(
                chunk_id=chunk_id,
                content=documents[index] or "",
                metadata=metadatas[index] or {},
                score=1.0 - float(distances[index]),
            )
            for index, chunk_id in enumerate(ids)
        ]

    def _keyword_search(
        self, query_text: str, k: int, where: dict[str, Any] | None
    ) -> list[SearchHit]:
        with self._index_lock:
            if self._bm25 is None:
                self._rebuild_keyword_index()
            bm25 = self._bm25
            ids = list(self._ids)
            documents = list(self._documents)
            metadatas = list(self._metadatas)

        if bm25 is None:
            return []
        allowed_ids = self._matching_ids(where)
        scores = bm25.get_scores(self._tokens(query_text))
        ordered = sorted(
            (
                index
                for index, chunk_id in enumerate(ids)
                if allowed_ids is None or chunk_id in allowed_ids
            ),
            key=lambda index: (-float(scores[index]), ids[index]),
        )[:k]
        return [
            SearchHit(
                chunk_id=ids[index],
                content=documents[index],
                metadata=metadatas[index],
                score=float(scores[index]),
            )
            for index in ordered
        ]

    def _matching_ids(self, where: dict[str, Any] | None) -> set[str] | None:
        if not where:
            return None
        result = self.collection.get(where=where, include=["metadatas"])
        return set(result.get("ids", []))

    async def delete_source(self, source: str) -> int:
        return await asyncio.to_thread(self._delete_source_sync, source)

    def _delete_source_sync(self, source: str) -> int:
        matching = self.collection.get(
            where={"source": source}, include=["metadatas"]
        )
        ids = matching.get("ids", [])
        if ids:
            self.collection.delete(ids=ids)
            with self._index_lock:
                self._rebuild_keyword_index()
        return len(ids)

    async def stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> dict[str, Any]:
        result = self.collection.get(include=["metadatas"])
        sources = Counter(
            str(metadata.get("source") or "unknown")
            for metadata in result.get("metadatas", [])
        )
        return {
            "chunk_count": self.collection.count(),
            "sources": [
                {"source": source, "count": count}
                for source, count in sorted(sources.items())
            ],
        }

    def _rebuild_keyword_index(self) -> None:
        from rank_bm25 import BM25Okapi

        with self._index_lock:
            result = self.collection.get(include=["documents", "metadatas"])
            self._ids = list(result.get("ids", []))
            self._documents = [
                document or "" for document in result.get("documents", [])
            ]
            self._metadatas = [
                metadata or {} for metadata in result.get("metadatas", [])
            ]
            tokenized = [self._tokens(document) for document in self._documents]
            self._bm25 = BM25Okapi(tokenized) if any(tokenized) else None

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return [word.lower() for word in _WORD.findall(text)]

    @staticmethod
    def _chroma_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
        converted = {}
        for key, value in metadata.items():
            if value is None:
                converted[key] = ""
            elif isinstance(value, (str, int, float, bool)):
                converted[key] = value
            elif isinstance(value, list):
                converted[key] = ", ".join(str(item) for item in value)
            else:
                converted[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        return converted


vector_store = VectorStore()
