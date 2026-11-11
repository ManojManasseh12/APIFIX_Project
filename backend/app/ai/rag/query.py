from urllib.parse import urlparse

from app.core.models import Observation, RequestSpec


def build_query(spec: RequestSpec, obs: Observation) -> str:
    path = urlparse(str(spec.url)).path
    parts = [f"{spec.method} {path}"]
    if obs.status:
        parts.append(f"HTTP {obs.status}")
    if obs.error_type:
        parts.append(obs.error_type.replace("_", " "))
    if obs.redirect_location:
        parts.append("redirect")
    # For a successful response a stale failure-phrased question (for example
    # one carried over from a preset) only drags error docs into retrieval,
    # so it is ignored; success semantics are queried explicitly instead.
    is_success = obs.status is not None and 200 <= obs.status < 300
    if is_success:
        parts.append("successful response 2xx semantics")
    elif spec.question:
        parts.append(spec.question)
    if obs.body_preview:
        parts.append(obs.body_preview[:400])
    return " | ".join(parts)

