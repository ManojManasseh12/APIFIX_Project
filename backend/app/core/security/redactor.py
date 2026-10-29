import re
from typing import Any

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "proxy-authorization",
    "x-auth-token",
}

SECRET_PATTERNS = [
    re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
]

SENSITIVE_KEYS = {
    "token",
    "access_token",
    "api_key",
    "apikey",
    "password",
    "secret",
    "cookie",
}


def redact_text(value: str) -> str:
    if not isinstance(value, str):
        return value
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("<REDACTED>", value)
    return value


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADERS:
            result[key] = "<REDACTED>"
        else:
            result[key] = redact_text(value)
    return result


def redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS:
                out[key] = "<REDACTED>"
            else:
                out[key] = redact_json(item)
        return out
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value

