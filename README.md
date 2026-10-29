# APIFix AI

**Test APIs. Find evidence. Understand failures. Verify fixes.**

APIFix AI is a documentation-grounded API debugging assistant. It sends a real request, captures the observation, retrieves relevant documentation, streams a diagnosis with citations, suggests a patch, and retests only after approval.

This repository is the working scaffold for the 5-day plan. It is deliberately a vertical slice, not a production service.

## What works

- Mock API with deterministic 401/403/404/405/410/413/415/422/429/500/502/503/timeout/redirect/SSRF behavior.
- HTTPX tester with error classification.
- Secret redaction and SSRF allowlist.
- Local retrieval (MiniLM + NumPy) and Supabase pgvector retrieval.
- Upload-based RAG ingestion with MiniLM embeddings, persistent ChromaDB, BM25 keyword search, and hybrid retrieval.
- Gemini → Groq → Mistral fallback adapters with quota-aware model/key switching.
- SSE diagnosis endpoint with citations and a JSON patch.
- **Ask API tab**: grounded, streaming Q&A for API concepts, doubts, and fixes.
- React UI for request, observation, diagnosis, citations, retest diff, and API Q&A.
- 50 evaluation request fixtures and an evidence map.
- Dense/keyword/hybrid retrieval evaluation using `eval/eval_questions.jsonl`.

## Retrieval evaluation

Measured on the `dev` split with a temporary Chroma collection seeded from
`data/kb_chunks.jsonl`:

| Mode | Recall@5 | MRR | Scored cases |
|---|---:|---:|---:|
| Dense | 1.000 | 0.690 | 35 |
| Keyword | 0.614 | 0.375 | 35 |
| Hybrid | 0.971 | 0.548 | 35 |

The corpus is small and the scores are directional. Run `python eval/run_eval.py
--split dev` or `--split heldout` against the Chroma knowledge base after
ingesting documents. Questions with no expected evidence are reported as
abstentions and excluded from Recall@5/MRR averages.

### Not yet verified here

- **Live LLM diagnosis** — provider keys are configured locally; streaming works, but quota fallback depends on each provider's current free-tier limits.
- **Frontend** — the React app is type-checked and built, but has not been exercised in a browser by the automated tests.
- **Supabase pgvector** — no project is configured, so only the local retriever was exercised.
- **Network-dependent cases** — DNS/TLS fixtures are skipped offline.

## What is not finished

- The evaluation runner currently scores **retrieval only**; diagnosis correctness, citation accuracy, unsupported claims, and retest scoring are the next step.
- The seed knowledge base has 22 starter chunks. Replace them with real source excerpts and add the rest of the 60–100 chunk set.
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
backend/app/        FastAPI app: tester, retrievers, LLM, security, routes
backend/app/ai/rag/ RAG loading, chunking, embeddings, ChromaDB, and pipeline
backend/uploads/    Local uploaded documents (gitignored)
backend/data/chroma/ Persistent ChromaDB files (gitignored)
backend/mock_api/   deterministic local API
backend/scripts/    provider check, ingestion, local index, scenario runner
backend/tests/      security tests
data/               datasets + knowledge-base seed
eval/               case requests, evidence map, evaluation runner
frontend/           React + Vite + Tailwind UI
docs/               plan, process guide, demo script, limitations
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

This works without Supabase:

```bash
python backend/scripts/build_local_index.py
```

The script reads `data/kb_seed.csv` and writes `data/kb_embeddings.npy` and `data/kb_meta.json`.

### 2a. Upload and index documents with ChromaDB

The upload/ingest/search endpoints are available at both `/api/...` and their
root paths. Allowed file types are PDF, JSON, YAML, JSONL, Markdown, and text;
uploads are limited by `MAX_UPLOAD_MB`.

```bash
curl -F 'files=@data/kb_chunks.jsonl' http://127.0.0.1:8000/api/upload
curl -X POST http://127.0.0.1:8000/api/ingest
curl -X POST http://127.0.0.1:8000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"What does HTTP 401 mean?","mode":"hybrid","k":5}'
```

Configure `UPLOAD_DIR`, `CHROMA_DIR`, `EMBED_MODEL`, and `MAX_UPLOAD_MB` in
`.env` to override the defaults.

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
models. Models/providers are streamed in `PROVIDER_ORDER`; if one hits a rate
limit or exhausted quota, the backend automatically retries the next model or
provider before any token is sent. Add `GEMINI_API_KEYS`, `GROQ_API_KEYS`, or
`MISTRAL_API_KEYS` (comma-separated) to rotate across multiple keys too.

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


## Supabase (optional but required for the deck's architecture)

1. Create a project and enable the `vector` extension.
2. Run the schema in `docs/ARCHITECTURE.md`.
3. Set `DATABASE_URL` and `RETRIEVAL_BACKEND=supabase`.
4. Ingest:

```bash
python backend/scripts/ingest.py
```

If Supabase is unavailable, set `RETRIEVAL_BACKEND=local`; the local index is enough for the demo.

## Tests

```bash
cd backend
pytest -q
```

## Evaluation

After uploading and ingesting the corpus that corresponds to the labels:

```bash
python eval/run_eval.py --split dev
python eval/run_eval.py --split heldout
```

The runner reads `eval/eval_questions.jsonl` and prints Recall@5 and MRR for
dense, keyword, and hybrid retrieval. An empty Chroma collection is reported
as an error rather than producing misleading zero scores.

---

**Read next:** `docs/APIFix_AI_5_Day_Plan.md` for the schedule and `docs/APIFix_AI_Full_Process_Guide.md` for the complete build runbook.
