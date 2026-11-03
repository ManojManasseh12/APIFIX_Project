import json
import time

from app.db.connection import get_connection
from app.db.schema import initialize_schema


def init() -> None:
    initialize_schema()


def save(run: dict) -> None:
    """Persist one diagnosis run, replacing any previous row with the same id."""
    init()
    with get_connection() as connection:
        connection.execute(
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
