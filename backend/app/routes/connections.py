from fastapi import APIRouter

from app.connectors import github

router = APIRouter(tags=["connections"])


@router.get("/connections")
async def connections() -> dict:
    """Connection status for the Ask sidebar.

    Only honest state is reported: whether a token is configured, whether
    GitHub accepted it, and which account it belongs to. The token itself
    is never included.
    """
    return {"github": await github.connection_status()}
