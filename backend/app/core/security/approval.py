from app.core.errors import ApprovalRequired
from app.core.models import RequestSpec

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_approval(spec: RequestSpec) -> None:
    if spec.method not in SAFE_METHODS and not spec.approved:
        raise ApprovalRequired(
            f"{spec.method} can change state; set approved=true after user consent"
        )

