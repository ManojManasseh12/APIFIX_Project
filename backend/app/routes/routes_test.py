from fastapi import APIRouter, HTTPException

from app.core.errors import ApprovalRequired
from app.core.models import RequestSpec
from app.core.security.approval import require_approval
from app.core.security.redactor import redact_headers
from app.core.tester import execute

router = APIRouter()


@router.post("/test")
async def test_endpoint(spec: RequestSpec):
    try:
        require_approval(spec)
    except ApprovalRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    observation = await execute(spec)
    return {
        "request": {
            **spec.model_dump(mode="json", exclude={"headers", "body"}),
            "headers": redact_headers(spec.headers),
        },
        "observation": observation.model_dump(mode="json"),
    }

