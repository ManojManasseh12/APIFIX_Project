# APIFix AI

**Test APIs. Find evidence. Understand failures. Verify fixes.**

APIFix AI is a documentation-grounded API debugging assistant. It sends a real request, captures the observation, retrieves relevant documentation, streams a diagnosis with citations, suggests a patch, and retests only after approval.

This repository is the working scaffold for the 5-day plan. It is deliberately a vertical slice, not a production service.

## What works

- Mock API with deterministic 401/403/404/405/410/413/415/422/429/500/502/503/timeout/redirect/SSRF behavior.
- HTTPX tester with error classification.
- Secret redaction and SSRF allowlist.
- Local retrieval (Gemini embeddings + NumPy) and Supabase pgvector retrieval.
- Upload-based RAG ingestion with Gemini embeddings, persistent ChromaDB, BM25 keyword search, and hybrid retrieval.
- Seed knowledge base (`data/kb_chunks.jsonl`, 88 chunks) auto-loaded into Chroma on first use, so uploads add to it instead of hiding it.
- Gemini → Groq → Mistral fallback adapters with quota-aware model/key switching.
- SSE diagnosis endpoint with citations and a JSON patch.
- Editable patch before retest, including a prompt for redacted token/header values.
- **Ask API tab**: grounded, streaming Q&A for API concepts, doubts, and fixes.
- React UI for request, observation, diagnosis, citations, retest diff, and API Q&A.
- 50 evaluation request fixtures and an evidence map.
- Dense/keyword/hybrid retrieval evaluation using `eval/eval_questions.jsonl`.

## Retrieval evaluation

Measured on the `dev` split with a temporary Chroma collection seeded from
`data/kb_chunks.jsonl` using `gemini-embedding-001` (768 dimensions):

| Mode | Recall@5 | MRR | Scored cases |
|---|---:|---:|---:|
| Dense | 0.971 | 0.623 | 35 |
| Keyword | 0.614 | 0.375 | 35 |
| Hybrid | 1.000 | 0.629 | 35 |

The hybrid row uses dense-anchored weighted RRF: the best two dense results
keep their order, then keyword contributes a small rank bonus (`RRF_KEYWORD_WEIGHT=0.05`)
so it can rescue a relevant chunk that dense ranked just outside the top 5.
Tuning was done on the dev split and confirmed on heldout (dense 1.000/0.564,
hybrid 1.000/0.576), so hybrid now improves both recall and ranking quality
instead of trading MRR for recall.

The same dense and hybrid scores were verified against a live Supabase pgvector
store (88 chunks). Supabase keyword search uses PostgreSQL full-text search,
which ANDs query terms by default and returns nothing for a natural-language
question; the backend builds an OR `tsquery` from the question's lexemes
instead. Its standalone keyword Recall@5 is lower than local BM25 (0.471 dev /
0.545 heldout), but hybrid still reaches 1.000 because it keeps a wider keyword
candidate pool and anchors the best dense results.

The corpus is small and the scores are directional. Run `python eval/run_eval.py
--split dev` or `--split heldout`. The seed corpus is loaded into Chroma on
first use, so no manual ingestion is required. Questions with no expected
evidence are reported as abstentions and excluded from Recall@5/MRR averages.

### Not yet verified here

- **Live LLM diagnosis** — provider keys are configured locally; streaming works, but quota fallback depends on each provider's current free-tier limits.
- **Frontend** — the React app is type-checked and built, but has not been exercised in a browser by the automated tests.
- **Supabase pgvector** — no project is configured, so only the local retriever was exercised.
- **Network-dependent cases** — DNS/TLS fixtures are skipped offline.

## What is not finished

- The evaluation runner currently scores **retrieval only**; diagnosis correctness, citation accuracy, unsupported claims, and retest scoring are the next step.
- The seed knowledge base has 88 starter chunks. Replace them with real source excerpts and add the rest of the 60–100 chunk set.
- `eval/evidence_map.json` is a starting map; refine it after ingestion.
- Playwright/CORS evidence is intentionally not implemented.
- No production auth, multi-tenancy, or deployment hardening.

## Deliverables

| File | Purpose |
|---|---|
| `/media/manoj/Windows/Users/manoj/Downloads/files/APIFix_AI_5_Day_Plan.md` | Condensed 5-day plan |
| `/media/manoj/Windows/Users/manoj/Downloads/files/APIFix_AI_5_Day_Plan.pdf` | 18-page A4 plan (verified render) |
| `/media/manoj/Windows/Users/manoj/Downloads/files/APIFix_AI_5_Day_Plan.docx` | Editable plan |
| `/media/manoj/Windows/Users/manoj/Downloads/files/APIFix_AI_Full_Process_Guide.md` | 12-phase build runbook |
| `/media/manoj/Windows/Users/manoj/Downloads/files/APIFix_AI_Full_Process_Guide.pdf` | 75-page A4 runbook (verified render) |
| `/media/manoj/Windows/Users/manoj/Downloads/files/APIFix_AI_Full_Process_Guide.docx` | Editable runbook |

Copies of the Markdown live in `docs/`. Regenerate the PDF/DOCX with `docs/md_to_html.py` plus LibreOffice:

```bash
python docs/md_to_html.py docs/APIFix_AI_Full_Process_Guide.md docs/APIFix_AI_Full_Process_Guide.html
libreoffice --headless --convert-to pdf docs/APIFix_AI_Full_Process_Guide.html
libreoffice --headless --convert-to 'docx:MS Word 2007 XML' docs/APIFix_AI_Full_Process_Guide.html
```

## Repository layout

```text
backend/app/main.py  FastAPI app: lifespan startup, CORS, router wiring
backend/app/core/    settings, domain models, errors, security, tester, retest
backend/app/db/      SQLite connection, schema initialisation, run history
backend/app/ai/      LLM fallback chain, prompts, prompt context builders
backend/app/ai/rag/  RAG loading, chunking, embeddings, ChromaDB, and pipeline
backend/app/routes/  one APIRouter per feature (health, test, diagnose, ask, ...)
backend/app/utils/   shared helpers (UUID generation)
backend/uploads/     local uploaded documents (gitignored)
backend/data/chroma/ persistent ChromaDB files (gitignored)
backend/mock_api/    deterministic local API
backend/scripts/     provider check, ingestion, local index, scenario runner
backend/tests/       unit, route, retrieval, security, and seed tests
data/                datasets + knowledge-base seed
eval/                case requests, evidence map, evaluation runner
frontend/            React + Vite + Tailwind UI
docs/                plan, process guide, demo script, limitations
```

## Quick start

### 1. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
```

If `python3 -m venv` fails with `ensurepip is not available`, install `python3-venv` (or `python3.12-venv`). This machine had no `pip` and no `venv` support, so the environment was built with `python3 -m venv --without-pip .venv` followed by `get-pip.py`.

### 2. Build the local retrieval index

Embeddings use Google's `gemini-embedding-001` (768 dimensions), the same model
family as `cloud_llm`, so `GEMINI_API_KEY` must be set in `.env` (see step 6)
before building the index. This works without Supabase:

```bash
python backend/scripts/build_local_index.py
```

The script reads `data/kb_seed.csv`, calls the Gemini embedding API, and writes
`data/kb_embeddings.npy` and `data/kb_meta.json`. Rebuild the index whenever
`EMBED_MODEL` or `EMBED_DIM` changes.

### 2a. Upload and index documents with ChromaDB

The upload/ingest/search endpoints are available at both `/api/...` and their
root paths. Allowed file types are PDF, DOCX, XLSX, PPTX, HTML, XML, CSV,
TSV, JSON, YAML, JSONL, Markdown, and text;
uploads are limited by `MAX_UPLOAD_MB`.

```bash
curl -F 'files=@data/kb_chunks.jsonl' http://127.0.0.1:8000/api/upload
curl -X POST http://127.0.0.1:8000/api/ingest
curl -X POST http://127.0.0.1:8000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"What does HTTP 401 mean?","mode":"hybrid","k":5}'
```

Configure `UPLOAD_DIR`, `CHROMA_DIR`, `EMBED_MODEL`, `EMBED_DIM`,
`CHUNK_TOKENIZER_MODEL`, and `MAX_UPLOAD_MB` in `.env` to override the defaults.
Chroma collections are named per embedding model and dimension, so switching
models creates a separate index instead of mixing incompatible vectors.

On the first retrieval, search, or KB-stats call, the backend loads
`data/kb_chunks.jsonl` into Chroma if the collection is empty. Uploaded
documents are added alongside the seed corpus. Set `SEED_KB=false` to start
with an empty knowledge base, or `SEED_KB_FILE=<path>` to use a different seed
file.

### 2b. Batch ingestion

Ingestion follows the same shape as `cloud_llm`: files are processed in batches
of `BATCH_FILE_SIZE` (default 10), up to `BATCH_MAX_CONCURRENCY` (default 4)
files are prepared at once, duplicate files in the same batch are skipped, and
chunks are embedded in requests capped at `EMBED_MAX_CHUNKS_PER_REQUEST`
(default 64) and `EMBED_MAX_TOKENS_PER_REQUEST` (default 8,000). Gemini requests
are throttled to `EMBED_REQUESTS_PER_MINUTE` (default 60) and
`EMBED_TOKENS_PER_MINUTE` (default 100,000), with retries controlled by
`EMBED_MAX_RETRIES`, `EMBED_RETRY_BASE_DELAY_S`, and `EMBED_RETRY_MAX_DELAY_S`.

`POST /api/ingest` returns both totals and per-file statuses
(`completed`, `skipped`, `empty`, or `failed`) so one bad file does not discard
the rest of the batch:

```json
{
  "chunks_added": 88,
  "sources": ["kb_chunks.jsonl"],
  "files": [
    {"file": "kb_chunks.jsonl", "status": "completed", "chunks_added": 88, "message": "..."}
  ]
}
```

### 3. Run the mock API

From the repository root:

```bash
python run_mock.py
```

### 4. Run the backend

From the repository root:

```bash
python run_api.py
```

### 5. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

### 6. Add provider keys

Edit `.env`:

```bash
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-flash-latest,gemini-3.5-flash
GROQ_API_KEY=...
GROQ_MODEL=openai/gpt-oss-120b,qwen/qwen3.8-27b
MISTRAL_API_KEY=...
MISTRAL_MODEL=mistral-small-latest
PROVIDER_ORDER=gemini,groq,mistral
```

`GEMINI_MODEL`, `GROQ_MODEL`, and `MISTRAL_MODEL` accept comma-separated
models. Models/providers are streamed in `PROVIDER_ORDER`. A retryable failure
(429/quota, timeout, connection error, 5xx) is retried on the **same** model
and key up to `LLM_MAX_ATTEMPTS_PER_PROVIDER` times (default 3) with exponential
backoff and jitter; only then does the backend rotate to the next model or
provider. Authentication errors move on immediately. Timeouts come from
`LLM_FIRST_TOKEN_TIMEOUT_S` and `LLM_TIMEOUT_S`. Add `GEMINI_API_KEYS`,
`GROQ_API_KEYS`, or `MISTRAL_API_KEYS` (comma-separated) to rotate across
multiple keys too.

Verify them:

```bash
python backend/scripts/check_providers.py
```

If no provider is configured, `/api/diagnose` returns a clear `no_providers_configured` error. `/api/test` still works.

## Demo request

In the UI:

- Method: `GET`
- URL: `http://localhost:8001/v1/profile`
- Headers: `Accept: application/json`
- Question: `Why does this request return 401?`

Expected: observation `401`, cited MDN 401 evidence, a patch that adds `Authorization: Bearer valid-user-token`, and a retest to `200`.

### Ask API assistant

Use the **Ask API** tab for general API questions. The same retriever provides
grounded sources and the same provider fallback keeps the answer streaming if a
model is rate-limited.

Token usage is deliberately capped: Ask sends only the **last 4 messages** as
conversation context, retrieves **4 evidence chunks**, and asks the model to keep
answers under **200 words**. Use **New chat** to start fresh and avoid sending
old history.

```bash
curl -N -X POST http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is idempotency and why do POST retries create duplicates?","history":[]}'
```


## Supabase pgvector

The app uses Supabase only when `RETRIEVAL_BACKEND=supabase` and
`DATABASE_URL` is set to a real (non-placeholder) value. Otherwise it uses the
local Chroma index. Both stores implement the same interface, so `/api/search`,
`/api/diagnose`, `/api/ask`, uploads, deletes, and `eval/run_eval.py` work
against either one.

1. Create a Supabase project and open **SQL Editor**.
2. Run [`backend/sql/schema.sql`](backend/sql/schema.sql) once. It enables the
   `vector` extension and creates `kb_chunks` with HNSW and full-text indexes.
3. Copy the **Session pooler** connection string from **Project Settings →
   Database**. Replace `[YOUR-PASSWORD]` and URL-encode special characters
   (`@` → `%40`, `#` → `%23`, `:` → `%3A`). Keep `?sslmode=require`.
4. Set these in `.env`:

   ```bash
   DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   RETRIEVAL_BACKEND=supabase
   ```

   `DATABASE_URL` is the only Supabase setting the backend needs; the pgvector
   retriever connects to it directly. If the value still contains the
   `.env.example` placeholders (`<ref>`, `<password>`, `<region>`), the app
   treats Supabase as unconfigured and stays on the local Chroma store instead
   of attempting a connection.
5. Install the Python deps (already listed in `backend/requirements.txt`):

   ```bash
   .venv/bin/pip install -r backend/requirements.txt
   ```

6. Load the 88-chunk seed corpus:

   ```bash
   python backend/scripts/ingest.py
   ```

   Alternatively, start the API with `SEED_KB=true` and the first search,
   diagnosis, or KB-stats call loads `data/kb_chunks.jsonl` into Supabase
   automatically.

7. Verify the connection and retrieval:

   ```bash
   python backend/scripts/check_supabase.py
   ```

   It prints the store type, chunk count, and top hybrid hits. Restart the API
   after changing `.env`, then try `/api/search` or the Ask API tab.

If the database is unreachable, the app logs a warning and falls back to
Chroma. Set `RETRIEVAL_BACKEND=local` to make that explicit. The schema is
`vector(768)` because `gemini-embedding-001` produces 768-dimensional
embeddings; if you change `EMBED_MODEL`/`EMBED_DIM`, update the column dimension
and re-ingest. If `kb_chunks` was created for the old 384-dimensional MiniLM
vectors, run [`backend/sql/migrate_embedding_384_to_768.sql`](backend/sql/migrate_embedding_384_to_768.sql)
first. Uploads written while one backend is active are not copied to the other,
so re-upload or run `ingest.py` after switching stores.

## Tests

From the repository root:

```bash
PYTHONPATH=backend pytest backend/tests -q
```

The suite includes unit and route tests plus `test_api_httpx.py`, which drives
the real FastAPI app through `httpx.ASGITransport` and starts the mock API on a
random port to exercise `/api/test`, `/api/retest`, upload → ingest → search →
delete, and the `/api/diagnose` and `/api/ask` SSE streams.

Mock API scenario smoke test (start `python run_mock.py` first):

```bash
python backend/scripts/run_scenarios.py
# add --network to include the external API checks
```

`--network` calls `postman-echo.com`, `petstore3.swagger.io`, and
`jsonplaceholder.typicode.com`. Those hosts must be in `ALLOWED_HOSTS`
(`.env.example` already lists them). Bare hosts are allowlisted for the
scheme's default port only, e.g. `postman-echo.com` permits HTTPS port 443 but
not `:8443`.

Browser tests use Playwright with route interception, so they do not need the
backend or an LLM key:

```bash
cd frontend
npm install
npx playwright install chromium
npm run test:e2e
```

Playwright requires Node 20 or newer. If your system Node is older, point
`PATH` at a Node 20 install first, for example:

```bash
PATH=/path/to/node20/bin:$PATH npm run test:e2e
```

## Evaluation

The runner auto-loads the seed corpus into Chroma when the collection is empty:

```bash
python eval/run_eval.py --split dev
python eval/run_eval.py --split heldout
```

The runner reads `eval/eval_questions.jsonl` and prints Recall@5 and MRR for
dense, keyword, and hybrid retrieval.

---

**Read next:** `docs/APIFix_AI_5_Day_Plan.md` for the schedule and `docs/APIFix_AI_Full_Process_Guide.md` for the complete build runbook.
