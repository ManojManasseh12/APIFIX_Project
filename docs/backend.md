# Backend — module by module

Layout (all imports control via `PYTHONPATH=backend`):

```
backend/
  server.py                 entry point (runs uvicorn on :8000)
  run_mock.py               demo mock API on :8001 (used by bundled presets)
  app/
    main.py                 FastAPI app factory: CORS, lifespan, /api routers
    core/                   config, security, models, tester, errors
    ai/                     LLM chain, prompts, RAG pipeline
    db/runs.py              run history dispatcher (Supabase / SQLite)
    routes/                 one router per feature, all mounted under /api
    utils/ids.py            id generation
  scripts/                  ingest.py, chunk_text.py, parse_openapi.py,
                            build_local_index.py, validate_dataset.py
  tests/                    pytest suite (80 tests)
  sql/schema.sql            Supabase DDL (kb_chunks, runs)
```

## core/ — the request engine

### `config.py` (Settings, pydantic-settings)
**What:** every tunable in one typed object: provider keys/models, provider
order, timeouts and retry backoff, `DATABASE_URL`, `RETRIEVAL_BACKEND`,
`ALLOWED_HOSTS`, upload limits, embedding model/dims.
**Why:** one source of truth; the LLM chain, stores and tester all read the
same settings instead of scattered env lookups.

### `security.py`
**What:** the three security primitives, folded into one module:
1. `check_url(url, allowed)` — SSRF guard: scheme allowlist, hostname
   allowlist (exact host match), DNS resolution + blocking of loopback,
   private, link-local, multicast and reserved ranges. `data/allowed_hosts.txt`
   (app-managed) is merged with the env allowlist on every check.
2. `redact()` / `REDACTED` — replaces token-looking header values with
   `<REDACTED>` so real secrets never reach the LLM, logs or history.
3. `require_approval(spec)` — state-changing methods (POST/PUT/PATCH/DELETE)
   must carry `approved=true`, which the frontend sets only after explicit
   user consent in the UI.
**Why:** the primary security story. The tool fetches arbitrary URLs by
design, so it *has* to defend against SSRF and so the LLM *never* sees real
credentials.

### `models.py`
**What:** all Pydantic schemas in one place — `RequestSpec`, `Observation`,
chunks, search hits, etc. (folded from `ai/rag/models.py`).
**Why:** single schema vocabulary across tester, retrieval, stores, routes
and the frontend's mirrored `types.ts`.

### `tester.py` — `execute(spec) -> Observation`
**What:** the real HTTP runner (async HTTPX):
- SSRF + DNS checks first → `blocked_by_policy`, `dns_error`
- TLS/connect/timeout guards → mapped error types with latency
- JSON or raw-string body support; **bodies are never attached to
  GET/HEAD/OPTIONS** (a stale UI body cannot leak into a bodyless request)
- response → `Observation` (status, selected headers, body_preview truncated,
  latency_ms, redirect_location)
**Why:** truth. Everything downstream (retrieval, diagnosis, history, UI)
consumes this exact observation object.

### `errors.py`
**What:** typed exceptions (`PolicyError`, `DNSError`, `ApprovalRequired`,
`AllProvidersFailed`, `StreamInterrupted`…).
**Why:** routes map error *types* to honest SSE events instead of generic 500s.

### `retest.py`
**What:** re-executes a patched spec and diffs against the original run
(status before/after, latency, verdict).
**Why:** the verify half of the loop — a fix counts only when the re-run
succeeds.

## ai/ — the intelligence layer

### `llm.py` — provider fallback chain
**What:** provider definitions for Groq, Gemini, Mistral, NVIDIA; expands
`PROVIDER_ORDER` into an attempt list (multiple keys/models per family are
rotated); `stream_with_fallback()` runs attempts first-serve with a
first-token timeout (default 20 s) and retries on 429/503/timeouts; NVIDIA
speaks the OpenAI-compatible endpoint via the OpenAI SDK.
**Why:** resilience and pedagogy. No single-vendor outage breaks the demo;
the header pill shows the real chain. Groq first because Gemini quota is
consumed by embeddings.

### `prompts.py`
**What:** SYSTEM_PROMPT (diagnosis format: OBSERVED FACTS → HYPOTHESES →
CONCLUSION, success-case rule), citation/patch parsers, ask-context builder.
**Why:** the LLM must answer *about this observation* with citations and a
machine-usable patch — the prompt and parsers are the contract with the UI.

### `rag/` — retrieval

| File | What | Why |
|---|---|---|
| `embedding.py` | Gemini embeddings (`gemini-embedding-001`, 768-d, batching) | One embedding family shared by both stores |
| `data_loader.py` | reads files from `data/`, picks parser by type, produces `Chunk`s; OpenAPI via `scripts/parse_openapi.py`, chunking via `scripts/chunk_text.py` | Live ingestion path used by /api/ingest |
| `batch_processor.py` | bounded-concurrency ingestion with per-file status | Failed files report specifics instead of crashing the batch |
| `vector_store.py` | local Chroma store + BM25 (fallback) | Offline/local mode without Postgres |
| `supabase_store.py` | pgvector store: upsert, delete-by-file, hybrid retrieve, KB stats | Production store; source-file attribution enables per-file cleanup |
| `fusion.py` | `weighted_rrf` + `dense_anchored_rrf` (dense weight 1.0, keyword 0.05, k=60, top-2 dense anchored) | Hybrid retrieval that fuses dense and BM25 ranks while protecting the best semantic hits (pure RRF lowered MRR) |
| `pipeline.py` | orchestrates ingest + retrieve; filters/scores | The seam between routes and storage |
| `retriever.py` | version detection & mismatch guard, local retriever variants | Prevents diagnosing v3 requests with v2 docs |
| `query.py` | `build_query(spec, observation)` — status-aware: 2xx queries "successful response 2xx semantics" and ignores stale failure-phrased questions | Fixed a real bug: stale preset questions polluted retrieval for successful calls |

## db/runs.py — history
**What:** dispatcher picking Supabase Postgres (DATABASE_URL) or SQLite
(data/runs.sqlite3); `initialize_async()` creates the table at startup;
`record/list` methods; save is best-effort.
**Why:** durable, server-side history (was in-browser only before); compared
with SQLite for offline runs.

## routes/ — the /api surface

| Route | Endpoint(s) | Purpose |
|---|---|---|
| `health.py` | GET /api/health | real stack facts (runner, retrieval backend, database, provider families) → frontend status pills |
| `test.py` | POST /api/test | bare observation without AI |
| `diagnose.py` | POST /api/diagnose | the SSE lifecycle: observe → retrieve → stream → patch → save |
| `retest.py` | POST /api/retest | apply patch (redaction-aware) and diff |
| `ask.py` | POST /api/ask | chat-style Q&A with KB citations |
| `runs.py` | GET /api/runs | recent history for the sidebar |
| `upload.py` | POST /api/upload, GET /api/files, DELETE /api/files/{name} | file management |
| `ingest.py` | POST /api/ingest, GET /api/kb/stats | chunk+embed uploaded docs; live KB stats |
| `search.py` | POST /api/search | direct KB search (hybrid modes) |
| `policy.py` | GET/POST /api/allowed-hosts | app-managed SSRF allowlist (no restart needed) |

## scripts/ — supporting runtime tools
- `ingest.py` — CLI reseed of the KB (the 91 bundled chunks live in
  `data/kb_chunks.jsonl`).
- `chunk_text.py`, `parse_openapi.py` — **runtime dependencies** of
  ingestion; chunk-by-tokens and OpenAPI → chunk conversion.
- `build_local_index.py` — builds the offline Chroma index.
- `validate_dataset.py` — checks KB integrity (IDs, required fields).

## tests/ — what is proven
- HTTP runner behavior incl. redaction and the no-body-GET guarantee
- Provider fallback chain (unit, env-driven, hermetic)
- Retrieval pipeline, fusion determinism, Supabase-store SQL paths
- Security: SSRF policy, allowlist API, approval gate
- Routes and lifecycle events, run history dispatcher
- `tests/conftest.py` forces hermetic settings (local retrieval, SQLite)
  so an developer's real keys in `.env` never leak into tests
