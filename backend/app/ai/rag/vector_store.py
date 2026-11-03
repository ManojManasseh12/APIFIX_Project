import asyncio
import json
import logging
import re
import threading
from collections import Counter
from typing import Any

from app.ai.rag.fusion import dense_anchored_rrf
from app.ai.rag.models import Chunk, SearchHit
from app.core.config import settings

_WORD = re.compile(r"\w+", flags=re.UNICODE)


def collection_name(model: str | None = None, dimensions: int | None = None) -> str:
    """One Chroma collection per embedding model/dimension pair."""
    model_name = (model or settings.embed_model).lower()
    dims = dimensions or settings.embed_dim
    slug = re.sub(r"[^a-z0-9]+", "_", model_name).strip("_")
    return f"apifix_docs_{slug}_{dims}"[:63].rstrip("_")


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
                        name=collection_name(),
                        configuration={"hnsw": {"space": "cosine"}},
                        metadata={
                            "embedding_model": settings.embed_model,
                            "embedding_dim": settings.embed_dim,
                        },
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
        for vector in vectors:
            if len(vector) != settings.embed_dim:
                raise ValueError(
                    f"Expected {settings.embed_dim}-dimensional vectors from "
                    f"{settings.embed_model}, received {len(vector)}."
                )

        self.collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            metadatas=[
                self._chroma_metadata(
                    {
                        **chunk.metadata,
                        "embedding_model": settings.embed_model,
                        "embedding_dim": settings.embed_dim,
                    }
                )
                for chunk in chunks
            ],
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

        self._check_embedding_compatibility()

        if mode == "dense":
            return self._dense_search(query_vector, k, where)[:k]
        if mode == "keyword":
            return self._keyword_search(query_text, k, where)[:k]
        # Pull a wider candidate pool so keyword can rescue a chunk that is
        # just outside the dense top-k, then anchor the best dense results.
        candidate_limit = max(k * 4, 20)
        dense = self._dense_search(query_vector, candidate_limit, where)
        keyword = self._keyword_search(query_text, candidate_limit, where)

        fused = dense_anchored_rrf(
            [hit.chunk_id for hit in dense],
            [hit.chunk_id for hit in keyword],
            dense_weight=settings.rrf_dense_weight,
            keyword_weight=settings.rrf_keyword_weight,
            k=settings.rrf_k,
            dense_anchor=settings.rrf_dense_anchor,
        )
        records = {hit.chunk_id: hit for hit in dense + keyword}
        return [
            records[chunk_id].model_copy(update={"score": score})
            for chunk_id, score in fused[:k]
        ]

    def _check_embedding_compatibility(self) -> None:
        metadata = getattr(self.collection, "metadata", None) or {}
        model = metadata.get("embedding_model")
        dimensions = metadata.get("embedding_dim")
        if model and str(model) != settings.embed_model:
            raise RuntimeError(
                f"Chroma collection was built with embedding model {model!r}, "
                f"but the app is configured for {settings.embed_model!r}. "
                "Rebuild the collection or restore the original EMBED_MODEL."
            )
        if dimensions is not None and int(dimensions) != settings.embed_dim:
            raise RuntimeError(
                f"Chroma collection was built with {dimensions}-dimensional "
                f"vectors, but EMBED_DIM is {settings.embed_dim}. Rebuild the "
                "collection or restore the original EMBED_DIM."
            )

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

_logger = logging.getLogger(__name__)


def get_store():
    """Return the configured retrieval store.

    Supabase is used when ``RETRIEVAL_BACKEND=supabase`` and ``DATABASE_URL``
    is set to a non-placeholder value. If the database cannot be reached (or
    the schema is missing), the app logs a warning and keeps working against the
    local Chroma index.
    """
    if (
        settings.retrieval_backend.strip().lower() == "supabase"
        and settings.database_url_configured
    ):
        try:
            from app.ai.rag.supabase_store import SupabaseStore

            _logger.info("Using Supabase pgvector retrieval backend")
            return SupabaseStore()
        except Exception as exc:  # pragma: no cover - depends on external DB
            _logger.warning(
                "Supabase retrieval backend unavailable (%s); falling back to "
                "the local Chroma index",
                exc,
            )
    return vector_store
