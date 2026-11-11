# Security — the three gates and what they protect

APIFix AI fetches arbitrary user-supplied URLs by design, ships request
payloads to third-party LLM providers, and stores diagnosis history in the
cloud. Each of those is a hazard; each has a dedicated gate.

## Gate 1 — SSRF guard (`app/core/security.py`)

**The threat:** a debugging tool that accepts any URL is a textbook
server-side request forgery (SSRF) primitive: point it at
`http://169.254.169.254/…` (cloud metadata), `http://192.168.1.1/admin`, or
an internal service, and the server will fetch it *for you* — from inside
the network.

**The defense, in order:**
1. **Scheme allowlist** — only `http`/`https`.
2. **Hostname allowlist** — exact-match against:
   - `ALLOWED_HOSTS` (env, e.g. `api.github.com,jsonplaceholder.typicode.com`)
   - `data/allowed_hosts.txt` — the **app-managed** list, merged on every
     check (a third host added mid-session needs no restart)
3. **DNS-resolve + IP-range block** — every resolved address must be public;
   loopback / private / link-local / multicast / reserved ranges are refused
   even if a hostname maps to them (DNS-rebinding resistant).
4. **Loopback carve-out** — `localhost:8001` is special-cased for the
   bundled mock API.

**Error output is honest:** the runner returns
`error_type: "blocked_by_policy"` with
`"host not allowlisted: <host>"` — and the Response panel offers a validated
**"Allow <host>"** button (`POST /api/allowed-hosts`, strict
`hostname[:port]` syntax; HTML/script/garbage rejected with 422). Trusting a
host only allows *name resolution* — the IP-range block still applies at
request time, so internal targets stay unreachable.

**Where it runs:** before every outbound call in `tester.execute()` —
Run test, diagnosis, retest all pass through it.

## Gate 2 — Secret redaction (same module)

**The threat:** Bearer tokens / API keys pasted into headers would leak to
the LLM providers (third parties), into streamed diagnosis text, into the
runs table, and onto every shared screen.

**The defense:** header values matching secret shapes are replaced with the
literal `"<REDACTED>"` before the request leaves the UI, and every
downstream copy (LLM prompt, history, patches) sees only that.

**The working rule the UI enforces:**
- Suggested patches reference `Bearer <REDACTED>` instead of real tokens.
- "Apply fix and re-run" stays disabled while any `<REDACTED>` remains:
  the user pastes the real value into a dedicated field, which is
  substituted **only in the outgoing retest request** and never persisted.

## Gate 3 — Approval gate for state changes

**The threat:** a mis-click on a POST against a real API changes real data
(creates issues, deletes resources).

**The defense:** `require_approval(spec)` — state-changing methods
(POST/PUT/PATCH/DELETE) must carry `approved: true`, which the frontend sets
only after an explicit user consent step. Without it the diagnose stream
emits `approval_required` and the UI asks first. GET/HEAD are read-only and
exempt.

## Supporting hardening

| Area | What is enforced |
|---|---|
| Uploads (`routes/upload.py`) | extension allowlist (json/yaml/yml/jsonl/md/txt/html/pdf), size cap (`MAX_UPLOAD_MB`), sanitized unique filenames |
| HTTP runner (`tester.py`) | `trust_env=False` (no silent proxy env routing), connect/total timeouts, redirect follow disabled (status + Location surfaced instead) |
| Tests | `tests/conftest.py` isolates from the real `.env` (SQLite, local retrieval, no provider keys), so nothing real leaks into test runs |
| Git hygiene | `.env`, `data/allowed_hosts.txt`, `data/runs.sqlite3`, `eval/results/` are gitignored — credentials and per-user data never enter the repo |

## Threat-model summary

| Attempt | Result |
|---|---|
| Point the tester at an internal IP | blocked (private-range filter) |
| Point it at an untrusted public host | blocked (allowlist) until trusted via validated API |
| Diagnose an OAuth-protected flow | supported *only* through manual, redacted header values (OAuth helper is out of scope — see roadmap) |
| Store a diagnosis containing an API key | impossible — key is `<REDACTED>` before the LLM/history ever see it |
| LLM provider outage | contained — fallback chain; no fallback ever skips the gates |
