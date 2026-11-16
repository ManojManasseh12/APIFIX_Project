"""Ask history persistence (ask_turns table, SQLite path)."""

import json

from app.core.config import settings
from app.db import ask_chats
from app.db.ask_chats import _parse_citations


def test_parse_citations_accepts_list_json_string_and_garbage():
    """psycopg parses jsonb into a Python list already; SQLite stores text.
    The Supabase read path must not raise on the already-parsed shape."""
    citation = {"chunk_id": "mdn:422", "source_url": "https://x", "source_title": "MDN"}
    assert _parse_citations([citation]) == [citation]
    assert _parse_citations(json.dumps([citation])) == [citation]
    assert _parse_citations(None) == []
    assert _parse_citations("") == []
    assert _parse_citations("not json") == []


def test_ask_history_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "runs_db", tmp_path / "runs.sqlite3")
    ask_chats.initialize()

    ask_chats.save(
        {
            "turn_id": "turn-one",
            "session_id": "sess-1",
            "question": "what is idempotency?",
            "answer": "Idempotent means safe to repeat [mdn:422].",
            "provider": "groq:model-a",
            "tool": None,
            "tool_label": None,
            "citations": [
                {"chunk_id": "mdn:422", "source_title": "MDN 422", "source_url": "https://developer.mozilla.org/422", "section": None, "page": None}
            ],
        }
    )
    ask_chats.save(
        {
            "turn_id": "turn-two",
            "session_id": "sess-1",
            "question": "what time is it in Tokyo?",
            "answer": "Tokyo — Friday, 2026-11-13 9:14 PM (Asia/Tokyo, UTC+09:00)",
            "provider": None,
            "tool": "time",
            "tool_label": "9:14 PM · Tokyo",
            "citations": [],
        }
    )

    turns = ask_chats.list_turns(limit=10)
    assert [turn["turn_id"] for turn in turns] == ["turn-two", "turn-one"]  # newest first
    assert turns[1]["citations"][0]["chunk_id"] == "mdn:422"
    assert turns[0]["tool"] == "time"
    # ISO timestamps so the frontend can render relative time on both backends.
    assert "T" in turns[0]["created_at"]

    # Saving the same turn_id twice must not duplicate (upsert).
    ask_chats.save(
        {
            "turn_id": "turn-one",
            "session_id": "sess-1",
            "question": "what is idempotency?",
            "answer": "updated answer",
            "provider": None,
            "tool": None,
            "tool_label": None,
            "citations": [],
        }
    )
    turns = ask_chats.list_turns(limit=10)
    assert len([turn for turn in turns if turn["turn_id"] == "turn-one"]) == 1
    saved_one = next(turn for turn in turns if turn["turn_id"] == "turn-one")
    assert saved_one["answer"] == "updated answer"


def test_ask_history_never_raises_on_backend_failure(monkeypatch):
    def broken():
        raise RuntimeError("backend down")

    monkeypatch.setattr(ask_chats, "uses_supabase", broken)
    ask_chats.save({"turn_id": "x", "question": "q", "answer": "a"})
    # returning is the whole point — no exception escaped
