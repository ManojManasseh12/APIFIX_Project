import time
import uuid

import httpx

from app.core.config import settings
from app.core.errors import DNSError, PolicyError
from app.core.models import Observation, RequestSpec
from app.core.security.redactor import redact_headers, redact_text
from app.core.security.ssrf import check_url

MAX_BODY_BYTES = 256 * 1024


async def execute(spec: RequestSpec) -> Observation:
    request_id = str(uuid.uuid4())
    headers = dict(spec.headers)

    try:
        check_url(str(spec.url), settings.allowed_host_set)
    except PolicyError as exc:
        return Observation(
            request_id=request_id,
            error_type="blocked_by_policy",
            error_message=str(exc),
        )
    except DNSError as exc:
        return Observation(
            request_id=request_id,
            error_type="dns_error",
            error_message=str(exc),
        )

    kwargs: dict = {
        "headers": headers,
        "timeout": httpx.Timeout(spec.timeout_s, connect=5.0),
        "follow_redirects": False,
    }
    if isinstance(spec.body, (dict, list)):
        kwargs["json"] = spec.body
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(spec.body, str):
        kwargs["content"] = spec.body.encode("utf-8")

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.request(spec.method, str(spec.url), **kwargs)
    except httpx.TimeoutException as exc:
        return Observation(
            request_id=request_id,
            error_type="timeout",
            error_message=str(exc),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    except httpx.ConnectError as exc:
        message = str(exc).lower()
        if "certificate" in message or "ssl" in message:
            error_type = "tls_error"
        elif "name or service" in message or "nodename" in message:
            error_type = "dns_error"
        else:
            error_type = "connect_error"
        return Observation(
            request_id=request_id,
            error_type=error_type,
            error_message=str(exc),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    except httpx.HTTPError as exc:
        return Observation(
            request_id=request_id,
            error_type="http_error",
            error_message=str(exc),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    raw = response.content
    truncated = len(raw) > MAX_BODY_BYTES
    preview = raw[:MAX_BODY_BYTES].decode("utf-8", errors="replace")
    return Observation(
        request_id=request_id,
        status=response.status_code,
        headers=redact_headers(dict(response.headers)),
        body_preview=redact_text(preview),
        latency_ms=int((time.perf_counter() - started) * 1000),
        redirect_location=response.headers.get("location"),
        truncated=truncated,
    )

