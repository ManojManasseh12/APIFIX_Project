from app.core.config import settings
from app.db import runs


def test_dashboard_stats_aggregate_persisted_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "runs_db", tmp_path / "runs.sqlite3")
    runs.initialize()
    runs.save(
        {
            "run_id": "run-one",
            "method": "GET",
            "url": "https://api.example.test/users?active=true",
            "status": 401,
            "provider": "groq:model-a",
        }
    )
    runs.save(
        {
            "run_id": "run-two",
            "method": "GET",
            "url": "https://api.example.test/users",
            "status": 200,
            "provider": "gemini:model-b",
        }
    )

    stats = runs.dashboard_stats()

    assert stats["total_runs"] == 2
    assert stats["failure_count"] == 1
    assert sum(day["count"] for day in stats["daily_runs"]) == 2
    assert {item["status"]: item["count"] for item in stats["status_counts"]} == {
        "2xx": 1,
        "4xx": 1,
    }
    assert {item["provider"]: item["count"] for item in stats["provider_usage"]} == {
        "groq:model-a": 1,
        "gemini:model-b": 1,
    }
    assert stats["endpoints"] == [
        {
            "method": "GET",
            "path": "/users",
            "latest_status": 200,
            "runs": 2,
        }
    ]


def test_list_runs_sqlite_returns_iso_timestamps(monkeypatch, tmp_path):
    """SQLite stores epoch seconds; list_runs must normalise to ISO like the
    Postgres path, or the frontend renders 1970 dates (regression)."""
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "runs_db", tmp_path / "runs.sqlite3")
    runs.initialize()
    runs.save(
        {
            "run_id": "run-one",
            "method": "GET",
            "url": "https://api.example.test/users",
            "status": 200,
        }
    )

    listed = runs.list_runs(limit=5)
    assert len(listed) == 1
    created_at = listed[0]["created_at"]
    # ISO with a "T" separator and a timezone offset, matching ask_chats.
    assert "T" in created_at and ("+" in created_at or "Z" in created_at)
