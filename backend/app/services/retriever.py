import json
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_MODEL = None


@dataclass
class RetrievalResult:
    chunks: list[dict] = field(default_factory=list)
    version_mismatch: bool = False
    other_versions: list[str] = field(default_factory=list)
    insufficient: bool = False
    query: str = ""


def _model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer

        _MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _MODEL


def detect_version(path: str) -> str | None:
    for part in (path or "").split("/"):
        if len(part) > 1 and part[0] == "v" and part[1:].isdigit():
            return part
    return None


def rrf(rank_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for rank, chunk_id in enumerate(ranks, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: -pair[1])


def _version_check(rows: list[dict], requested_path: str):
    requested = detect_version(requested_path)
    api_rows = [row for row in rows if row.get("path")]
    if not requested or not api_rows:
        return False, []
    versions = {detect_version(row["path"]) for row in api_rows}
    if requested not in versions:
        return True, sorted(v for v in versions if v)
    return False, []


class LocalRetriever:
    def __init__(self):
        import numpy as np

        self.np = np
        meta_path = DATA_DIR / "kb_meta.json"
        vec_path = DATA_DIR / "kb_embeddings.npy"
        self.rows = json.load(open(meta_path)) if meta_path.exists() else []
        self.vectors = np.load(vec_path) if vec_path.exists() else None

    def search(self, query: str, api_name: str | None = None, path: str = "") -> RetrievalResult:
        if self.vectors is None or not self.rows:
            return RetrievalResult(insufficient=True, query=query)
        vector = _model().encode([query], normalize_embeddings=True)[0]
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
                order by embedding <=> %s
                limit 20
                """,
                (Vector(vector), api_name, api_name, Vector(vector)),
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
                order by score desc
                limit 20
                """,
                (query, query, api_name, api_name),
            )
            columns = [c.name for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def search(self, query: str, api_name: str | None = None, path: str = "") -> RetrievalResult:
        vector = _model().encode([query], normalize_embeddings=True)[0]
        dense = self._search_dense(vector, api_name)
        keyword = self._search_keyword(query, api_name)

        mismatch, versions = _version_check(dense + keyword, path)
        if mismatch:
            return RetrievalResult(
                version_mismatch=True, other_versions=versions, query=query
            )

        fused = rrf(
            [[row["chunk_id"] for row in dense], [row["chunk_id"] for row in keyword]]
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
    if settings.retrieval_backend == "supabase" and settings.database_url:
        try:
            _RETRIEVER = SupabaseRetriever()
            return _RETRIEVER
        except Exception:
            pass
    _RETRIEVER = LocalRetriever()
    return _RETRIEVER
