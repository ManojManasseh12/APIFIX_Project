-- APIFix AI — upgrade the knowledge base from MiniLM (384-d) to Gemini (768-d)
--
-- Run this once in Supabase Dashboard → SQL Editor if you already created
-- kb_chunks with the old 384-dimensional embedding column.
--
-- MiniLM vectors cannot be reused by gemini-embedding-001, so this clears the
-- table. Re-ingest afterwards:
--   PYTHONPATH=backend python backend/scripts/ingest.py
-- and re-upload/re-ingest any user documents.

begin;

-- Existing rows contain 384-d MiniLM vectors.
delete from kb_chunks;

-- The HNSW index is dimension-specific, so rebuild it around the type change.
drop index if exists kb_chunks_embedding_idx;

alter table kb_chunks
  alter column embedding type vector(768);

create index if not exists kb_chunks_embedding_idx
  on kb_chunks using hnsw (embedding vector_cosine_ops);

commit;
