"""Public profiles: signed-in users mirrored into a visible table.

Covers the contract of app/db/profiles.record_login:

* no-op when no DATABASE_URL is configured;
* one upsert per user per interval (per-process cache);
* a database failure is swallowed — authentication must keep working.
"""

import sys
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.db import profiles


@pytest.fixture(autouse=True)
def _reset_profiles_state():
    profiles._recent_writes.clear()
    profiles._pg_ready = False
    yield
    profiles._recent_writes.clear()
    profiles._pg_ready = False


class _FakeConnection:
    def __init__(self, recorder):
        self._recorder = recorder

    def execute(self, sql, params=None):
        self._recorder.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _install_fake_psycopg(monkeypatch, recorder, fail=False):
    """Swap psycopg for a fake that records executes (or raises)."""
    def connect(url, autocommit=False):
        if fail:
            raise RuntimeError("database unreachable")
        return _FakeConnection(recorder)

    fake = SimpleNamespace(connect=connect)
    monkeypatch.setitem(sys.modules, "psycopg", fake)


def test_record_login_is_a_noop_without_database_url(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")

    def _must_not_connect(*args, **kwargs):
        raise AssertionError("record_login must not touch the DB without DATABASE_URL")

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=_must_not_connect))
    profiles.record_login({"user_id": "user-1", "email": "a@example.com"})
    profiles.record_login(None)


def test_record_login_upserts_once_per_interval(monkeypatch):
    recorder = []
    _install_fake_psycopg(monkeypatch, recorder)
    monkeypatch.setattr(settings, "database_url", "postgres://db")

    user = {
        "user_id": "user-1",
        "email": "a@example.com",
        "app_metadata": {"provider": "google"},
    }
    profiles.record_login(user)
    profiles.record_login(user)  # inside the TTL → cached, no second write

    # First call: create table + one upsert. Second: nothing new.
    assert len(recorder) == 2
    create_sql, _ = recorder[0]
    insert_sql, params = recorder[1]
    assert "create table if not exists profiles" in create_sql
    assert "on conflict (user_id) do update" in insert_sql
    assert params == ("user-1", "a@example.com", "google")

    # A different user writes again (cache is per user_id, table already
    # created in-process so no second CREATE).
    profiles.record_login({"user_id": "user-2", "email": "b@example.com"})
    assert len(recorder) == 3


def test_record_login_defaults_provider_to_email(monkeypatch):
    recorder = []
    _install_fake_psycopg(monkeypatch, recorder)
    monkeypatch.setattr(settings, "database_url", "postgres://db")

    profiles.record_login({"user_id": "user-1", "email": "a@example.com"})
    assert recorder[1][1][2] == "email"


def test_database_failure_never_breaks_login(monkeypatch):
    _install_fake_psycopg(monkeypatch, [], fail=True)
    monkeypatch.setattr(settings, "database_url", "postgres://db")

    # Must not raise — the caller is the auth dependency.
    profiles.record_login({"user_id": "user-1", "email": "a@example.com"})
