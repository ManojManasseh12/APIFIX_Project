import asyncio

from app.db.connection import get_connection

RUNS_TABLE = """
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


def initialize_schema() -> None:
    """Create the run-history table if it does not exist yet."""
    with get_connection() as connection:
        connection.execute(RUNS_TABLE)


async def initialize_schema_async() -> None:
    """Async wrapper so FastAPI startup does not block the event loop."""
    await asyncio.to_thread(initialize_schema)
