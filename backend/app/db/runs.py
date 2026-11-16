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
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from app.core.config import settings

logger = logging.getLogger(__name__)

_PG_TABLE = """
create table if not exists runs (
    run_id text primary key,
    created_at timestamptz not null default now(),
    user_id text,
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
    user_id text,
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
        # Tables created before authentication keep working: they gain the
        # column here, existing rows stay NULL (= legacy/pre-auth data).
        connection.execute("alter table runs add column if not exists user_id text")
    _pg_ready = True


def _ensure_sqlite_columns() -> None:
    with _get_sqlite_connection() as connection:
        connection.execute(_SQLITE_TABLE)
        names = {
            row[1] for row in connection.execute("pragma table_info(runs)").fetchall()
        }
        if "user_id" not in names:
            connection.execute("alter table runs add column user_id text")


def _user_filter(user_id: str | None) -> str:
    """WHERE fragment scoping rows to one user.

    Legacy rows saved before authentication have user_id NULL; they belong
    to the project owner, so a scoped list keeps showing them.
    """
    return "where user_id = ? or user_id is null" if user_id else ""


def _save_sqlite(run: dict[str, Any]) -> None:
    _ensure_sqlite_columns()
    with _get_sqlite_connection() as connection:
        connection.execute(
            """
            insert or replace into runs (
                run_id, created_at, user_id, method, url, status, error_type,
                retrieved_ids, provider, diagnosis, patch
            ) values (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run["run_id"],
                time.time(),
                run.get("user_id"),
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
                run_id, user_id, method, url, status, error_type,
                retrieved_ids, provider, diagnosis, patch
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (run_id) do update set
                user_id = excluded.user_id,
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
                run.get("user_id"),
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


def list_runs(limit: int = 8, user_id: str | None = None) -> list[dict[str, Any]]:
    """Most recent runs (newest first) for the Recent Tests sidebar.

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
                    select run_id, created_at, method, url, status, provider
                    from runs {where} order by created_at desc limit %s
                    """,
                    ((user_id, limit) if user_id else (limit,)),
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
        _ensure_sqlite_columns()
        with _get_sqlite_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                select run_id, created_at, method, url, status, provider
                from runs {_user_filter(user_id)} order by created_at desc limit ?
                """,
                ((user_id, limit) if user_id else (limit,)),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                # SQLite keeps seconds-since-epoch; normalise to ISO like
                # Postgres so the frontend's relative-time rendering agrees.
                "created_at": (
                    datetime.fromtimestamp(row["created_at"], tz=timezone.utc).isoformat()
                    if isinstance(row["created_at"], (int, float))
                    else str(row["created_at"])
                ),
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


def _created_day(value: Any) -> date | None:
    if isinstance(value, datetime):
        created_at = value
    elif isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).date()
    elif isinstance(value, str):
        try:
            created_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(timezone.utc).date()


def _url_path(url: str | None) -> str:
    if not url:
        return "(unknown path)"
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return url.split("?", 1)[0]


def dashboard_stats(user_id: str | None = None) -> dict[str, Any]:
    """Aggregate persisted diagnosis runs for the live dashboard.

    ``user_id`` scopes every aggregate to one authenticated user.
    """
    if uses_supabase():
        import psycopg

        where = "where user_id = %s or user_id is null" if user_id else ""
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            rows = conn.execute(
                f"""
                select created_at, method, url, status, provider
                from runs {where}
                order by created_at desc
                """,
                (user_id,) if user_id else (),
            ).fetchall()
    else:
        _ensure_sqlite_columns()
        with _get_sqlite_connection() as conn:
            rows = conn.execute(
                f"""
                select created_at, method, url, status, provider
                from runs {_user_filter(user_id)}
                order by created_at desc
                """,
                (user_id,) if user_id else (),
            ).fetchall()

    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=6)
    daily_counts: Counter[date] = Counter()
    status_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    endpoints: dict[tuple[str, str], dict[str, Any]] = {}
    failure_count = 0

    for row in rows:
        created_at, method, url, status, provider = row
        run_day = _created_day(created_at)
        if run_day is not None and first_day <= run_day <= today:
            daily_counts[run_day] += 1

        if isinstance(status, int):
            status_group = f"{status // 100}xx"
            status_counts[status_group] += 1
            failure_count += status >= 400
        else:
            status_group = "No response"
            status_counts[status_group] += 1

        if provider:
            provider_counts[str(provider)] += 1

        method_name = str(method or "—").upper()
        path = _url_path(url)
        key = (method_name, path)
        endpoint = endpoints.get(key)
        if endpoint is None:
            endpoints[key] = {
                "method": method_name,
                "path": path,
                "latest_status": status,
                "runs": 1,
            }
        else:
            endpoint["runs"] += 1

    return {
        "total_runs": len(rows),
        "failure_count": failure_count,
        "daily_runs": [
            {
                "date": (first_day + timedelta(days=offset)).isoformat(),
                "count": daily_counts[first_day + timedelta(days=offset)],
            }
            for offset in range(7)
        ],
        "status_counts": [
            {"status": status, "count": count}
            for status, count in sorted(status_counts.items())
        ],
        "provider_usage": [
            {"provider": provider, "count": count}
            for provider, count in provider_counts.most_common()
        ],
        "endpoints": sorted(
            endpoints.values(), key=lambda endpoint: endpoint["runs"], reverse=True
        )[:5],
        "recent_runs": list_runs(limit=5, user_id=user_id),
    }


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
        _ensure_sqlite_columns()


async def initialize_async() -> None:
    await asyncio.to_thread(initialize)
