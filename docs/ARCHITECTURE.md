# Architecture

```text
React UI
  → FastAPI /api/test, /api/diagnose (SSE), /api/retest
  → SSRF guard + redaction + approval
  → HTTPX observation
  → query builder
  → local NumPy retriever or Supabase pgvector + FTS + RRF
  → Gemini → Groq → Mistral
  → citations + JSON patch
  → user approval
  → retest + diff
```

## Database schema

```sql
create extension if not exists vector;

create table if not exists kb_chunks (
  chunk_id text primary key,
  source_type text not null,
  source_url text not null,
  source_title text not null,
  api_name text,
  api_version text,
  method text,
  path text,
  section text,
  page int,
  license text,
  retrieved_at date,
  token_count int,
  chunk_text text not null,
  embedding vector(384) not null,
  embedding_model text not null,
  embedding_dim int not null,
  embedding_pipeline_version text not null,
  fts tsvector generated always as (to_tsvector('english', chunk_text)) stored
);

create index if not exists kb_chunks_embedding_idx
  on kb_chunks using hnsw (embedding vector_cosine_ops);

create index if not exists kb_chunks_fts_idx
  on kb_chunks using gin (fts);

create index if not exists kb_chunks_filter_idx
  on kb_chunks (api_name, api_version, method);
```

## Design decisions

| Decision | Choice | Why |
|---|---|---|
| Streaming | SSE over POST + fetch reader | `EventSource` cannot POST |
| Retrieval | Supabase pgvector primary; local `.npy` fallback | Demo survives network failure |
| Runs | SQLite | Fewer dependencies on demo day |
| LLM output | Markdown + `[KB-...]` + final JSON patch | Streams naturally and is machine-applicable |
| Approval | Method-based, server-enforced | Matches the deck's safety rule |
| Redirects | Not followed automatically | Preserves 3xx evidence and removes redirect SSRF |
