# APIFix AI — 5-Minute Demo Script

| Time | Beat | Action |
|---|---|---|
| 0:00 | Problem | "Debugging APIs means juggling a client, logs, and docs. APIFix AI connects the actual response to official evidence." |
| 0:25 | AUTH-001 | `GET /v1/profile`, no auth → 401 → cited MDN 401 → patch adds Bearer header → retest 200. |
| 1:55 | VAL-002 | `POST /v1/orders` with `"quantity":"two"` → 422 → schema evidence → patch `quantity: 2` → retest 201. |
| 2:55 | RNET-001 | `GET /v1/search` → 429 with `Retry-After: 30` → back off and honor the header. |
| 3:35 | RAG-004 | `GET /v1/x` → vendor code `X-ERR-7731` → "insufficient evidence" and next checks. |
| 4:15 | Security | `http://localhost:9/` blocked by SSRF policy; secret is redacted. |
| 4:45 | Results | Show the evaluation table and one limitation. |

## Before the demo

1. `POST http://localhost:8001/__reset`
2. Confirm `GET /api/health`
3. Confirm one provider responds
4. Browser zoom 110–125%
5. Backup video ready
