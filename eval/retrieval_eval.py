import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.retriever import get_retriever  # noqa: E402


def recall5(predicted, expected):
    return 1.0 if set(predicted[:5]) & set(expected) else 0.0


def mrr(predicted, expected):
    for rank, chunk_id in enumerate(predicted[:5], start=1):
        if chunk_id in expected:
            return 1.0 / rank
    return 0.0


def main(split="dev"):
    with open(ROOT / "data" / "evaluation_cases.csv", newline="", encoding="utf-8") as fh:
        cases = list(csv.DictReader(fh))
    evidence = json.load(open(ROOT / "eval" / "evidence_map.json"))
    retriever = get_retriever()

    rows = []
    abstain = []
    for case in cases:
        if case["split"] != split or case["case_id"] not in evidence:
            continue
        query = f"{case['method']} {case['path']} HTTP {case['observed_status']} {case['input_issue']}"
        result = retriever.search(query, api_name=None, path=case["path"])
        predicted = [chunk["chunk_id"] for chunk in result.chunks]
        expected = evidence[case["case_id"]]
        if not expected:
            abstain.append((case["case_id"], result.version_mismatch or result.insufficient))
            continue
        rows.append((case["case_id"], recall5(predicted, expected), mrr(predicted, expected)))

    print("retrieval cases:", len(rows))
    if rows:
        print("Recall@5:", round(sum(row[1] for row in rows) / len(rows), 3))
        print("MRR:", round(sum(row[2] for row in rows) / len(rows), 3))
    print("abstention cases:", len(abstain), "correct:", sum(1 for _, ok in abstain if ok))
    for row in rows:
        print(row)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dev")
