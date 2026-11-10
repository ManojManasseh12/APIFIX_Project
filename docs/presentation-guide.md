# Presentation guide — explain the project, file by file

Use this sheet to walk **anyone** (reviewer, interviewer, teammate) through the
codebase in one pass. Every function name below is real and verifiable in the
code.

## The 30-second pitch (memorize this)

> "APIFix AI is a documentation-grounded API debugger. You send a real HTTP
> request, the system captures the real response, retrieves matching
> documentation from a vector knowledge base, streams an AI diagnosis where
> every claim cites a chunk ID, proposes a JSON patch, and re-runs the fixed
> request — the 'Verified' badge only appears after a real re-test passes."

The differentiator vs Postman: Postman *executes* requests; it never
*explains* or *verifies* anything.

## The demo flow (what to click, in order)

| # | Action | What to point at |
|---|---|---|
| 1 | Header pills | "Runner / Search / Model chain — fetched live from `/api/health`, not hardcoded." |
| 2 | Run `GET http://localhost:8001/v1/profile` | Real 401 streamed into the Observation card. |
| 3 | Diagnosis streams | "Every sentence will cite a KB-… chunk." Point at SOURCES chips + the "served by …" provider pill. |
| 4 | SUGGESTED FIX | "The patch is editable. The token is `<REDACTED>` — I never send real keys to the LLM." |
| 5 | Paste token → Apply fix and re-run | Retest diff: 401 → 200, "Verified ✓" pill. |
| 6 | Sidebar Recent Tests | "History is a real database table (Postgres or SQLite), rendered live." |
| 7 | Ask API tab | "Same knowledge base, same citations, chat-style." |

## Backend — layer by layer

### Entry point

- **`server.py`** — starts uvicorn on :8000 with `PYTHONPATH=backend`. One line, five words: "thin launcher; all logic lives in `app/`."

### `backend/app/core/` — engine + safety

| File | Function | What it does — the line to say |
|---|---|---|
| `models.py` | `RequestSpec`, `Observation`, `Chunk`, … | "All Pydantic schemas in one file — the *contract* shared by tester, retrieval, routes and the UI's `types.ts`." |
| `security.py` | `check_url(raw_url, allowed)` | "The SSRF gate. 3 layers: scheme must be http(s) → hostname must be on an exact allowlist → every DNS-resolved IP must be public (loopback/private/link-local ranges refused, so DNS rebinding is useless)." |
| | `load_allowed_hosts()` / `trust_host(raw_host)` | "Merges an env allowlist with an app-managed `data/allowed_hosts.txt` on every check — so the UI's **Allow host** button trusts a new host without a restart." |
| | `redact_headers()` / `redact_text()` | "Replaces secret-shaped values with `<REDACTED>` *before* anything reaches the LLM, logs, or history." |
| | `require_approval(spec)` | "POST/PUT/PATCH/DELETE need explicit user consent (`approved: true`) — the UI asks before any state-changing call." |
| `errors.py` | `PolicyError`, `DNSError`, `ApprovalRequired`, `AllProvidersFailed` | "Typed errors so routes map error *types* into honest SSE events instead of generic 500s." |
| `tester.py` | `execute(spec) -> Observation` | "The truth engine. Async HTTPX with `trust_env=False` (no silent proxy), timeouts, redirect-following off. GET/HEAD never get a body. Everything downstream observes — nothing is simulated." |
| `retest.py` | `apply_patch(spec, patch)` / `diff(before, after)` | "The verify half: swaps patched fields into a fresh spec (with un-redacted secrets only in-memory for this call), re-executes, and returns a before/after diff with a pass/fail verdict." |

### `backend/app/ai/` — intelligence

| File | Function | Line to say |
|---|---|---|
| `llm.py` | `get_providers()` | "Reads `PROVIDER_ORDER` (groq,gemini,mistral,nvidia), expands comma-separated keys/models into an ordered attempt list. Failures never skip the gates." |
| | `stream_with_fallback(...)` | "Runs providers first-serve with a **first-token timeout** (~20s). A stall, 429/503, or auth error falls through to the next provider *before* the user sees anything. Each `provider` flip is streamed as an SSE event." |
| | `GeminiProvider / GroqProvider / MistralProvider / NvidiaProvider` | "Each wraps `stream(system, user)` and reports when it's `available` — same interface, swappable vendors. That's why a vendor outage can't break the demo." |
| `prompts.py` | `build_context(spec, obs, retrieval)` | "Assembles the only context the LLM ever gets: the redacted spec, the observed response, and the retrieved evidence chunks. The LLM can't invent anything it wasn't shown." |
| | `build_ask_context(...)` | "Same for chat: question + last 4 messages + evidence (token-capped on purpose)." |
| `rag/query.py` | `build_query(spec, obs)` | "The status-aware query fix: a 2xx response is phrased 'successful response 2xx semantics', ignoring stale failure-worded preset questions. This fixed a real retrieval-pollution bug." |
| `rag/embedding.py` | `Embedding.embed_chunks` / `embed_prompt` / `embed_texts` | "Google's `gemini-embedding-001`, 768-dim, batched, with retry/backoff. One embedding family for documents *and* questions. Query embeddings use task type RETRIEVAL_QUERY, docs use RETRIEVAL_DOCUMENT — that distinction measurably improves retrieval." |
| `rag/fusion.py` | `weighted_rrf()` / `dense_anchored_rrf()` | "The retrieval math. `score = Σ weight / (60 + rank)` fuses the dense ranking with BM25. We anchor the top-2 dense hits and give keyword a 0.05 weight — tuning that, measured on the 50-case eval set, took hybrid to Recall@5 = 1.000 *without* sacrificing MRR." |
| `rag/supabase_store.py` | `retrieve()` → `_search_dense()` / `_search_keyword()` | "pgvector cosine search + PostgreSQL full-text search with an OR-lexeme tsquery (AND semantics kill natural-language questions). Chunk rows carry provenance so deleting an uploaded file deletes exactly its chunks." |
| | `insert()`, `delete_source()`, `stats()` | "Upsert with embeddings, per-source cleanup (source-file attribution), and the live chunk count the sidebar shows." |
| `rag/retriever.py` | `detect_version` / `_version_check` | "Guard: a `v3` request against `v2` docs is flagged as a version mismatch instead of silently mixing docs." |
| | `get_retriever()` | "Chooses lib-pgvector/Supabase when configured, else local Chroma — same interface either way." |
| `rag/pipeline.py` | `retrieve_for_api(...)` | "Orchestration: embed → retrieve → score, filter, mode-hybrid → top-k. The seam between routes and storage." |
| | `ensure_seeded()` | "Auto-loads `data/kb_chunks.jsonl` on first retrieval if the store is empty — fresh clone boots usable." |

### `backend/app/db/`

| File | Function | Line to say |
|---|---|---|
| `runs.py` | `uses_supabase()` / `_save_sqlite()` / `_save_supabase()` | "Dispatch by `DATABASE_URL`: Postgres `runs` table or local SQLite. Every diagnosis is saved; `save()` never raises — history can't break the diagnosis." |
| | `list_runs(limit)` | "Newest-first feed for the sidebar's Recent Tests." |

### `backend/app/routes/` — one router per feature, all under `/api`

| Route | Endpoint | Line to say |
|---|---|---|
| `diagnose.py` | `diagnose()` → async `events()` generator | "The main SSE lifecycle: require_approval → execute → build_query → retrieve → stream with fallback → parse patch → save run. Each stage is a named SSE event; the UI lights up steps as they arrive." |
| `retest.py` | `retest()` | "Applies the (service-de-redacted) patch, re-executes, returns the diff and verdict." |
| `ask.py` | `ask()` | "Chat endpoint, same retrieval, streams tokens + source chips." |
| `runs.py` | `runs()` | "GET history for the sidebar." |
| `health.py` | `health()` | "Real stack facts the header pills consume: runner, retrieval backend, database, provider families." |
| `policy.py` | `add_host()` | "Validated allowlist self-service (`hostname[:port]` syntax, strict regex, 422 on junk). Blocked-by-policy responses include a one-click Allow host button in the UI." |
| `test.py` / `upload.py` / `ingest.py` / `search.py` | — | "Bare observation / file upload / chunk+embed ingestion / direct KB search." |

## Frontend — component by component

| File | Key piece | Line to say |
|---|---|---|
| `App.tsx` | state hub | "Every top-level state lives here: spec, observation, streaming text, patch, retest result, runs, files, KB stats. It wires SSE events to states." |
| | `runTest/onRun` | "Calls the diagnose stream; each SSE event type is handled explicitly." |
| `lib/sse.ts` | `streamSSE()` | "A fetch-based SSE reader — posts JSON, asserts 200, decodes `event:/data:` frames with per-event callbacks. `streamDiagnosis`/`streamAsk` wrap it." |
| `components/RequestBuilder.tsx` | — | "Method, URL (tolerates a pasted 'GET https://…' and strips the verb), empty question with a status-aware placeholder, body/headers/auth/params tabs." |
| `components/ResponseViewer.tsx` | `blockedHost()` | "Renders the real observation. If the SSRF guard blocked it, it detects the host and shows a one-click **Allow host** button." |
| `components/DiagnosisViewer.tsx` | step pills, "served by" pill, `containsRedacted()` | "Steps light up Tested → Retrieved → Diagnosed → Verify. Provider pill streams live. Apply stays disabled while any `<REDACTED>` remains — secrets never leave this page except into the retest request." |
| `components/Sidebar.tsx` | `relativeTime()` | "Live collections (bundled KB chunk count, imported files) + real run history, newest first." |
| `components/AskPanel.tsx` | — | "Chat for API concepts, same retrieval, sources chips, 200-word cap, last-4-message context." |
| `components/Header.tsx` | — | "Tabs + status pills from `/api/health`." |
| `types.ts` | — | "Mirrors backend `models.py` — the two must stay in sync." |

## Knowledge base + eval (one paragraph)

> "`data/kb_chunks.jsonl` is 91 curated chunks with stable IDs (KB-MDN-401,
> KB-SEC-SSRF, KB-API-……) and provenance (title, URL, section, api). Ingestion
> is chunk-by-tokens → Gemini embedding → pgvector upsert. Retrieval quality
> isn't guessed — there are 50 labelled questions and a runner
> (`eval/run_eval.py`) scoring Recall@5 and MRR; the hybrid fusion settings
> came from that harness. Discipline line: *measure before adding machinery*."

## Load-bearing numbers (say them with confidence)

| Fact | Value |
|---|---|
| Backend tests | 80 passing (`PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q`) |
| E2E tests | 4 Playwright scenarios (`cd frontend && npx playwright test e2e`) |
| KB chunks | 91 |
| Eval set | 50 labelled questions; dev split Recall@5: hybrid 1.000, dense 0.971, keyword 0.614 |
| Provider chain | Groq → Gemini → Mistral → NVIDIA, 20 s first-token timeout |
| Embeddings | `gemini-embedding-001`, 768-dim |

## Likely questions & one-line answers

- **"Why search both dense and BM25?"** — Dense misses exact identifiers
  (error codes, header names); BM25 misses phrasing; RRF fusion showed
  hybrid beats both on the eval set.
- **"Why is the patch gated?"** — Two gates: approval before any
  state-changing method, and redaction-aware fixing (real secrets are
  substituted only in the outgoing retest).
- **"What if a provider is down?"** — First-serve fallback chain with a
  first-token timeout; the UI shows the serving provider, so fallback is
  *visible*, not silent.
- **"Where does data live?"** — Supabase Postgres (`kb_chunks` + `runs`);
  without `DATABASE_URL` it degrades to local SQLite + Chroma, same
  interfaces.
- **"Is anything fake?"** — Only the bundled preset demo content, which the
  footer labels as sample data; history, KB stats, and the provider chain
  are live.
