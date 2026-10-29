# Backward-compatibility shim
from app.core.security.redactor import (  # noqa: F401
    SENSITIVE_HEADERS, SECRET_PATTERNS, SENSITIVE_KEYS,
    redact_headers, redact_json, redact_text,
)
