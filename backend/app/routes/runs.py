import asyncio

from fastapi import APIRouter

from app.db import runs

router = APIRouter(tags=["diagnosis"])


@router.get("/runs")
async def list_runs() -> dict[str, list[dict]]:
    """Recent diagnosis history (newest first) for the sidebar."""
    return {"runs": runs.list_runs(limit=8)}


@router.get("/runs/analytics")
async def run_analytics() -> dict:
    """Real dashboard aggregates calculated from persisted diagnosis runs."""
    return await asyncio.to_thread(runs.dashboard_stats)
