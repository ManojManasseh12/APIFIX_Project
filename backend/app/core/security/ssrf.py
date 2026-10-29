import ipaddress
import socket
from urllib.parse import urlparse

from app.core.errors import DNSError, PolicyError

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
    if key not in allowed:
        raise PolicyError(f"host not allowlisted: {key}")
    if key in LOOPBACK_ALLOW:
        return

    port = url.port or (443 if url.scheme == "https" else 80)
    for ip in _resolve(url.hostname, port):
        if _is_blocked(ip):
            raise PolicyError(f"private or reserved address blocked: {ip}")

