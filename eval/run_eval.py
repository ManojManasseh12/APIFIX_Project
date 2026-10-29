import asyncio
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.models import RequestSpec  # noqa: E402
from app.services.query import build_query  # noqa: E402
from app.services.retriever import get_retriever  # noqa: E402
from app.services.tester import execute  # noqa: E402

MOCK = "http://localhost:8001"
ALLOWED_FIELDS = {
    "method", "url", "headers", "body", "timeout_s",
    "expected_status", "question", "approved",
}


def labels():
    with open(ROOT / "data" / "evaluation_cases.csv", newline="", encoding="utf-8") as fh:
        return {row["case_id"]: row for row in csv.DictReader(fh)}


async def setup_mock(client, steps):
    await client.post(f"{MOCK}/__reset")
    for step in steps:
        await client.post(f"{MOCK}/__control", json=step)


def recall5(predicted, expected):
    return 1.0 if set(predicted[:5]) & set(expected) else 0.0


def mrr(predicted, expected):
    for rank, chunk_id in enumerate(predicted[:5], start=1):
        if chunk_id in expected:
            return 1.0 / rank
    return 0.0


async def run(split: str):
    rows = labels()
    requests = json.load(open(ROOT / "eval" / "case_requests.json"))
    evidence = json.load(open(ROOT / "eval" / "evidence_map.json"))
    retriever = get_retriever()
    results = []

    async with httpx.AsyncClient(timeout=30) as client:
        for case_id, label in rows.items():
            if label["split"] != split:
                continue

            payload = requests.get(case_id)
            if not payload:
                results.append({"case_id": case_id, "status": "missing_fixture"})
                continue
            if payload.get("skip"):
                results.append({"case_id": case_id, "status": "skipped", "reason": payload.get("reason")})
                continue
            if payload.get("requires_network"):
                results.append({"case_id": case_id, "status": "skipped_offline"})
                continue

            try:
                await setup_mock(client, payload.get("mock_setup", []))
                spec = RequestSpec(**{k: v for k, v in payload.items() if k in ALLOWED_FIELDS})
                observation = await execute(spec)
                retrieval = retriever.search(
                    build_query(spec, observation), api_name=None, path=str(spec.url)
                )
                predicted = [chunk["chunk_id"] for chunk in retrieval.chunks]
                expected = evidence.get(case_id, [])
                abstain_expected = not expected
                abstain_ok = retrieval.version_mismatch or retrieval.insufficient

                results.append({
                    "case_id": case_id,
                    "category": label["category"],
                    "observed_status": observation.status,
                    "observed_error": observation.error_type,
                    "expected_observed_status": label["observed_status"],
                    "predicted_chunks": predicted[:5],
                    "expected_chunks": expected,
                    "abstain_expected": abstain_expected,
                    "abstain_ok": abstain_ok if abstain_expected else None,
                    "recall5": None if abstain_expected else recall5(predicted, expected),
                    "mrr": None if abstain_expected else mrr(predicted, expected),
                    "version_mismatch": retrieval.version_mismatch,
                    "insufficient": retrieval.insufficient,
                    "status": "ok",
                })
            except Exception as exc:
                results.append({
                    "case_id": case_id,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                })

        await client.post(f"{MOCK}/__reset")

    out = ROOT / "eval" / "results" / f"{split}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(results, open(out, "w"), indent=2)
    print(f"{split}: {len(results)} cases -> {out}")
    return results


def summarize(rows):
    scored = [
        row for row in rows
        if row["status"] == "ok" and not row.get("abstain_expected")
    ]
    abstain = [
        row for row in rows
        if row["status"] == "ok" and row.get("abstain_expected")
    ]
    if not scored and not abstain:
        return {"cases": 0}
    by_category = defaultdict(list)
    for row in scored:
        by_category[row["category"]].append(row)
    summary = {
        "retrieval_cases": len(scored),
        "recall_at_5": round(sum(row["recall5"] for row in scored) / len(scored), 3) if scored else None,
        "mrr": round(sum(row["mrr"] for row in scored) / len(scored), 3) if scored else None,
        "abstention_cases": len(abstain),
        "abstention_correct": sum(1 for row in abstain if row.get("abstain_ok")),
        "skipped": sum(1 for row in rows if row["status"].startswith("skipped")),
        "errors": sum(1 for row in rows if row["status"] == "error"),
        "by_category": {
            category: {
                "cases": len(values),
                "recall_at_5": round(sum(row["recall5"] for row in values) / len(values), 3),
            }
            for category, values in sorted(by_category.items())
        },
    }
    return summary


if __name__ == "__main__":
    split = sys.argv[1] if len(sys.argv) > 1 else "dev"
    result = asyncio.run(run(split))
    print(json.dumps(summarize(result), indent=2))
