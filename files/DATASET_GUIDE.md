# APIFix AI — Dataset Guide

APIFix AI uses three separate datasets. Keep them separate: documentation is evidence, test APIs produce observations, and labelled cases measure the system.

## 1. Knowledge-base dataset (RAG)

File: `knowledge_base_sample.jsonl` (one chunk per line). In production these rows live in Supabase PostgreSQL with pgvector.

| Column | Meaning |
|---|---|
| chunk_id | Unique ID for one text chunk |
| source_type | official_doc, openapi_spec, or pdf |
| source_url | Where the text came from; required for citations |
| source_title | Human-readable title shown in citations |
| api_name / api_version | Used for metadata filtering so a v2 error does not retrieve v1 docs |
| method / path | Set for OpenAPI operations; null for prose docs |
| section / page | Heading or PDF page number for precise citation |
| license | Licence or terms note; check before storing |
| retrieved_at | Date the source was downloaded |
| token_count | Chunk length; target 180–230 tokens with 30–40 overlap |
| chunk_text | The text that is embedded and shown to the LLM |
| embedding_model / embedding_dim | sentence-transformers/all-MiniLM-L6-v2, 384 dimensions |
| embedding | 384-number vector in a pgvector column |
| embedding_pipeline_version | Version of chunking and embedding; change it when the pipeline changes |

Note: the sample `chunk_text` values are paraphrases written for this guide. Replace them with text from the cited sources during ingestion, and check each source's licence first.

## 2. API testing dataset

File: `test_api_scenarios.csv`. Each row is a request APIFix AI can send to reproduce a situation.

| Column | Meaning |
|---|---|
| scenario_id | Unique ID |
| target | local_mock (controlled), postman_echo, petstore, or jsonplaceholder |
| method / url | Request sent by HTTPX |
| headers_redacted | Credentials replaced with `<REDACTED>`; never store real tokens |
| body | Optional JSON body |
| expected_status | Status or outcome the scenario should produce |
| safe_by_default | yes = GET/HEAD-style test allowed without approval; no = needs user approval |
| notes | Why the scenario exists |

Public demo APIs can change or rate-limit requests, so use the local mock API for any scenario that must reproduce an exact error.

## 3. Evaluation dataset

File: `evaluation_cases.csv`. 50 labelled cases: 10 auth, 10 endpoint, 10 validation, 10 rate/network, 10 RAG/diagnosis.

| Column | Meaning |
|---|---|
| case_id | Unique ID; prefix shows category |
| category | auth, endpoint, validation, rate_net, or rag |
| method / path | Request under test |
| input_issue | What is wrong with the request or setup |
| observed_status | Actual HTTP status, or an error type such as timeout, dns_error, tls_error, blocked_by_policy |
| expected_cause | The diagnosis the system should give |
| expected_evidence | Documentation that should be retrieved and cited |
| expected_fix | Fix the system should suggest |
| retest_expected_status | Status after the approved fix (only if the mock accepts the fix) |
| must_not_claim | A claim the system must avoid (for example, "token expired" without evidence) |
| split | dev for tuning; heldout for final measurement. Do not tune on heldout cases |

## How the datasets connect

A test scenario produces an observed status. The retriever uses that status and the request to find chunks in the knowledge base. The diagnosis is scored against the matching evaluation case. Metrics from the deck: Recall@5 and MRR for retrieval, citation accuracy, diagnosis correctness, unsupported-claim rate, and retest outcome.

## Limitations

- These are planned cases. Run each one against the mock API before reporting results.
- Retest status codes assume the mock accepts the fixed request. A real API may behave differently.
- Check each public source's terms and rate limits before ingestion.
