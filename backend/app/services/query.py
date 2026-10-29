from urllib.parse import urlparse

from app.models import Observation, RequestSpec


def build_query(spec: RequestSpec, obs: Observation) -> str:
    path = urlparse(str(spec.url)).path
    parts = [f"{spec.method} {path}"]
    if obs.status:
        parts.append(f"HTTP {obs.status}")
    if obs.error_type:
        parts.append(obs.error_type.replace("_", " "))
    if obs.redirect_location:
        parts.append("redirect")
    if spec.question:
        parts.append(spec.question)
    if obs.body_preview:
        parts.append(obs.body_preview[:400])
    return " | ".join(parts)
