# APIFix AI — Documentation

**What this project is:** a documentation-grounded API debugging assistant.
You run a real HTTP request against a real API; the system observes the actual
response, retrieves matching documentation from a knowledge base, streams an
AI diagnosis with citations, proposes a concrete JSON patch, and re-runs the
fixed request to verify the fix.

**What this project is NOT:** Postman. It does not aim to be a full API
workbench (environments, scripting, team collections). It does the one thing
manual tools cannot: explain and verify.

## Document map

| Document | Contents |
|---|---|
| [`workflow.md`](./workflow.md) | **Start here for flows**: every input entry point, the 10-hop diagnose journey, Ask, import, allowlist, history, failover, and why Playwright is test-only |
| [`architecture.md`](./architecture.md) | The three components, how they connect, every request lifecycle end to end, ports and runs |
| [`backend.md`](./backend.md) | Module-by-module: every backend file, what it does, why it exists |
| [`frontend.md`](./frontend.md) | Components, state flow, the SSE contract, presets and types |
| [`knowledge-and-eval.md`](./knowledge-and-eval.md) | The knowledge base, chunking, hybrid retrieval + RRF fusion, the 50-case eval harness |
| [`security.md`](./security.md) | SSRF guard + allowlist, secret redaction, approval gate, upload validation |

## The system on one line

```
Browser (React, :5173) --/api/*--> FastAPI (:8000) --> {target API, Supabase Postgres, LLM providers}
```

## Quick start

```bash
# 1. Backend (from repo root)
python server.py                 # http://127.0.0.1:8000  (docs at /docs)

# 2. Frontend
cd frontend && npm run dev       # http://localhost:5173  (proxies /api to :8000)

# 3. Checks
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q     # backend tests
cd frontend && npx playwright test e2e                             # browser e2e
```

Configuration lives in `.env` (copy `.env.example`): provider API keys, the
LLM provider order, the SSRF host allowlist seed, and the Supabase
`DATABASE_URL`. Without `DATABASE_URL` the system still runs — run history
falls back to local SQLite and retrieval falls back to a local Chroma store.

## The five things to be able to explain

1. **Real observation** — requests are executed by async HTTPX with SSRF
   checks, timeouts, and secret redaction. Nothing is simulated.
2. **Hybrid retrieval + RRF** — dense vectors fused with BM25 keyword ranks
   (weighted reciprocal rank fusion, dense-anchored).
3. **Cited AI diagnosis** — the LLM is prompted only with the observed
   response plus retrieved chunks; every claim maps to a chunk ID.
4. **Fix-and-verify loop** — a suggested patch is applied only after user
   consent, secrets are un-redacted only for the actual re-run, and the run
   shows Verified only after a successful re-test.
5. **Honest UI** — live run history from the database, live KB stats, live
   provider chain; no fake numbers anywhere.
