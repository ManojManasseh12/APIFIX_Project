import asyncio
import threading
from datetime import date
from typing import Any

from app.ai.rag.fusion import dense_anchored_rrf
from app.ai.rag.models import Chunk, SearchHit
from app.core.config import settings

_SELECT_COLUMNS = """
  chunk_id, chunk_text, source_file, source_title, source_url, section, page,
  api_name, api_version, method, path
"""

_UPSERT = """
insert into kb_chunks (
  chunk_id, source_type, source_url, source_title, api_name, api_version,
  method, path, section, page, license, retrieved_at, token_count,
  chunk_text, embedding, embedding_model, embedding_dim,
  embedding_pipeline_version, source_file
) values (
  %(chunk_id)s, %(source_type)s, %(source_url)s, %(source_title)s,
  %(api_name)s, %(api_version)s, %(method)s, %(path)s, %(section)s,
  %(page)s, %(license)s, %(retrieved_at)s, %(token_count)s,
  %(chunk_text)s, %(embedding)s, %(embedding_model)s, %(embedding_dim)s,
  %(embedding_pipeline_version)s, %(source_file)s
)
on conflict (chunk_id) do update set
  source_type = excluded.source_type,
  source_url = excluded.source_url,
  source_title = excluded.source_title,
  api_name = excluded.api_name,
  api_version = excluded.api_version,
  method = excluded.method,
  path = excluded.path,
  section = excluded.section,
  page = excluded.page,
  license = excluded.license,
  retrieved_at = excluded.retrieved_at,
  token_count = excluded.token_count,
  chunk_text = excluded.chunk_text,
  embedding = excluded.embedding,
  embedding_model = excluded.embedding_model,
  embedding_dim = excluded.embedding_dim,
  embedding_pipeline_version = excluded.embedding_pipeline_version,
  source_file = excluded.source_file
"""


class SupabaseStore:
    """pgvector-backed implementation of the RAG vector store interface."""

    def __init__(self, dsn: str | None = None):
        import psycopg
        from pgvector.psycopg import register_vector

        self._lock = threading.RLock()
        self.conn = psycopg.connect(
            dsn or settings.database_url,
            autocommit=True,
            connect_timeout=10,
        )
        register_vector(self.conn)

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

        rows = [self._row(chunk, vector) for chunk, vector in zip(chunks, vectors)]
        with self._lock, self.conn.cursor() as cur:
            cur.executemany(_UPSERT, rows)
        return len(chunks)

    def _row(self, chunk: Chunk, vector: list[float]) -> dict[str, Any]:
        from pgvector import Vector

        metadata = chunk.metadata or {}
        source_file = str(metadata.get("source") or metadata.get("file_path") or "")
        title = str(
            metadata.get("source_title") or chunk.title or source_file or chunk.id
        )
        page = metadata.get("page")
        if isinstance(page, str) and page.isdigit():
            page = int(page)
        elif not isinstance(page, int):
            page = None

        retrieved_at = metadata.get("retrieved_at") or date.today()
        token_count = metadata.get("token_count")

        return {
            "chunk_id": chunk.id,
            "source_type": str(metadata.get("source_type") or "upload"),
            "source_url": str(metadata.get("source_url") or ""),
            "source_title": title,
            "api_name": str(metadata.get("api_name") or ""),
            "api_version": str(metadata.get("api_version") or ""),
            "method": str(metadata.get("method") or ""),
            "path": str(metadata.get("path") or ""),
            "section": str(metadata.get("section") or ""),
            "page": page,
            "license": str(metadata.get("license") or ""),
            "retrieved_at": retrieved_at,
            "token_count": token_count if isinstance(token_count, int) else None,
            "chunk_text": chunk.content,
            "embedding": Vector(vector),
            "embedding_model": settings.embed_model,
            "embedding_dim": len(vector),
            "embedding_pipeline_version": "v1",
            "source_file": source_file,
        }

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

        api_name = (where or {}).get("api_name")
        # Supabase's OR-based FTS ranking is noisier than local BM25, so keep a
        # wider candidate pool for keyword to rescue a chunk outside the dense
        # top-k without displacing the anchored dense results.
        candidate_limit = max(k * 4, 40)

        if mode == "dense":
            rows = self._search_dense(query_vector, candidate_limit, api_name)
            return [self._hit(row, row["score"]) for row in rows[:k]]
        if mode == "keyword":
            rows = self._search_keyword(query_text, candidate_limit, api_name)
            return [self._hit(row, row["score"]) for row in rows[:k]]

        dense = self._search_dense(query_vector, candidate_limit, api_name)
        keyword = self._search_keyword(query_text, candidate_limit, api_name)

        fused = dense_anchored_rrf(
            [row["chunk_id"] for row in dense],
            [row["chunk_id"] for row in keyword],
            dense_weight=settings.rrf_dense_weight,
            keyword_weight=settings.rrf_keyword_weight,
            k=settings.rrf_k,
            dense_anchor=settings.rrf_dense_anchor,
        )

        records = {
            row["chunk_id"]: row
            for row in dense + keyword
        }
        return [
            self._hit(records[chunk_id], score)
            for chunk_id, score in fused[:k]
        ]

    def _search_dense(
        self, vector: list[float], limit: int, api_name: str | None
    ) -> list[dict[str, Any]]:
        from pgvector import Vector

        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                f"""
                select {_SELECT_COLUMNS},
                       1 - (embedding <=> %s) as score
                from kb_chunks
                where (%s::text is null or api_name = %s)
                  and embedding_model = %s
                  and embedding_dim = %s
                order by embedding <=> %s
                limit %s
                """,
                (
                    Vector(vector),
                    api_name,
                    api_name,
                    settings.embed_model,
                    settings.embed_dim,
                    Vector(vector),
                    limit,
                ),
            )
            columns = [column.name for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def _search_keyword(
        self, query: str, limit: int, api_name: str | None
    ) -> list[dict[str, Any]]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                f"""
                with terms as (
                  select string_agg(quote_literal(lexeme), ' | ') as query
                  from (
                    select distinct lexeme
                    from unnest(to_tsvector('english', %s)) as t(lexeme)
                  ) t
                ),
                q as (
                  select to_tsquery('english', query) as tsq
                  from terms
                  where query is not null
                )
                select {_SELECT_COLUMNS},
                       ts_rank_cd(fts, q.tsq) as score
                from kb_chunks, q
                where fts @@ q.tsq
                  and (%s::text is null or api_name = %s)
                  and embedding_model = %s
                  and embedding_dim = %s
                order by score desc
                limit %s
                """,
                (
                    query,
                    api_name,
                    api_name,
                    settings.embed_model,
                    settings.embed_dim,
                    limit,
                ),
            )
            columns = [column.name for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    @staticmethod
    def _hit(row: dict[str, Any], score: float) -> SearchHit:
        return SearchHit(
            chunk_id=row["chunk_id"],
            content=row.get("chunk_text") or "",
            metadata={
                "source": row.get("source_file") or row.get("source_title") or "",
                "source_title": row.get("source_title") or "",
                "source_url": row.get("source_url") or "",
                "section": row.get("section") or "",
                "page": row.get("page") if row.get("page") is not None else "",
                "api_name": row.get("api_name") or "",
                "api_version": row.get("api_version") or "",
                "method": row.get("method") or "",
                "path": row.get("path") or "",
            },
            score=float(score or 0.0),
        )

    async def delete_source(self, source: str) -> int:
        return await asyncio.to_thread(self._delete_source_sync, source)

    def _delete_source_sync(self, source: str) -> int:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                "delete from kb_chunks where source_file = %s", (source,)
            )
            return cur.rowcount or 0

    async def stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> dict[str, Any]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                select coalesce(
                         nullif(source_file, ''),
                         nullif(source_title, ''),
                         'unknown'
                       ) as source,
                       count(*)
                from kb_chunks
                group by 1
                order by 1
                """
            )
            rows = cur.fetchall()
        return {
            "chunk_count": sum(row[1] for row in rows),
            "sources": [
                {"source": row[0], "count": row[1]} for row in rows
            ],
        }
