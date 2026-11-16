"""Editable system prompts (prompt templates).

Three prompts drive the product — ``diagnosis``, ``ask`` and ``tool`` —
with defaults in app/ai/prompts.py. This module stores optional
overrides in a ``prompt_templates`` table (Supabase Postgres when
DATABASE_URL is configured, data/prompts.sqlite3 otherwise), one active
row per purpose, so prompts can be tuned from the UI without a deploy.

Every read is best-effort: a database problem falls back to the
built-in default so diagnosis and Ask keep working.
"""

import logging
import sqlite3
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# The three prompt slots the backend uses (app/ai/prompts.py defaults).
PURPOSES: tuple[str, ...] = ("diagnosis", "ask", "tool")

_MIN_LENGTH = 20
_MAX_LENGTH = 12000

_PG_TABLE = """
create table if not exists prompt_templates (
    purpose text primary key,
    name text not null default 'custom',
    template text not null,
    updated_at timestamptz not null default now()
)
"""

_SQLITE_TABLE = """
create table if not exists prompt_templates (
    purpose text primary key,
    name text not null default 'custom',
    template text not null,
    updated_at real
)
"""

_pg_ready = False


def uses_supabase() -> bool:
    """True when prompt overrides are (or will be) stored in Supabase."""
    return settings.database_url_configured


def _get_sqlite_connection() -> sqlite3.Connection:
    settings.runs_db.parent.mkdir(parents=True, exist_ok=True)
    sqlite_path = settings.runs_db.parent / "prompts.sqlite3"
    return sqlite3.connect(sqlite_path)


def is_valid_purpose(purpose: str) -> bool:
    return purpose in PURPOSES


def validate_text(text: str) -> str | None:
    """Return an error message, or None when the template is acceptable."""
    stripped = text.strip()
    if len(stripped) < _MIN_LENGTH:
        return f"Template is too short — at least {_MIN_LENGTH} characters."
    if len(stripped) > _MAX_LENGTH:
        return f"Template is too long — at most {_MAX_LENGTH} characters."
    return None


def _ensure_pg_table() -> None:
    global _pg_ready
    if _pg_ready:
        return
    import psycopg

    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        connection.execute(_PG_TABLE)
    _pg_ready = True


def _read_pg(purpose: str) -> dict[str, Any] | None:
    import psycopg

    _ensure_pg_table()
    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        row = connection.execute(
            "select purpose, name, template, updated_at"
            " from prompt_templates where purpose = %s",
            (purpose,),
        ).fetchone()
    if row is None:
        return None
    return {
        "purpose": row[0],
        "name": row[1],
        "template": row[2],
        "updated_at": row[3].isoformat() if row[3] else None,
    }


def _read_sqlite(purpose: str) -> dict[str, Any] | None:
    with _get_sqlite_connection() as connection:
        connection.execute(_SQLITE_TABLE)
        row = connection.execute(
            "select purpose, name, template, updated_at"
            " from prompt_templates where purpose = ?",
            (purpose,),
        ).fetchone()
    if row is None:
        return None
    return {
        "purpose": row[0],
        "name": row[1],
        "template": row[2],
        "updated_at": row[3],
    }


def get_override(purpose: str) -> dict[str, Any] | None:
    """The active custom template row for a purpose, or None (default)."""
    if not is_valid_purpose(purpose):
        return None
    try:
        if uses_supabase():
            return _read_pg(purpose)
        return _read_sqlite(purpose)
    except Exception:
        logger.warning("Could not read prompt template %r", purpose, exc_info=True)
        return None


def save_override(purpose: str, text: str) -> dict[str, Any]:
    """Upsert the active custom template for one purpose."""
    if not is_valid_purpose(purpose):
        raise ValueError(f"Unknown prompt purpose '{purpose}'.")
    if uses_supabase():
        import psycopg

        _ensure_pg_table()
        with psycopg.connect(settings.database_url, autocommit=True) as connection:
            row = connection.execute(
                """
                insert into prompt_templates (purpose, name, template)
                values (%s, 'custom', %s)
                on conflict (purpose) do update set
                    name = 'custom',
                    template = excluded.template,
                    updated_at = now()
                returning purpose, name, template, updated_at
                """,
                (purpose, text.strip()),
            ).fetchone()
        return {
            "purpose": row[0],
            "name": row[1],
            "template": row[2],
            "updated_at": row[3].isoformat() if row[3] else None,
        }

    with _get_sqlite_connection() as connection:
        connection.execute(_SQLITE_TABLE)
        connection.execute(
            "insert or replace into prompt_templates values (?,?,?,?)",
            (purpose, "custom", text.strip(), None),
        )
    return {"purpose": purpose, "name": "custom", "template": text.strip()}


def delete_override(purpose: str) -> None:
    """Remove a custom template; the purpose falls back to its default."""
    if not is_valid_purpose(purpose):
        return
    try:
        if uses_supabase():
            import psycopg

            _ensure_pg_table()
            with psycopg.connect(settings.database_url, autocommit=True) as connection:
                connection.execute(
                    "delete from prompt_templates where purpose = %s", (purpose,)
                )
            return
        with _get_sqlite_connection() as connection:
            connection.execute(_SQLITE_TABLE)
            connection.execute(
                "delete from prompt_templates where purpose = ?", (purpose,)
            )
    except Exception:
        logger.warning("Could not delete prompt template %r", purpose, exc_info=True)
