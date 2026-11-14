"""Ask API chat history.

Every answered Ask turn (RAG or tool) is persisted here, mirroring how
diagnosis runs are stored in app/db/runs.py: Supabase Postgres when
DATABASE_URL is configured, with a local SQLite file (data/ask.sqlite3)
as the offline fallback. Both stores keep the same columns.
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
create table if not exists ask_turns (
    turn_id text primary key,
    created_at timestamptz not null default now(),
    user_id text,
    session_id text,
    question text,
    answer text,
    provider text,
    tool text,
    tool_label text,
    citations jsonb not null default '[]'::jsonb
)
"""

_SQLITE_TABLE = """
create table if not exists ask_turns (
    turn_id text primary key,
    created_at real,
    user_id text,
    session_id text,
    question text,
    answer text,
    provider text,
    tool text,
    tool_label text,
    citations text
)
"""

_pg_ready = False


def uses_supabase() -> bool:
    """True when Ask history is (or will be) stored in Supabase Postgres."""
    return settings.database_url_configured


def _get_sqlite_connection() -> sqlite3.Connection:
    settings.runs_db.parent.mkdir(parents=True, exist_ok=True)
    sqlite_path = settings.runs_db.parent / "ask.sqlite3"
    return sqlite3.connect(sqlite_path)


def _ensure_pg_table() -> None:
    global _pg_ready
    if _pg_ready:
        return
    import psycopg

    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        connection.execute(_PG_TABLE)
        # Tables created before authentication keep working: they gain the
        # column here, existing rows stay NULL (= legacy/pre-auth data).
        connection.execute(
            "alter table ask_turns add column if not exists user_id text"
        )
    _pg_ready = True


def _ensure_sqlite_columns() -> None:
    with _get_sqlite_connection() as connection:
        connection.execute(_SQLITE_TABLE)
        names = {
            row[1]
            for row in connection.execute("pragma table_info(ask_turns)").fetchall()
        }
        if "user_id" not in names:
            connection.execute("alter table ask_turns add column user_id text")


def _user_filter(user_id: str | None) -> str:
    """WHERE fragment scoping turns to one user (NULL = legacy/pre-auth)."""
    return "where user_id = ? or user_id is null" if user_id else ""


def _save_sqlite(turn: dict[str, Any]) -> None:
    _ensure_sqlite_columns()
    with _get_sqlite_connection() as connection:
        connection.execute(
            "insert or replace into ask_turns values (?,?,?,?,?,?,?,?,?,?)",
            (
                turn["turn_id"],
                time.time(),
                turn.get("user_id"),
                turn.get("session_id"),
                turn.get("question"),
                turn.get("answer"),
                turn.get("provider"),
                turn.get("tool"),
                turn.get("tool_label"),
                json.dumps(turn.get("citations", [])),
            ),
        )


def _save_supabase(turn: dict[str, Any]) -> None:
    import psycopg

    _ensure_pg_table()
    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into ask_turns (
                turn_id, user_id, session_id, question, answer,
                provider, tool, tool_label, citations
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (turn_id) do update set
                user_id = excluded.user_id,
                session_id = excluded.session_id,
                question = excluded.question,
                answer = excluded.answer,
                provider = excluded.provider,
                tool = excluded.tool,
                tool_label = excluded.tool_label,
                citations = excluded.citations
            """,
            (
                turn["turn_id"],
                turn.get("user_id"),
                turn.get("session_id"),
                turn.get("question"),
                turn.get("answer"),
                turn.get("provider"),
                turn.get("tool"),
                turn.get("tool_label"),
                json.dumps(turn.get("citations", [])),
            ),
        )


def save(turn: dict[str, Any]) -> None:
    """Persist one Ask turn. Never raises: a history failure must not
    break an already-streamed answer."""
    try:
        backend = "supabase" if uses_supabase() else "sqlite"
        if backend == "supabase":
            _save_supabase(turn)
        else:
            _save_sqlite(turn)
    except Exception:
        logger.warning(
            "Could not save Ask history; continuing without it", exc_info=True
        )


def list_turns(limit: int = 10, user_id: str | None = None) -> list[dict[str, Any]]:
    """Recent Ask turns (newest first) for the Ask history panel.

    ``user_id`` scopes the list to one authenticated user; None returns
    everything (used when authentication is disabled).
    """
    try:
        if uses_supabase():
            import psycopg

            where = "where user_id = %s or user_id is null" if user_id else ""
            with psycopg.connect(settings.database_url, autocommit=True) as conn:
                rows = conn.execute(
                    f"""
                    select turn_id, created_at, question, answer, provider, tool, tool_label, citations
                    from ask_turns {where} order by created_at desc limit %s
                    """,
                    ((user_id, limit) if user_id else (limit,)),
                ).fetchall()
            created_ats = [row[1].isoformat() for row in rows]
        else:
            from datetime import datetime, timezone

            _ensure_sqlite_columns()
            with _get_sqlite_connection() as conn:
                rows = conn.execute(
                    f"""
                    select turn_id, created_at, question, answer, provider, tool, tool_label, citations
                    from ask_turns {_user_filter(user_id)} order by created_at desc limit ?
                    """,
                    ((user_id, limit) if user_id else (limit,)),
                ).fetchall()
            # SQLite keeps seconds-since-epoch; normalise to ISO like Postgres.
            created_ats = [
                datetime.fromtimestamp(row[1], tz=timezone.utc).isoformat()
                if isinstance(row[1], (int, float))
                else str(row[1])
                for row in rows
            ]

        return [
            {
                "turn_id": row[0],
                "created_at": created_ats[index],
                "question": row[2],
                "answer": row[3],
                "provider": row[4],
                "tool": row[5],
                "tool_label": row[6],
                "citations": json.loads(row[7]) if row[7] else [],
            }
            for index, row in enumerate(rows)
        ]
    except Exception:
        logger.warning("Could not read Ask history", exc_info=True)
        return []


def initialize() -> None:
    """Create the Ask history table on the configured backend (idempotent)."""
    if uses_supabase():
        _ensure_pg_table()
    else:
        _ensure_sqlite_columns()


async def initialize_async() -> None:
    await asyncio.to_thread(initialize)
