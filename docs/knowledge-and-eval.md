# Knowledge base, retrieval, and evaluation

## 1. The knowledge base (KB)

**Canonical source:** `data/kb_chunks.jsonl` — 91 chunks, seeded into the
store with `backend/scripts/ingest.py`. Each chunk carries:

- `chunk_id` — stable hash prefixed IDs, e.g. `KB-MDN-401`,
  `KB-CONCEPT-2XX`, `KB-SEC-SSRF`, `KB-API-GH-ISSUES-CREATE`
- `chunk_text` — a self-contained documentation snippet
- provenance: `source_title`, `source_url`, `section`, `api_name`,
  `method`, `path` — chunk IDs surface in the UI as citation chips with
  real links when available

**Content families:**

| Family | What it holds |
|---|---|
| `KB-MDN-*` | HTTP status semantics from MDN (200, 201, 422, 401, 403, 405, …) |
| `KB-RFC-*` | RFC 9110 sections (authentication challenge, 403 semantics) |
| `KB-CONCEPT-*` | Concept pages: status families, why a 422 is/never is the answer, 2xx success semantics |
| `KB-SEC-*` | The platform's own security docs (SSRF policy), so the app can diagnose itself |
| `KB-API-*` | Per-API reference: GitHub REST operations, Swagger Petstore operations |
| hash-prefixed chunks (`KB-…sha…`) | chunks ingested from uploaded user specs |

**Why a curated JSONL:** reproducible seeds for demo and tests, plus it is
versioned in git (unlike operator uploads). User-imported specs (upload →
ingest) add *additional* chunks attributed by `source_file`, so deleting the
file deletes exactly its chunks.

## 2. Ingestion pipeline (`POST /api/ingest`, scripts/ingest.py)

```
file → parse (parse_openapi.py for specs; text/pdf/html/md/jsonl readers)
     → chunk_by_tokens (chunk_text.py, token-bounded overlapping windows)
     → embed (Gemini gemini-embedding-001, 768-d, batched, RETRIEVAL_DOCUMENT)
     → store upsert (Supabase pgvector | local Chroma)
     → per-file status report (chunks_added / failure reason)
```

`GET /api/kb/stats` reports the live totals the sidebar shows ("91 chunks
indexed"). Deleting a file (`DELETE /api/files/{name}`) removes the file and
its chunks, then stats refresh.

## 3. Retrieval — hybrid search fused by RRF

Every diagnosis, Ask answer, and `/api/search` call runs
`pipeline.retrieve(question, mode="hybrid", k=6)`:

1. **Query building is status-aware** (`rag/query.py`): for a 2xx response
   the query is phrased as "successful response 2xx semantics" and stale
   failure-phrased preset questions are ignored — this fixed the real bug
   where a canned 422 question polluted successful diagnoses.
2. **Dense retrieval**: embed the query → top-k by cosine in pgvector (or
   Chroma).
3. **Keyword retrieval**: BM25-style lexical scoring.
4. **Fusion** (`rag/fusion.py`): `dense_anchored_rrf`
   - `score(d) = Σ weight_i / (60 + rank_i)` across the two rankings,
     dense weight 1.0, keyword weight 0.05
   - the top-2 dense results are *anchored* in front, then the fused order
     follows; keyword-only hits can still rescue chunks dense missed
   - why: pure RRF let a strong keyword match displace the best semantic
     hit and hurt MRR; anchoring fixed it.
5. **Guards** (`rag/retriever.py`): below-score-threshold → "insufficient
   evidence"; version detection — a `v3` path queried against `v2` docs gets
   flagged as a version mismatch instead of silently mixing docs.
6. **Cutoff**: only real hits are surfaced; the UI shows every source
   actually used.

**Search modes** on `POST /api/search`: `dense`, `keyword`-style and
`hybrid` (default in the app).

## 4. Evaluation — measure before improving

`eval/` holds the harness and the labelled set:

- `eval_questions.jsonl` — **50 labelled questions** (`case_id`, `question`,
  `expected_chunk_ids`), e.g. `AUTH-001: 401 header omitted → KB-MDN-401`
- `case_requests.json`, `evidence_map.json` — the request fixtures and the
  chunk ground truth
- `retrieval_eval.py` — embeds/batches questions, retrieves with the
  configured mode, scores hit@k and MRR against expected chunks
- `run_eval.py` — the CLI that produces `eval/results/*.jsonl` reports

**Why:** the project's discipline line is "do not add a reranker or extra
infrastructure before the baseline is measured." This harness *is* the
baseline. Running it after any retrieval change (weights, chunking, KB
additions) is the regression test for relevance.

```
PYTHONPATH=backend:. .venv/bin/python eval/run_eval.py
```

(Results land in `eval/results/`, gitignored.)

## 5. Extending the KB

1. **Bundled content:** edit/append valid chunk records to
   `data/kb_chunks.jsonl` (stable IDs, provenance fields) →
   `PYTHONPATH=backend:. .venv/bin/python backend/scripts/ingest.py`
   → confirm with `/api/kb/stats`.
2. **Per-API docs:** import an OpenAPI/JSON spec from the UI (or drop files
   in `data/uploads` and call ingest) — chunks become attributed to that
   file and citable as KB-… .
3. **Never hand-edit chunk text without re-ingesting** — the store, not the
   JSONL, serves retrieval.
