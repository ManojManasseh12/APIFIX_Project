"""Public user profiles for everyone who signs in.

Supabase Auth keeps account data in its hidden ``auth.users`` schema
(only visible under Authentication → Users in the dashboard). This
module mirrors the essentials into a normal ``public.profiles`` table
so signed-up users appear in the Table Editor next to ``runs`` and
``ask_turns``:

    user_id, email, provider, first_seen_at, last_seen_at

``record_login`` is called from the auth dependency after a token is
verified. A short per-process cache keeps one logged-in user from
hitting the database on every request. Every failure is swallowed (and
logged): a profile-write problem must never block an authenticated
request.
"""

import logging
import threading
import time
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_PG_TABLE = """
create table if not exists profiles (
    user_id text primary key,
    email text,
    provider text,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now()
)
"""

# Re-write a user's row at most once per this interval, per process.
_WRITE_EVERY_S = 300.0
_CACHE_MAX_ENTRIES = 10_000

_pg_ready = False
_recent_writes: dict[str, float] = {}
_cache_lock = threading.Lock()


def _provider_of(user: dict[str, Any]) -> str:
    """Sign-in method from the token's app_metadata (google/email/...)."""
    metadata = user.get("app_metadata")
    if isinstance(metadata, dict):
        provider = metadata.get("provider")
        if provider:
            return str(provider)
    return "email"


def record_login(user: dict[str, Any] | None) -> None:
    """Upsert the verified user into ``profiles`` (best effort)."""
    if not user or not settings.database_url_configured:
        return

    user_id = str(user.get("user_id") or "")
    if not user_id:
        return

    with _cache_lock:
        if time.monotonic() - _recent_writes.get(user_id, -_WRITE_EVERY_S) < _WRITE_EVERY_S:
            return

    try:
        import psycopg

        global _pg_ready
        with psycopg.connect(settings.database_url, autocommit=True) as connection:
            if not _pg_ready:
                connection.execute(_PG_TABLE)
                _pg_ready = True
            connection.execute(
                """
                insert into profiles (user_id, email, provider)
                values (%s, %s, %s)
                on conflict (user_id) do update set
                    email = excluded.email,
                    provider = excluded.provider,
                    last_seen_at = now()
                """,
                (user_id, user.get("email"), _provider_of(user)),
            )
    except Exception:
        # Profile bookkeeping must never break authentication. The throttle
        # was NOT set before the write, so a transient DB failure is retried
        # on the next request instead of being deferred by the TTL.
        logger.warning("Could not record profile for %s", user_id, exc_info=True)
        return

    with _cache_lock:
        # Bound the cache: one entry per user is fine at demo scale, but a
        # cap keeps a long-lived process from growing it without limit.
        if len(_recent_writes) >= _CACHE_MAX_ENTRIES:
            oldest = min(_recent_writes, key=_recent_writes.get)
            _recent_writes.pop(oldest, None)
        _recent_writes[user_id] = time.monotonic()