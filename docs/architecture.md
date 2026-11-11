# Architecture — components, connectivity, request lifecycles

## 1. Components and connectivity

```
┌─────────────────────────────┐
│  Browser (React + Vite)     │  localhost:5173 (dev)
│  frontend/src               │
└──────────────┬──────────────┘
               │  fetch / EventSource-style SSE, /api/*
               │  (vite.config.ts proxies /api/ → http://localhost:8000)
┌──────────────▼──────────────┐
│  FastAPI backend            │  127.0.0.1:8000, started by server.py
│  backend/app                │  routes/* mount routers under /api
└───┬──────────┬──────────┬───┘
    │          │          │
    │          │          └──────────────────────┐
    ▼          ▼                                     ▼
┌──────────┐ ┌───────────────────────┐  ┌────────────────────────────┐
│ Target   │ │ Supabase Postgres     │  │ LLM providers (fallback    │
│ API under│ │ (DATABASE_URL)        │  │ chain, first-serve):       │
│ test     │ │ - kb_chunks (pgvector)│  │ Groq → Gemini → Mistral →  │
│ (httpx)  │ │ - runs (history)      │  │ NVIDIA (OpenAI-compatible) │
└──────────┘ └───────────────────────┘  └────────────────────────────┘
```

- **Browser ⇄ backend:** everything goes through `/api/...`. Vite's dev proxy
  (`/api/`) makes the frontend origin-agnostic; production checks use
  `vite preview` with the same proxy.
- **Backend ⇄ target API:** `app.core.tester.execute()` sends the real
  request with async HTTPX (`trust_env=False` so proxy env vars cannot
  silently reroute traffic).
- **Backend ⇄ Supabase Postgres:** psycopg connections. Two concerns:
  `kb_chunks` (knowledge base, pgvector) and `runs` (diagnosis history).
  - Fallbacks: no `DATABASE_URL` → retrieval uses a local Chroma store
    (`RETRIEVAL_BACKEND=local`), run history uses local SQLite.
- **Backend ⇄ LLM providers:** `app.ai.llm` builds a provider chain in the
  configured order (`PROVIDER_ORDER=groq,gemini,mistral,nvidia`). Each
  attempt is bounded by a first-token timeout; failures fall through to the
  next provider (rate limits: 429,503; errors; stalls).

## 2. Request lifecycles

### 2.1 Run test + live diagnosis (`POST /api/diagnose`, SSE stream)

This is the primary loop. One call does the test AND the diagnosis:

```
Browser ── POST /api/diagnose (RequestSpec JSON) ──▶ FastAPI
   │                                                    │
   │                          1. require_approval(spec)  ← state-changing methods
   │                              (POST/PUT/…) need approved=true; without it
   │                              an approval_required SSE event is sent and
   │                              the UI asks for consent
   │                                                    │
   │                          2. execute(spec)           ← async HTTPX, SSRF
   │   ◀── event: observation           (policy/DNS/SSL/timeout guarded)
   │       (status, headers, body_preview, latency…)    │
   │                                                    │
   │                          3. build_query(spec, obs)  ← status-aware:
   │                                                     │  2xx → search
   │                          4. retrieve_for_api(query) │  "successful response
   │   ◀── event: retrieval        hybrid+RRF, k=6       │  semantics"; failures
   │       (chunk ids)                                   │  include the status
   │                                                     │
   │                          5. stream_with_fallback(   │
   │                               providers first-serve)│
   │   ◀── event: token ……streamed……                     │
   │                                                     │
   │                          6. parse PATCH from tokens │  (json patch block)
   │   ◀── event: patch + citations                      │
   │                          7. save run → runs table   │  (never raises:
   │                                                     │   history failure
   │                                                     │   cannot kill the stream)
```

- `GET /api/test` style quick testing also exists (`POST /api/test`) for a
  bare observation without AI (used when the run itself is enough).

### 2.2 Fix-and-re-verify loop (`POST /api/retest`)

1. The diagnosis ends with a `json {patch…}` block → the UI shows
   "SUGGESTED FIX" with Edit JSON.
2. If headers contain `<REDACTED>`, the UI locks "Apply fix and re-run"
   until real values are pasted (they are substituted only in the outgoing
   retest request — nothing persists them).
3. `onApplyFix` → `POST /api/retest` with the patched spec.
4. The retest re-executes the request and returns a diff:
   `status_before → status_after`, latency, verdict pass/fail.
5. The UI shows the "Verified ✓" pill only if the retest succeeded.

### 2.3 Ask API (`POST /api/ask`)

Chat-style tab. Each turn:

1. The question + last 4 conversation messages are kept.
2. Retrieval runs against the same KB (hybrid, mode-aware).
3. The LLM answers citing `chunk_id`s; the UI renders SOURCES chips with
   links (`source_url` when available).

### 2.4 Import OpenAPI spec (upload → ingest → presets)

```
Browser: file input (JSON/YAML/YML)
  1. POST /api/upload  → data/uploads/<name>-<hash>.<ext>  (size/ext validated, name sanitized)
  2. POST /api/ingest  → parse (scripts.parse_openapi) → chunk (scripts.chunk_text)
                         → embed (Gemini) → insert into kb_chunks (Supabase or local)
  3. GET  /api/files   → file list for the "My own API" collection
  4. Browser parses JSON specs → endpoint presets (base URL from servers[0],
     path parameters filled with "1") → clickable in the sidebar
  5. GET /api/kb/stats → live chunk count for the "Bundled API docs" card
  6. DELETE /api/files/{name} → removes the file AND its chunks
```

After ingest, diagnoses and Ask answers can cite chunks from the imported
spec — "your own API becomes a first-class KB citizen".

### 2.5 Run history (`GET /api/runs`)

`db/runs.py` is a dispatcher:

- `DATABASE_URL` set → Supabase Postgres table `runs`
  (auto-created at startup; also in `backend/sql/schema.sql`)
- otherwise → SQLite at `data/runs.sqlite3`

Saves: run_id, created_at, method, url, status, error_type, retrieved_ids,
provider (which LLM served it), diagnosis, patch. `save()` never raises — a
history failure cannot break the streamed diagnosis. The sidebar "Recent
Tests" renders this live, newest first, with relative timestamps.

## 3. Ports and processes

| Process | Port | Started by |
|---|---|---|
| FastAPI backend | 8000 | `python server.py` (repo root; requires `PYTHONPATH=backend`) |
| Vite dev server | 5173 | `npm run dev` (only ONE dev server per project — a second instance causes stale-cache symptoms) |
| Mock API (demo presets) | 8001 | `python run_mock.py` |

## 4. Configuration surface (.env)

| Variable | Effect |
|---|---|
| `GROQ_API_KEY`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `NVIDIA_API_KEY` | provider keys (multiple comma-separated keys/models are rotated through) |
| `PROVIDER_ORDER` | fallback chain order; empty `NVIDIA_MODEL` disables a provider |
| `LLM_FIRST_TOKEN_TIMEOUT_S` | stall bound per attempt (default 20 s) |
| `DATABASE_URL` | Supabase Postgres; unset → SQLite history + local Chroma retrieval |
| `RETRIEVAL_BACKEND` | `supabase` (pgvector) or `local` (Chroma) |
| `ALLOWED_HOSTS` | SSRF allowlist seed list, merged with the app-managed `data/allowed_hosts.txt` |
| `MAX_UPLOAD_MB` | upload size cap |
