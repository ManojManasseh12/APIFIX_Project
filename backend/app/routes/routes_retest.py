from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.errors import ApprovalRequired
from app.core.models import Observation, Patch, RequestSpec
from app.core.security.approval import require_approval
from app.core.retest import apply_patch, diff
from app.core.tester import execute

router = APIRouter()


class RetestIn(BaseModel):
    spec: RequestSpec
    patch: Patch
    before: Observation | None = None


@router.post("/retest")
async def retest(payload: RetestIn):
    patched = apply_patch(payload.spec, payload.patch)
    try:
        require_approval(patched)
    except ApprovalRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    after = await execute(patched)
    return {
        "patched_request": {
            **patched.model_dump(mode="json", exclude={"headers", "body"}),
            "headers": {
                k: "<REDACTED>" if k.lower() == "authorization" else v
                for k, v in patched.headers.items()
            },
        },
        "observation": after.model_dump(mode="json"),
        "diff": diff(payload.before, after, patched.expected_status),
    }

