import json
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parents[4] / "data" / "runs.sqlite3"


def init() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as conn:
        conn.execute(
            """
            create table if not exists runs (
                run_id text primary key,
                created_at real,
                method text,
                url text,
                status int,
                error_type text,
                retrieved_ids text,
                provider text,
                diagnosis text,
                patch text
            )
            """
        )


def save(run: dict) -> None:
    init()
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "insert or replace into runs values (?,?,?,?,?,?,?,?,?,?)",
            (
                run["run_id"],
                time.time(),
                run.get("method"),
                run.get("url"),
                run.get("status"),
                run.get("error_type"),
                json.dumps(run.get("retrieved_ids", [])),
                run.get("provider"),
                run.get("diagnosis"),
                json.dumps(run.get("patch")),
            ),
        )

