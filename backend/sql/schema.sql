-- APIFix AI — Supabase / pgvector schema
-- Run this once in Supabase Dashboard → SQL Editor.
--
-- Embeddings from sentence-transformers/all-MiniLM-L6-v2 are 384-dimensional.
-- If you change EMBED_MODEL, change vector(384) to the new dimension and
-- re-ingest every chunk.

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
  -- File name used by the upload/delete UI. Nullable for rows inserted by SQL.
  source_file text,
  fts tsvector generated always as (
    to_tsvector('english', chunk_text)
  ) stored
);

-- Upgrade an existing table created from an older copy of docs/ARCHITECTURE.md.
alter table kb_chunks add column if not exists source_file text;

create index if not exists kb_chunks_embedding_idx
  on kb_chunks using hnsw (embedding vector_cosine_ops);

create index if not exists kb_chunks_fts_idx
  on kb_chunks using gin (fts);

create index if not exists kb_chunks_filter_idx
  on kb_chunks (api_name, api_version, method);

create index if not exists kb_chunks_source_file_idx
  on kb_chunks (source_file);
