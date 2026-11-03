import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.ai.rag.embedding import embedding
from app.ai.rag.fusion import dense_anchored_rrf
from app.core.config import settings

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[4] / "data"


@dataclass
class RetrievalResult:
    chunks: list[dict] = field(default_factory=list)
    version_mismatch: bool = False
    other_versions: list[str] = field(default_factory=list)
    insufficient: bool = False
    query: str = ""


def detect_version(path: str) -> str | None:
    for part in (path or "").split("/"):
        if len(part) > 1 and part[0] == "v" and part[1:].isdigit():
            return part
    return None


def _version_check(rows: list[dict], requested_path: str):
    requested = detect_version(requested_path)
    if not requested:
        return False, []
    versions = {
        detect_version(row["path"])
        for row in rows
        if row.get("path") and detect_version(row["path"])
    }
    if not versions:
        return False, []
    if requested not in versions:
        return True, sorted(v for v in versions if v)
    return False, []


class LocalRetriever:
    """NumPy index over ``data/kb_embeddings.npy`` (Gemini vectors)."""

    def __init__(self):
        import numpy as np

        self.np = np
        meta_path = DATA_DIR / "kb_meta.json"
        vec_path = DATA_DIR / "kb_embeddings.npy"
        self.rows = json.load(open(meta_path)) if meta_path.exists() else []
        if vec_path.exists():
            vectors = np.load(vec_path).astype(float)
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self.vectors = vectors / norms
        else:
            self.vectors = None

    async def search(
        self, query: str, api_name: str | None = None, path: str = ""
    ) -> RetrievalResult:
        if self.vectors is None or not self.rows:
            return RetrievalResult(insufficient=True, query=query)

        vector = self.np.asarray(
            await embedding.embed_prompt(query), dtype=float
        )
        if self.vectors.shape[1] != vector.shape[0]:
            raise RuntimeError(
                f"Local index has {self.vectors.shape[1]}-dimensional vectors but "
                f"{settings.embed_model} returns {vector.shape[0]}-dimensional "
                "vectors. Rebuild it with backend/scripts/build_local_index.py."
            )

        norm = self.np.linalg.norm(vector)
        if norm:
            vector = vector / norm

        scores = self.vectors @ vector
        order = self.np.argsort(-scores)[:20]
        rows = [dict(self.rows[i], score=float(scores[i])) for i in order]
        if api_name:
            rows = [row for row in rows if row.get("api_name") == api_name]

        mismatch, versions = _version_check(rows, path)
        if mismatch:
            return RetrievalResult(
                version_mismatch=True, other_versions=versions, query=query
            )

        top = rows[:6]
        if not top or top[0]["score"] < 0.15:
            return RetrievalResult(insufficient=True, query=query)
        return RetrievalResult(chunks=top, query=query)


class SupabaseRetriever:
    """pgvector retriever used when RETRIEVAL_BACKEND=supabase."""

    def __init__(self):
        import psycopg
        from pgvector.psycopg import register_vector

        self.conn = psycopg.connect(settings.database_url, autocommit=True)
        register_vector(self.conn)

    def _search_dense(self, vector, api_name):
        from pgvector import Vector

        with self.conn.cursor() as cur:
            cur.execute(
                """
                select chunk_id, chunk_text, source_url, source_title, section, page,
                       api_name, api_version, method, path,
                       1 - (embedding <=> %s) as score
                from kb_chunks
                where (%s is null or api_name = %s)
                  and embedding_model = %s
                  and embedding_dim = %s
                order by embedding <=> %s
                limit 20
                """,
                (
                    Vector(vector),
                    api_name,
                    api_name,
                    settings.embed_model,
                    settings.embed_dim,
                    Vector(vector),
                ),
            )
            columns = [c.name for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def _search_keyword(self, query, api_name):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select chunk_id, chunk_text, source_url, source_title, section, page,
                       api_name, api_version, method, path,
                       ts_rank_cd(fts, websearch_to_tsquery('english', %s)) as score
                from kb_chunks
                where fts @@ websearch_to_tsquery('english', %s)
                  and (%s is null or api_name = %s)
                  and embedding_model = %s
                  and embedding_dim = %s
                order by score desc
                limit 20
                """,
                (
                    query,
                    query,
                    api_name,
                    api_name,
                    settings.embed_model,
                    settings.embed_dim,
                ),
            )
            columns = [c.name for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    async def search(
        self, query: str, api_name: str | None = None, path: str = ""
    ) -> RetrievalResult:
        vector = await embedding.embed_prompt(query)
        return await asyncio.to_thread(
            self._search_sync, vector, query, api_name, path
        )

    def _search_sync(
        self, vector: list[float], query: str, api_name: str | None, path: str
    ) -> RetrievalResult:
        dense = self._search_dense(vector, api_name)
        keyword = self._search_keyword(query, api_name)

        mismatch, versions = _version_check(dense + keyword, path)
        if mismatch:
            return RetrievalResult(
                version_mismatch=True, other_versions=versions, query=query
            )

        fused = dense_anchored_rrf(
            [row["chunk_id"] for row in dense],
            [row["chunk_id"] for row in keyword],
            dense_weight=settings.rrf_dense_weight,
            keyword_weight=settings.rrf_keyword_weight,
            k=settings.rrf_k,
            dense_anchor=settings.rrf_dense_anchor,
        )
        by_id = {row["chunk_id"]: row for row in dense + keyword}
        top = [by_id[cid] | {"rrf_score": score} for cid, score in fused[:6]]
        if not top:
            return RetrievalResult(insufficient=True, query=query)
        return RetrievalResult(chunks=top, query=query)


_RETRIEVER = None


def get_retriever():
    global _RETRIEVER
    if _RETRIEVER is not None:
        return _RETRIEVER
    backend = settings.retrieval_backend.strip().lower()
    if backend == "supabase" and settings.database_url_configured:
        try:
            _RETRIEVER = SupabaseRetriever()
            return _RETRIEVER
        except Exception:
            logger.warning(
                "Supabase retriever unavailable; using the local NumPy index",
                exc_info=True,
            )
    _RETRIEVER = LocalRetriever()
    return _RETRIEVER
