# Backward-compatibility shim
from app.core.security.ssrf import (  # noqa: F401
    BLOCKED_NETS, LOOPBACK_ALLOW, check_url,
)
