from copy import deepcopy

from app.core.models import Observation, Patch, RequestSpec


def apply_patch(spec: RequestSpec, patch: Patch) -> RequestSpec:
    """Apply the suggested fix to a copy of the request spec.

    Approval is deliberately NOT granted here: a retest of a
    state-changing method must carry the same explicit consent
    (``spec.approved``) that /api/test required. The frontend only
    reaches /api/retest with a spec the user already consented to run.
    """
    new = deepcopy(spec)
    for key, value in patch.set_headers.items():
        new.headers[key] = value
    for key in patch.remove_headers:
        new.headers.pop(key, None)
    if patch.set_body is not None:
        new.body = patch.set_body
    if patch.set_url:
        new.url = patch.set_url
    return new


def diff(before: Observation | None, after: Observation, expected: int | None) -> dict:
    if before is None:
        return {"verdict": "unclear", "status_after": after.status}
    if expected is not None:
        verdict = "pass" if after.status == expected else "fail"
    elif after.error_type is None and before.status != after.status:
        verdict = "changed"
    else:
        verdict = "unclear"
    return {
        "status_before": before.status,
        "status_after": after.status,
        "error_before": before.error_type,
        "error_after": after.error_type,
        "latency_before_ms": before.latency_ms,
        "latency_after_ms": after.latency_ms,
        "body_changed": before.body_preview != after.body_preview,
        "verdict": verdict,
    }

