import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.config import settings


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Open the local run database, committing on success and closing always."""
    settings.runs_db.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.runs_db)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()
