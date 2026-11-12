# Frontend — components, state, contracts

Stack: React 19 + TypeScript + Vite + Tailwind CSS 4. Entry `src/main.tsx`
mounts `App.tsx`. All server access goes through `/api/*` (proxied to the
FastAPI backend, see `vite.config.ts`).

## App.tsx — the state hub

**What it owns (top-level state):**

| State | Set by | Consumed by |
|---|---|---|
| `activeTab` | Header tabs | switches Diagnose / Ask API views |
| `spec` (RequestSpec) | RequestBuilder edits, preset selection | RequestBuilder, diagnose calls |
| `observation` | `observation` SSE event / sample | ResponseViewer, DiagnosisViewer heading |
| `status` (idle/running/done/error) | run lifecycle | pills, buttons |
| `citations`, `sources`, `whatWentWrong`, `suggestedFix`, `patch` | SSE events | DiagnosisViewer |
| `retestResult`, `retesting` | retest call | verdict card, Verified pill |
| `recentRuns` | `GET /api/runs` on mount + after runs | Sidebar Recent Tests |
| `uploadedFiles` | `GET /api/files`, refreshed after uploads | Sidebar "My own API" |
| `bundledChunks` | `GET /api/kb/stats`, refreshed after ingest | Sidebar "Bundled API docs" |
| Dashboard analytics | `GET /api/runs/analytics` on dashboard open/refresh | persisted run totals, status mix, model usage, endpoints and recent diagnoses |
| `presets` | bundled + imported-spec presets | Sidebar Collections |
| `error` / `notice` | failures / import success | banner messages |
| `isMockData` | preset selection vs live results | "Sample data" footer honesty label |

**Import flow (`handleImportOpenAPI`):** upload → ingest → parse JSON spec →
generate up to 8 endpoint presets (base URL from `servers[0]`, path
parameters replaced with `1`) → select the first preset → show a success
banner with the indexed chunk count. YAML is accepted for indexing; only
preset parsing needs JSON.

**Care detail:** the question field is empty by default in presets; the
RequestBuilder's placeholder suggests a question dynamically from the live
observation status. The backend is status-aware, so empty beats stale.

## Components

### Dashboard.tsx
Displays live aggregates from saved diagnosis runs and the indexed knowledge
chunk count. Empty histories show an empty state; analytics are not populated
with sample counts or synthetic latency values. The stored run schema does not
include retest verification or request latency, so the dashboard does not claim
those as analytics.

### Header.tsx
Tabs (Diagnose / Ask API) + **status pills fetched live from
`GET /api/health`**: runner (HTTPX async), search backend
(`pgvector + BM25 hybrid (Supabase)` or the Chroma local variant), and the
provider chain (`Groq → Gemini → Mistral → NVIDIA`) mirrored from
`PROVIDER_ORDER`. Pills degrade to neutral labels if the backend is down.

### Sidebar.tsx
- **Collections**: "Bundled API docs — N chunks indexed" (live KB stats) and
  "My own API (OpenAPI file) — N imported files" (live file list). Each
  expands to that collection's clickable endpoints.
- **Recent Tests**: real runs from `/api/runs` (newest first, status color,
  relative time from `created_at`; falls back to bundled presets only when
  the backend is unreachable).
- **Import OpenAPI file** button with an "Uploading & indexing..." state.
- On large screens the panel is sticky and scrolls internally.

### RequestBuilder.tsx
- Row 1: method dropdown (GET/POST/PUT/PATCH/DELETE/HEAD — changing to
  GET/HEAD clears a stale body) + Run test button (disabled while running)
- Row 2: full-width URL field — **tolerates a method word pasted with the
  URL** (`GET https://…` is auto-stripped, the dropdown already carries it)
- Question-for-the-AI field: empty by default, status-aware placeholder
- Tabs: Body (JSON editor) / Headers (key-value) / Auth: Bearer / Params

### ResponseViewer.tsx
Real observation card: status badge (color-mapped), latency + size, body
preview in a capped, scrollable block. Blocked by the SSRF guard? Shows an
amber hint with a one-click **"Allow <host>"** button (calls
`POST /api/allowed-hosts`, then you run the test again). Honest footer:
"Sample data for the mockup." on presets vs "Live execution observation."
on real runs.

### DiagnosisViewer.tsx
The main feature card, viewport-capped on large screens (`sticky`,
`max-h`, content scrolls inline, action footer pinned):
- Step pills light up progressively: Tested → Retrieved → Diagnosed → Verify
- Heading is status-aware: "What Went Wrong" for failures, **"What Happened"
  for 2xx** — the assistant refutes wrong assumptions instead of running
  with them
- File streaming: "Diagnosing…" while tokens arrive
- SUGGESTED FIX: dark JSON block; Edit JSON toggle; redacted-secret inputs
  (each `<REDACTED>` header gets its own paste field; the Apply button stays
  disabled until resolved or waylaid with "Edit JSON")
- SOURCES: citation chips (KB-xxx IDs) from the retrieval event; live docs
  links when available
- Retest verdict card when a retest succeeded (before/after status, latency)
- Action footer: Apply fix and re-run (disabled while secrets missing or
  patch JSON invalid) + Copy cURL

### AskPanel.tsx
Chat tab for the same knowledge base. Streams answers, tracks source chips,
"New chat" reset, starter-question buttons fill the empty state.

## Data contracts

`src/types.ts` mirrors the backend schemas (keep in sync!):

| Frontend type | Backend counterpart |
|---|---|
| `RequestSpec` | `app.core.models.RequestSpec` |
| `Observation` (status, headers, body_preview, latency_ms, error_type…) | same |
| `Citation` / `RetestResult` / `RunSummary` | SSE payloads / retest diff / runs rows |

`sse.ts` is the shared fetch-based SSE reader: `streamSSE(url, body,
onEvent)` posts JSON, asserts a 200, and decodes `event:/data:` frames into
typed callbacks. `streamDiagnosis()` wraps it with the `/api/diagnose`
endpoint.

## FAQ notes for maintenance

- **"Loading knowledge base…"** in the sidebar is the honest empty state
  while `/api/kb/stats` resolves (or fails).
- **Stale page vs stale server:** the UI always reflects the code bundled at
  page load; if it contradicts a fresh build, hard-refresh (Ctrl+Shift+R).
  Only run ONE Vite dev server; a second instance on 5174 is the usual
  source of half-applied hot reloads.
- **Everything visible is real** (run history, KB stats, provider chain,
  files). The only sample data is the preset demo content, explicitly
  labelled.
