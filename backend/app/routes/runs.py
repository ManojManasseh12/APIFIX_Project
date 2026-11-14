import asyncio

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.db import runs

router = APIRouter(tags=["diagnosis"])


@router.get("/runs")
async def list_runs(user: dict | None = Depends(get_current_user)) -> dict[str, list[dict]]:
    """Recent diagnosis history (newest first) for the sidebar."""
    user_id = user["user_id"] if user else None
    return {"runs": await asyncio.to_thread(runs.list_runs, limit=8, user_id=user_id)}


@router.get("/runs/analytics")
async def run_analytics(user: dict | None = Depends(get_current_user)) -> dict:
    """Real dashboard aggregates calculated from persisted diagnosis runs."""
    user_id = user["user_id"] if user else None
    return await asyncio.to_thread(runs.dashboard_stats, user_id)
