"""Security primitives for outbound requests: SSRF policy, secret redaction,
and the approval gate for state-changing methods."""

import ipaddress
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.core.errors import ApprovalRequired, DNSError, PolicyError
from app.core.models import RequestSpec

# ---------------------------------------------------------------- SSRF policy

BLOCKED_NETS = [
    ipaddress.ip_network(net)
    for net in [
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    ]
]

LOOPBACK_ALLOW = {"localhost:8001", "127.0.0.1:8001"}

# Hosts trusted via the app API (GET/POST /api/allowed-hosts) live in this
# file, merged with the ALLOWED_HOSTS env setting. Editing from the app
# means new target APIs need no backend restart.
ALLOWED_HOSTS_FILE = Path("data/allowed_hosts.txt")

_HOST_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{2,5})?$")


def load_allowed_hosts() -> set[str]:
    """Env allowlist merged with hosts trusted through the app UI."""
    hosts = set(settings.allowed_host_set)
    try:
        for line in ALLOWED_HOSTS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#") and _HOST_PATTERN.match(line):
                hosts.add(line)
    except FileNotFoundError:
        pass
    return hosts


def trust_host(raw_host: str) -> set[str]:
    """Append a host to the app-managed allowlist file and return the set.

    Only hostname[:port] entries are accepted; IP-range and private-address
    protection is still applied at request time, so trusting a host can
    never expose internal destinations.
    """
    host = raw_host.strip().lower().rstrip(".")
    if not _HOST_PATTERN.match(host) or len(host) > 253:
        raise PolicyError(f"invalid hostname: {raw_host!r}")
    try:
        existing = {
            line.strip()
            for line in ALLOWED_HOSTS_FILE.read_text(encoding="utf-8").splitlines()
        }
    except FileNotFoundError:
        existing = set()
    if host not in existing:
        ALLOWED_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ALLOWED_HOSTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{host}\n")
    return load_allowed_hosts()


def _key(url) -> str:
    port = url.port or (443 if url.scheme == "https" else 80)
    return f"{(url.hostname or '').lower()}:{port}"


def _resolve(host: str, port: int):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DNSError(str(exc)) from exc
    return {ipaddress.ip_address(info[4][0]) for info in infos}


def _is_blocked(ip) -> bool:
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast:
        return True
    for net in BLOCKED_NETS:
        if ip.version == net.version and ip in net:
            return True
    return False


def check_url(raw_url: str, allowed: set[str]) -> None:
    url = urlparse(raw_url)
    if url.scheme not in {"http", "https"}:
        raise PolicyError(f"scheme not allowed: {url.scheme}")
    if not url.hostname:
        raise PolicyError("missing hostname")

    key = _key(url)
    host = (url.hostname or "").lower()
    default_port = 443 if url.scheme == "https" else 80
    # Documentation and .env.example list public hosts without a port, while
    # loopback entries keep one. Accept a bare host for the scheme's default
    # port, but not for arbitrary ports.
    bare_default = host in allowed and url.port in (None, default_port)
    if key not in allowed and not bare_default:
        raise PolicyError(f"host not allowlisted: {key}")
    if key in LOOPBACK_ALLOW:
        return

    port = url.port or default_port
    for ip in _resolve(url.hostname, port):
        if _is_blocked(ip):
            raise PolicyError(f"private or reserved address blocked: {ip}")


# ------------------------------------------------------------- secret redaction

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


# ------------------------------------------------------------ approval gate

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_approval(spec: RequestSpec) -> None:
    if spec.method not in SAFE_METHODS and not spec.approved:
        raise ApprovalRequired(
            f"{spec.method} can change state; set approved=true after user consent"
        )
