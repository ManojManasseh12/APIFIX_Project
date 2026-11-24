from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.connectors import github, sentry
from app.core.auth import get_current_user
from app.core.config import settings
from app.mcp import google

router = APIRouter(tags=["connections"])
# Google redirects the browser here without our bearer token, so the
# callback cannot sit behind the auth dependency. It is protected by the
# signed, short-lived OAuth state instead.
public_router = APIRouter(tags=["connections"])


def _user_id(user: dict | None) -> str:
    """The account that owns a connection (local dev has no auth)."""
    return user["user_id"] if user else "local"


def _safe_origin(request: Request) -> str:
    """Where to send the browser back after consent (allowlisted only)."""
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin:
        referer = urlsplit(request.headers.get("referer") or "")
        if referer.scheme and referer.netloc:
            origin = f"{referer.scheme}://{referer.netloc}"
    if origin in settings.cors_origins:
        return origin
    return settings.cors_origins[0]


def _redirect(origin: str, outcome: str, message: str | None = None):
    params = {"google": outcome}
    if message:
        params["message"] = message[:300]
    return RedirectResponse(f"{origin}/?{urlencode(params)}", status_code=303)


@router.get("/connections")
async def connections(user: dict | None = Depends(get_current_user)) -> dict:
    """Connection status for the Ask sidebar.

    Only honest state is reported: whether credentials are configured,
    whether the provider accepted them, and which account they belong to.
    Tokens are never included.
    """
    return {
        "github": await github.connection_status(),
        "google": await google.status(_user_id(user)),
        "sentry": await sentry.connection_status(),
    }


@router.get("/connections/google/start")
async def google_start(
    request: Request, user: dict | None = Depends(get_current_user)
) -> dict:
    """The Google consent URL for this user (the browser redirects to it)."""
    try:
        url = google.authorization_url(_user_id(user), _safe_origin(request))
    except google.GoogleNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"auth_url": url}


@public_router.get("/connections/google/callback")
async def google_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
):
    """Finish the OAuth flow, store the tokens, and return to the app."""
    try:
        payload = google.verify_state(state)
    except google.GoogleError as exc:
        return _redirect(settings.cors_origins[0], "error", str(exc))

    origin = str(payload.get("o") or "")
    if origin not in settings.cors_origins:
        origin = settings.cors_origins[0]

    if error:
        return _redirect(origin, "error", f"Google returned: {error}")
    if not code:
        return _redirect(origin, "error", "Google did not return an authorization code.")

    try:
        tokens = await google.exchange_code(code)
        await google.save_connection(str(payload.get("u") or ""), tokens)
    except google.GoogleError as exc:
        return _redirect(origin, "error", str(exc))
    except Exception as exc:  # never leak a stack trace into the browser
        return _redirect(origin, "error", f"Unexpected error ({type(exc).__name__}).")

    return _redirect(origin, "connected")


@router.delete("/connections/google")
async def google_disconnect(user: dict | None = Depends(get_current_user)) -> dict:
    """Forget this user's Google grant."""
    try:
        removed = await google.disconnect(_user_id(user))
    except google.GoogleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"disconnected": True, "removed": removed}
