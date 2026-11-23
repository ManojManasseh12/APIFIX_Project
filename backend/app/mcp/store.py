"""Per-user MCP connection records (encrypted OAuth tokens).

Mirrors ``app/db/ask_chats.py``: Supabase Postgres when ``DATABASE_URL`` is
configured, a local SQLite file otherwise. Both stores keep the same
columns:

    user_id, provider, account_email, scopes,
    access_token, refresh_token, expires_at, created_at, updated_at

Tokens are encrypted by this module before they reach the database and
decrypted on read, so callers only ever see plaintext in memory.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.mcp import crypto

_PG_TABLE = """
create table if not exists mcp_connections (
    user_id text not null,
    provider text not null,
    account_email text,
    scopes text,
    access_token text,
    refresh_token text,
    expires_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (user_id, provider)
)
"""

_SQLITE_TABLE = """
create table if not exists mcp_connections (
    user_id text not null,
    provider text not null,
    account_email text,
    scopes text,
    access_token text,
    refresh_token text,
    expires_at real,
    created_at real,
    updated_at real,
    primary key (user_id, provider)
)
"""

_pg_ready = False


def uses_supabase() -> bool:
    return settings.database_url_configured


def _sqlite_path():
    settings.runs_db.parent.mkdir(parents=True, exist_ok=True)
    return settings.runs_db.parent / "mcp.sqlite3"


def _sqlite() -> sqlite3.Connection:
    return sqlite3.connect(_sqlite_path())


def _ensure_pg() -> None:
    global _pg_ready
    if _pg_ready:
        return
    import psycopg

    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        connection.execute(_PG_TABLE)
    _pg_ready = True


def _ensure_sqlite() -> None:
    with _sqlite() as connection:
        connection.execute(_SQLITE_TABLE)


def initialize() -> None:
    """Create the table on the configured backend (idempotent)."""
    if uses_supabase():
        _ensure_pg()
    else:
        _ensure_sqlite()


def save(record: dict[str, Any]) -> None:
    """Insert or replace one user's connection, encrypting both tokens."""
    access_token = crypto.encrypt(record.get("access_token"))
    refresh_token = crypto.encrypt(record.get("refresh_token"))
    scopes = " ".join(record.get("scopes") or [])
    expires_at: datetime | None = record.get("expires_at")
    now = datetime.now(timezone.utc)

    if uses_supabase():
        import psycopg

        _ensure_pg()
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            conn.execute(
                """
                insert into mcp_connections (
                    user_id, provider, account_email, scopes,
                    access_token, refresh_token, expires_at, updated_at
                ) values (%s, %s, %s, %s, %s, %s, %s, now())
                on conflict (user_id, provider) do update set
                    -- A refresh response has no id_token, so keep the email
                    -- that the first consent stored.
                    account_email = coalesce(
                        excluded.account_email, mcp_connections.account_email
                    ),
                    scopes = excluded.scopes,
                    access_token = excluded.access_token,
                    refresh_token = coalesce(
                        excluded.refresh_token, mcp_connections.refresh_token
                    ),
                    expires_at = excluded.expires_at,
                    updated_at = now()
                """,
                (
                    record["user_id"],
                    record["provider"],
                    record.get("account_email"),
                    scopes,
                    access_token,
                    refresh_token,
                    expires_at,
                ),
            )
        return

    _ensure_sqlite()
    epoch = expires_at.timestamp() if expires_at else None
    with _sqlite() as conn:
        existing = conn.execute(
            "select refresh_token, account_email from mcp_connections "
            "where user_id = ? and provider = ?",
            (record["user_id"], record["provider"]),
        ).fetchone()
        # Google only returns a refresh token on the first consent; keep the
        # stored one when a later save does not include a new value.
        kept_refresh = refresh_token or (existing[0] if existing else None)
        # A refresh response carries no id_token, so the account email must
        # survive it too.
        kept_email = record.get("account_email") or (existing[1] if existing else None)
        conn.execute(
            """
            insert or replace into mcp_connections (
                user_id, provider, account_email, scopes,
                access_token, refresh_token, expires_at, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, coalesce(
                (select created_at from mcp_connections
                 where user_id = ? and provider = ?), ?
            ), ?)
            """,
            (
                record["user_id"],
                record["provider"],
                kept_email,
                scopes,
                access_token,
                kept_refresh,
                epoch,
                record["user_id"],
                record["provider"],
                now.timestamp(),
                now.timestamp(),
            ),
        )


def get(user_id: str, provider: str) -> dict[str, Any] | None:
    """One connection with decrypted tokens, or None."""
    if uses_supabase():
        import psycopg

        _ensure_pg()
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            row = conn.execute(
                """
                select user_id, provider, account_email, scopes,
                       access_token, refresh_token, expires_at
                from mcp_connections
                where user_id = %s and provider = %s
                """,
                (user_id, provider),
            ).fetchone()
    else:
        _ensure_sqlite()
        with _sqlite() as conn:
            row = conn.execute(
                """
                select user_id, provider, account_email, scopes,
                       access_token, refresh_token, expires_at
                from mcp_connections
                where user_id = ? and provider = ?
                """,
                (user_id, provider),
            ).fetchone()

    if not row:
        return None

    expires_at = row[6]
    if isinstance(expires_at, (int, float)):
        expires_at = datetime.fromtimestamp(expires_at, tz=timezone.utc)

    return {
        "user_id": row[0],
        "provider": row[1],
        "account_email": row[2],
        "scopes": (row[3] or "").split(),
        "access_token": crypto.decrypt(row[4]),
        "refresh_token": crypto.decrypt(row[5]),
        "expires_at": expires_at,
    }


def delete(user_id: str, provider: str) -> bool:
    """Remove a user's connection. True when a row was deleted."""
    if uses_supabase():
        import psycopg

        _ensure_pg()
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            result = conn.execute(
                "delete from mcp_connections where user_id = %s and provider = %s",
                (user_id, provider),
            )
            return result.rowcount > 0

    _ensure_sqlite()
    with _sqlite() as conn:
        cursor = conn.execute(
            "delete from mcp_connections where user_id = ? and provider = ?",
            (user_id, provider),
        )
        return cursor.rowcount > 0


def is_expired(record: dict[str, Any], skew_s: int = 60) -> bool:
    """True when the access token is missing or about to expire."""
    expires_at = record.get("expires_at")
    if not record.get("access_token") or not isinstance(expires_at, datetime):
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at.timestamp() - skew_s <= time.time()
