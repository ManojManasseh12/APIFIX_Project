from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.errors import PolicyError
from app.core.security import load_allowed_hosts, trust_host

router = APIRouter(tags=["policy"])


class HostRequest(BaseModel):
    host: str


@router.get("/allowed-hosts")
def list_hosts() -> dict[str, list[str]]:
    """Hosts the SSRF guard currently lets the runner call."""
    return {"hosts": sorted(load_allowed_hosts())}


@router.post("/allowed-hosts")
def add_host(payload: HostRequest) -> dict[str, object]:
    """Trust one more host for outbound API tests (no restart needed).

    Single-tenant trust model: every authenticated user shares one
    allowlist file, so any signed-in user can whitelist a host for all
    users. Still SSRF-checked at request time (security.check_url).
    If this becomes multi-tenant, move to admin-only or per-user lists.
    """
    try:
        hosts = trust_host(payload.host)
    except PolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"added": payload.host.strip().lower().rstrip("."), "hosts": sorted(hosts)}
