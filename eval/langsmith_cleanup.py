"""Delete old APIFix experiments (and optionally datasets) from LangSmith.

LangSmith keeps every experiment and trace forever unless you remove it. This
script is a manual housekeeping tool; nothing in the app calls it.

It is a **dry run by default** — pass ``--apply`` to actually delete:

    python eval/langsmith_cleanup.py --days 30
    python eval/langsmith_cleanup.py --days 30 --datasets --apply

Only resources whose names start with ``--prefix`` (default ``apifix``) are
considered, so a shared LangSmith workspace is left alone. Tracing must be
enabled with a key; otherwise the script exits without contacting LangSmith.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.observability import get_client, langsmith_enabled  # noqa: E402


def _start_time(project: Any) -> datetime | None:
    value = getattr(project, "start_time", None)
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _created_at(dataset: Any) -> datetime | None:
    value = getattr(dataset, "created_at", None)
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete old APIFix experiments/datasets from LangSmith."
    )
    parser.add_argument(
        "--days", type=int, default=30, help="Keep resources newer than N days."
    )
    parser.add_argument(
        "--prefix", default="apifix", help="Only touch names starting with this."
    )
    parser.add_argument(
        "--datasets",
        action="store_true",
        help="Also delete matching datasets (and their examples).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without it the script only lists what it would remove.",
    )
    args = parser.parse_args()

    if args.days < 0:
        print("--days must be zero or positive.", file=sys.stderr)
        return 2
    if not langsmith_enabled():
        print(
            "LangSmith is not enabled. Set LANGSMITH_TRACING=true and "
            "LANGSMITH_API_KEY in .env. No data was sent.",
            file=sys.stderr,
        )
        return 1

    client = get_client()
    if client is None:
        print("Could not create a LangSmith client.", file=sys.stderr)
        return 1

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    prefix = args.prefix.strip()
    mode = "DELETE" if args.apply else "would delete (dry run)"

    deleted_projects = 0
    deleted_datasets = 0
    try:
        for project in client.list_projects():
            name = project.name or ""
            started = _start_time(project)
            if not name.startswith(prefix):
                continue
            if started is None or started >= cutoff:
                continue
            print(f"{mode} experiment {name!r} (started {started.isoformat()})")
            if args.apply:
                client.delete_project(project_id=project.id)
                deleted_projects += 1

        if args.datasets:
            for dataset in client.list_datasets():
                name = dataset.name or ""
                created = _created_at(dataset)
                if not name.startswith(prefix):
                    continue
                if created is None or created >= cutoff:
                    continue
                print(f"{mode} dataset {name!r} (created {created.isoformat()})")
                if args.apply:
                    client.delete_dataset(dataset_id=dataset.id)
                    deleted_datasets += 1
    finally:
        client.flush()

    if args.apply:
        print(
            f"Deleted {deleted_projects} experiment(s) and "
            f"{deleted_datasets} dataset(s)."
        )
    else:
        print("Dry run only. Re-run with --apply to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
