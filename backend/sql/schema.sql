-- APIFix AI — Supabase / pgvector schema
-- Run this once in Supabase Dashboard → SQL Editor.
--
-- Embeddings from Google gemini-embedding-001 are 768-dimensional.
-- If you change EMBED_MODEL/EMBED_DIM, change vector(768) to the new dimension
-- and re-ingest every chunk. See migrate_embedding_384_to_768.sql when
-- upgrading an existing MiniLM (384-d) table.

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
  embedding vector(768) not null,
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
alter table kb_chunks add column if not exists embedding_model text;
alter table kb_chunks add column if not exists embedding_dim int;
alter table kb_chunks add column if not exists embedding_pipeline_version text;
alter table kb_chunks add column if not exists source_file text;

create index if not exists kb_chunks_embedding_idx
  on kb_chunks using hnsw (embedding vector_cosine_ops);

create index if not exists kb_chunks_fts_idx
  on kb_chunks using gin (fts);

create index if not exists kb_chunks_filter_idx
  on kb_chunks (api_name, api_version, method);

create index if not exists kb_chunks_source_file_idx
  on kb_chunks (source_file);

-- Diagnosis run history. The backend also creates this automatically at
-- startup (create table if not exists), so running it here is optional.
create table if not exists runs (
  run_id text primary key,
  created_at timestamptz not null default now(),
  user_id text,
  method text,
  url text,
  status int,
  error_type text,
  retrieved_ids jsonb not null default '[]'::jsonb,
  provider text,
  diagnosis text,
  patch jsonb
);

-- Rows saved before authentication have user_id NULL; the backend treats
-- them as legacy data owned by the project owner.
alter table runs add column if not exists user_id text;
create index if not exists runs_user_id_idx on runs (user_id);

-- Ask API chat history (one row per answered question). Also auto-created
-- at startup; see app/db/ask_chats.py.
create table if not exists ask_turns (
  turn_id text primary key,
  created_at timestamptz not null default now(),
  user_id text,
  session_id text,
  question text,
  answer text,
  provider text,
  tool text,
  tool_label text,
  citations jsonb not null default '[]'::jsonb
);

alter table ask_turns add column if not exists user_id text;
create index if not exists ask_turns_user_id_idx on ask_turns (user_id);
