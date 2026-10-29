# Limitations

- **Small evaluation set.** 50 cases, 12 held out. Treat percentages as directional, not definitive.
- **Mock API, not production APIs.** The mock is deterministic by design; real APIs differ.
- **LLM nondeterminism.** The same request can produce different wording; citations and structured patches reduce the risk but do not eliminate it.
- **Documentation licensing.** MDN, RFC, OpenAPI specs, and project docs have different terms. Check before publishing the corpus.
- **No production auth.** The demo has no multi-tenant auth, quotas, or billing.
- **Playwright not implemented.** CORS claims are limited to "not reproduced server-side."
- **Network-dependent cases.** DNS/TLS/redirect cases are skipped offline and must be reported as not executed.
- **Fallback is not availability.** Three providers reduce quota risk; they do not guarantee service.
