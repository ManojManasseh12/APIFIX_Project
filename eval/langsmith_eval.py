"""Upload the local evaluation sets to LangSmith and score them there.

This complements ``run_eval.py``: that script is fully offline and prints
Recall@5/MRR. This one records the same cases as a LangSmith dataset so you can
compare experiments over time in the LangSmith UI.

Tracing must be explicitly enabled — the script refuses to run otherwise:

    LANGSMITH_TRACING=true
    LANGSMITH_API_KEY=lsv2_...
    LANGSMITH_PROJECT=apifix-ai

Examples:

    python eval/langsmith_eval.py --dataset routing
    python eval/langsmith_eval.py --dataset retrieval --split dev
    python eval/langsmith_eval.py --dataset routing --recreate

``--recreate`` replaces the dataset's examples with the current JSONL files.
Without it, an existing dataset is reused so past experiments stay comparable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from langsmith.evaluation import EvaluationResult  # noqa: E402

from app.observability import get_client, langsmith_enabled  # noqa: E402

EVAL_DIR = ROOT / "eval"

DATASETS = {
    "retrieval": {
        "name": "apifix-retrieval",
        "path": EVAL_DIR / "eval_questions.jsonl",
        "description": (
            "APIFix retrieval cases: question -> expected chunk ids. "
            "Built from eval/eval_questions.jsonl."
        ),
    },
    "routing": {
        "name": "apifix-routing",
        "path": EVAL_DIR / "routing_questions.jsonl",
        "description": (
            "APIFix router cases: question -> expected route "
            "(rag or tool:<name>). Built from eval/routing_questions.jsonl."
        ),
    },
}


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            rows.append(row)
    if not rows:
        raise ValueError(f"No evaluation rows found in {path}")
    return rows


def to_example(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    question = row.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError(f"{kind} row has no question: {row}")
    metadata = {"split": row.get("split") or "dev"}
    if kind == "retrieval":
        expected = row.get("expected_chunk_ids")
        if not isinstance(expected, list) or not all(
            isinstance(item, str) for item in expected
        ):
            raise ValueError(f"retrieval row needs expected_chunk_ids: {row}")
        return {
            "inputs": {"question": question},
            "outputs": {"expected_chunk_ids": expected},
            "metadata": metadata,
        }
    expected_route = row.get("expected_route")
    if not isinstance(expected_route, str) or not expected_route:
        raise ValueError(f"routing row needs expected_route: {row}")
    return {
        "inputs": {"question": question},
        "outputs": {"expected_route": expected_route},
        "metadata": metadata,
    }


def ensure_dataset(
    client: Any, kind: str, rows: list[dict[str, Any]], recreate: bool
) -> str:
    spec = DATASETS[kind]
    name = spec["name"]
    examples = [to_example(kind, row) for row in rows]

    if recreate and client.has_dataset(dataset_name=name):
        client.delete_dataset(dataset_name=name)
        print(f"Deleted existing dataset {name!r} (--recreate)")

    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
        print(
            f"Using existing dataset {name!r} "
            f"({dataset.example_count} examples). "
            "Pass --recreate to replace its examples."
        )
        return name

    dataset = client.create_dataset(name, description=spec["description"])
    client.create_examples(dataset_id=dataset.id, examples=examples)
    print(f"Created dataset {name!r} with {len(examples)} examples")
    return name


# ------------------------------------------------------------------- targets


def retrieval_target(inputs: dict[str, Any]) -> dict[str, Any]:
    from app.ai.rag.pipeline import pipeline

    hits = asyncio.run(pipeline.retrieve(inputs["question"], mode="hybrid", k=5))
    return {"chunk_ids": [hit.chunk_id for hit in hits]}


def routing_target(inputs: dict[str, Any]) -> dict[str, Any]:
    from app.ai.router import route_question

    decision = asyncio.run(route_question(inputs["question"]))
    return {"route": decision.route, "via": decision.via}


# ---------------------------------------------------------------- evaluators


def _expected_outputs(example: Any) -> dict[str, Any]:
    return getattr(example, "outputs", None) or {}


def _run_outputs(run: Any) -> dict[str, Any]:
    return getattr(run, "outputs", None) or {}


def recall_at_5(run: Any, example: Any) -> EvaluationResult:
    expected = set(_expected_outputs(example).get("expected_chunk_ids") or [])
    predicted = list(_run_outputs(run).get("chunk_ids") or [])
    if not expected:
        return EvaluationResult(
            key="recall_at_5", score=None, comment="abstention case"
        )
    score = len(set(predicted[:5]) & expected) / len(expected)
    return EvaluationResult(key="recall_at_5", score=score)


def mrr_at_5(run: Any, example: Any) -> EvaluationResult:
    expected = set(_expected_outputs(example).get("expected_chunk_ids") or [])
    predicted = list(_run_outputs(run).get("chunk_ids") or [])
    if not expected:
        return EvaluationResult(key="mrr_at_5", score=None, comment="abstention case")
    score = 0.0
    for rank, chunk_id in enumerate(predicted[:5], start=1):
        if chunk_id in expected:
            score = 1.0 / rank
            break
    return EvaluationResult(key="mrr_at_5", score=score)


def route_exact_match(run: Any, example: Any) -> EvaluationResult:
    expected = _expected_outputs(example).get("expected_route")
    actual = _run_outputs(run).get("route")
    score = 1.0 if expected and actual == expected else 0.0
    comment = None if score else f"expected {expected!r}, got {actual!r}"
    return EvaluationResult(key="route_exact_match", score=score, comment=comment)


EVALUATORS: dict[str, tuple[Callable[..., EvaluationResult], ...]] = {
    "retrieval": (recall_at_5, mrr_at_5),
    "routing": (route_exact_match,),
}

TARGETS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "retrieval": retrieval_target,
    "routing": routing_target,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run an APIFix evaluation set in LangSmith."
    )
    parser.add_argument("--dataset", choices=sorted(DATASETS), required=True)
    parser.add_argument(
        "--split",
        choices=("dev", "heldout"),
        help="Only upload rows with this split.",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete and re-upload the dataset examples before running.",
    )
    parser.add_argument("--experiment-prefix", default=None)
    args = parser.parse_args()

    if not langsmith_enabled():
        print(
            "LangSmith is not enabled. Set these in .env and restart your shell:\n"
            "  LANGSMITH_TRACING=true\n"
            "  LANGSMITH_API_KEY=lsv2_...\n"
            "  LANGSMITH_PROJECT=apifix-ai\n"
            "No data was sent.",
            file=sys.stderr,
        )
        return 1

    client = get_client()
    if client is None:
        print("Could not create a LangSmith client.", file=sys.stderr)
        return 1

    rows = load_rows(DATASETS[args.dataset]["path"])
    if args.split:
        rows = [row for row in rows if (row.get("split") or "dev") == args.split]
        if not rows:
            print(f"No rows with split={args.split!r}.", file=sys.stderr)
            return 1

    try:
        if args.dataset == "retrieval":
            from app.ai.rag.pipeline import ensure_seeded

            stats = asyncio.run(ensure_seeded())
            if stats["chunk_count"] == 0:
                print(
                    "The knowledge base is empty. Upload/ingest documents first.",
                    file=sys.stderr,
                )
                return 1

        dataset_name = ensure_dataset(
            client, args.dataset, rows, recreate=args.recreate
        )
        prefix = args.experiment_prefix or f"apifix-{args.dataset}"
        if args.split:
            prefix = f"{prefix}-{args.split}"

        from langsmith import evaluate

        results = evaluate(
            TARGETS[args.dataset],
            data=dataset_name,
            evaluators=list(EVALUATORS[args.dataset]),
            experiment_prefix=prefix,
            description=(
                f"APIFix {args.dataset} evaluation"
                + (f" (split={args.split})" if args.split else "")
            ),
            max_concurrency=1,
            client=client,
        )
        print(f"Experiment: {results.experiment_name}")
        print(f"Compare: {results.url}")
        try:
            frame = results.to_pandas()
            score_columns = [
                column
                for column in frame.columns
                if column.startswith("feedback.")
            ]
            for column in score_columns:
                mean = frame[column].dropna().mean()
                print(f"{column}: mean={mean:.3f}")
        except Exception:
            print("Open the experiment URL for per-case scores.")
    finally:
        client.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
