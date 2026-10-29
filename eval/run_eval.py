import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def load_questions(path: Path, split: str | None = None) -> list[dict[str, Any]]:
    questions = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            question = row.get("question")
            expected = row.get("expected_chunk_ids")
            if not isinstance(question, str) or not question.strip():
                raise ValueError(f"{path}:{line_number} has no question")
            if not isinstance(expected, list) or not all(
                isinstance(chunk_id, str) for chunk_id in expected
            ):
                raise ValueError(
                    f"{path}:{line_number} expected_chunk_ids must be a string list"
                )
            if split is None or row.get("split") == split:
                questions.append(row)
    if not questions:
        raise ValueError(f"No evaluation questions found in {path}")
    return questions


def recall_at_k(predicted: list[str], expected: set[str], k: int = 5) -> float:
    if not expected:
        raise ValueError("Recall@k requires at least one expected chunk")
    return len(set(predicted[:k]) & expected) / len(expected)


def mean_reciprocal_rank(
    predicted: list[str], expected: set[str], k: int = 5
) -> float:
    for rank, chunk_id in enumerate(predicted[:k], start=1):
        if chunk_id in expected:
            return 1.0 / rank
    return 0.0


async def evaluate(
    questions_path: Path, split: str | None = None
) -> dict[str, dict[str, float | int | None]]:
    pipeline_module = importlib.import_module("app.ai.rag.pipeline")
    stats = await pipeline_module.vector_store.stats()
    if stats["chunk_count"] == 0:
        raise RuntimeError(
            "The Chroma knowledge base is empty; upload and ingest documents before evaluation."
        )
    retrieve = pipeline_module.retrieve
    questions = load_questions(questions_path, split)
    summaries: dict[str, dict[str, float | int | None]] = {}
    for mode in ("dense", "keyword", "hybrid"):
        recalls = []
        reciprocal_ranks = []
        abstentions = 0
        for row in questions:
            hits = await retrieve(row["question"], mode=mode, k=5)
            predicted = [hit.chunk_id for hit in hits]
            expected = set(row["expected_chunk_ids"])
            if not expected:
                abstentions += 1
                continue
            recalls.append(recall_at_k(predicted, expected))
            reciprocal_ranks.append(mean_reciprocal_rank(predicted, expected))
        summaries[mode] = {
            "retrieval_cases": len(recalls),
            "abstention_cases": abstentions,
            "recall_at_5": (
                sum(recalls) / len(recalls) if recalls else None
            ),
            "mrr": (
                sum(reciprocal_ranks) / len(reciprocal_ranks)
                if reciprocal_ranks
                else None
            ),
        }
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate dense, keyword, and hybrid RAG retrieval."
    )
    parser.add_argument(
        "questions",
        nargs="?",
        type=Path,
        default=ROOT / "eval" / "eval_questions.jsonl",
    )
    parser.add_argument("--split", choices=("dev", "heldout"))
    args = parser.parse_args()
    results = asyncio.run(evaluate(args.questions, args.split))
    for mode, metrics in results.items():
        recall = metrics["recall_at_5"]
        mrr = metrics["mrr"]
        print(
            f"{mode}: Recall@5={recall:.3f}" if recall is not None
            else f"{mode}: Recall@5=n/a",
            f"MRR={mrr:.3f}" if mrr is not None else "MRR=n/a",
            f"cases={metrics['retrieval_cases']}",
            f"abstentions={metrics['abstention_cases']}",
        )


if __name__ == "__main__":
    main()
