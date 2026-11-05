"""Diagnosis run history.

Stored in Supabase Postgres when DATABASE_URL is configured, with a local
SQLite file (data/runs.sqlite3) as the offline fallback. Both stores keep the
same columns, so a run saved under one backend stays readable if the other is
selected later.
"""

import asyncio
import json
import logging
import sqlite3
import time
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_PG_TABLE = """
create table if not exists runs (
    run_id text primary key,
    created_at timestamptz not null default now(),
    method text,
    url text,
    status int,
    error_type text,
    retrieved_ids jsonb not null default '[]'::jsonb,
    provider text,
    diagnosis text,
    patch jsonb
)
"""

_SQLITE_TABLE = """
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

_pg_ready = False


def uses_supabase() -> bool:
    """True when run history is (or will be) stored in Supabase Postgres."""
    return settings.database_url_configured


def _get_sqlite_connection() -> sqlite3.Connection:
    settings.runs_db.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(settings.runs_db)


def _ensure_pg_table() -> None:
    global _pg_ready
    if _pg_ready:
        return
    import psycopg

    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        connection.execute(_PG_TABLE)
    _pg_ready = True


def _save_sqlite(run: dict[str, Any]) -> None:
    with _get_sqlite_connection() as connection:
        connection.execute(_SQLITE_TABLE)
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


def _save_supabase(run: dict[str, Any]) -> None:
    import psycopg

    _ensure_pg_table()
    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into runs (
                run_id, method, url, status, error_type,
                retrieved_ids, provider, diagnosis, patch
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (run_id) do update set
                method = excluded.method,
                url = excluded.url,
                status = excluded.status,
                error_type = excluded.error_type,
                retrieved_ids = excluded.retrieved_ids,
                provider = excluded.provider,
                diagnosis = excluded.diagnosis,
                patch = excluded.patch
            """,
            (
                run["run_id"],
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


def list_runs(limit: int = 8) -> list[dict[str, Any]]:
    """Most recent runs (newest first) for the Recent Tests sidebar."""
    try:
        if uses_supabase():
            import psycopg

            with psycopg.connect(settings.database_url, autocommit=True) as conn:
                rows = conn.execute(
                    """
                    select run_id, created_at, method, url, status, provider
                    from runs order by created_at desc limit %s
                    """,
                    (limit,),
                ).fetchall()
            return [
                {
                    "run_id": row[0],
                    "created_at": row[1].isoformat(),
                    "method": row[2],
                    "url": row[3],
                    "status": row[4],
                    "provider": row[5],
                }
                for row in rows
            ]
        with _get_sqlite_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select run_id, created_at, method, url, status, provider
                from runs order by created_at desc limit ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "created_at": row["created_at"],
                "method": row["method"],
                "url": row["url"],
                "status": row["status"],
                "provider": row["provider"],
            }
            for row in rows
        ]
    except Exception:
        logger.warning("Could not read run history", exc_info=True)
        return []


def save(run: dict[str, Any]) -> None:
    """Persist one diagnosis run. Never raises: a history failure must not
    break an already-streamed diagnosis."""
    try:
        if uses_supabase():
            _save_supabase(run)
        else:
            _save_sqlite(run)
    except Exception:
        logger.warning(
            "Could not save run history (%s backend); continuing without it",
            "supabase" if uses_supabase() else "sqlite",
            exc_info=True,
        )


def initialize() -> None:
    """Create the history table on the configured backend (idempotent)."""
    if uses_supabase():
        _ensure_pg_table()
    else:
        with _get_sqlite_connection() as connection:
            connection.execute(_SQLITE_TABLE)


async def initialize_async() -> None:
    await asyncio.to_thread(initialize)
