"""Question router: send a question to RAG or to a real tool.

Layered decision, cheapest first:

1. Fast rules answer the obvious cases instantly (no network): clear
   API/documentation questions -> "rag"; unmistakable debug-helper
   requests (JWT decode, epoch, DNS, IP, time) -> their tool.
2. Anything ambiguous goes to the NVIDIA LLM (OpenAI-compatible NIM
   endpoint) with a tiny classification prompt and a short timeout.
   If NVIDIA is unavailable, unreachable, or returns garbage, the
   default is "rag" — the assistant never blocks on the router.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Literal

import httpx

Route = Literal[
    "rag",
    "tool:time",
    "tool:epoch",
    "tool:decode",
    "tool:dns",
    "tool:ip",
]

_TOOL_ROUTES = (
    "tool:time",
    "tool:epoch",
    "tool:decode",
    "tool:dns",
    "tool:ip",
)

_NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_NVIDIA_TIMEOUT = httpx.Timeout(8.0, connect=4.0)

# ── Fast rules ────────────────────────────────────────────────────────────────

_API_MARKERS = re.compile(
    r"\b(api|endpoint|http|https|status code|response|request|header|bearer|"
    r"auth|oauth|token|json|rest|graphql|webhook|pagination|rate limit|"
    r"curl|payload|cors|ssl|latency|docs?|documentation|idempoten|"
    r"put\b|patch\b|delete\b|422|401|403|404|405|429|500|502|503)\b",
    re.IGNORECASE,
)
# Decode requests are checked FIRST: a JWT question contains token-like words
# that would otherwise trip the API markers, and a URL inside the payload
# would trip "http".
_DECODE_MARKERS = re.compile(
    r"\bdecod\w*|\bbase64\b|\burl\s*encoded\b|\burl\s*decoded\b|\bunencode\w*\b|"
    r"\bjwt\b.*\b(?:claims?|header|payload|exp)\b|\beyJ[A-Za-z0-9_-]{6,}",
    re.IGNORECASE,
)
# DNS before API markers: "dns of api.github.com" contains "api".
_DNS_MARKERS = re.compile(
    r"\b(dns|resolve[sd]?\s+(?:to|as)|a\s+record|cname|nameserver|"
    r"dig\s|nslookup)\b|\bdns\s+(?:of|for|lookup)",
    re.IGNORECASE,
)
_IP_PHRASE_MARKERS = re.compile(
    r"\b(?:where\s+is|who\s+owns?|geolocat\w*|locate|hosted\s+at|hosting\s+of)\s+"
    r"(?:\d{1,3}\.){3}\d{1,3}\b",
    re.IGNORECASE,
)
_TIME_MARKERS = re.compile(
    r"\b(?:what(?:'s| is)? the time|what time|time in|current time|"
    r"local time|world clock|timezone of|time zone of)\b",
    re.IGNORECASE,
)
_EPOCH_MARKERS = re.compile(
    r"\b(?:unix\s+timestamp|epoch|posix\s+time|"
    r"timestamp\s+\d{5,}|convert\s+\d{5,})\b|\b\d{10,13}\b",
)
_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")
_DOMAIN = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:com|org|net|io|dev|ai|in|co|gov|edu|app|me|uk|de|fr|jp|us)\b",
    re.IGNORECASE,
)

# ── Argument extraction ──────────────────────────────────────────────────────

_CITY_FILLER = re.compile(
    r"\b(today|tomorrow|tonight|now|right now|currently|current|this week|"
    r"this weekend|please|tell me|give me|show me|the)\b",
    re.IGNORECASE,
)


@dataclass
class RouteDecision:
    route: Route
    via: str  # "rule" or "nvidia"
    args: dict = field(default_factory=dict)


def extract_city(question: str) -> str | None:
    """City name from a place phrase: 'time in Tokyo', 'clock for New York'."""
    match = re.search(
        r"\b(?:in|for|at|of)\s+([A-Za-z][A-Za-z .'-]{1,60})", question, re.IGNORECASE
    )
    if not match:
        return None
    candidate = _CITY_FILLER.sub(" ", match.group(1))
    candidate = re.split(r"\b(?:like|right|today|now)\b", candidate, flags=re.IGNORECASE)[0]
    candidate = candidate.strip(" .,?!")
    # A "city" longer than four words is almost always a parsed sentence.
    if not candidate or len(candidate.split()) > 4:
        return None
    return candidate


def extract_domain(question: str) -> str | None:
    """Domain name in the question, for DNS lookups."""
    match = _DOMAIN.search(question)
    if not match:
        return None
    return match.group(0).lower()


def extract_ip(question: str) -> str | None:
    """IPv4 address in the question, for geolocation."""
    match = _IPV4.search(question)
    return match.group(0) if match else None


def extract_epoch(question: str) -> str | None:
    """The largest 5-13 digit number, for the unix timestamp converter."""
    numbers = re.findall(r"\b(\d{5,13})\b", question)
    if not numbers:
        return None
    return max(numbers, key=len)


def extract_decode_payload(question: str) -> str | None:
    """The token/text to decode: prefer an eyJ… JWT, fall back to a quoted blob."""
    jwt = re.search(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.?[A-Za-z0-9_.-]*", question)
    if jwt:
        return jwt.group(0)
    quoted = re.search(r'''["'`]([^"'`]{12,})["'`]''', question)
    if quoted:
        return quoted.group(1)
    return None


# ── NVIDIA classification (for everything the rules cannot settle) ───────────

_ROUTE_PROMPT = (
    "You are the router of an API debugging assistant. Classify the user question "
    "into exactly one of: 'rag' (anything about APIs, HTTP, status codes, auth, "
    "webhooks, documentation, or the assistant's own knowledge base), "
    "'tool:time' (current time in a city), "
    "'tool:epoch' (converting a unix timestamp to a date), 'tool:decode' "
    "(decoding a JWT/base64/URL-encoded string), 'tool:dns' (DNS records for a "
    "domain), or 'tool:ip' (geolocation of an IP address). Answer with ONLY a "
    'JSON object: {"route": "..."}. No explanation.'
)


async def _nvidia_route(question: str) -> Route | None:
    """Ask NVIDIA for the route; any failure returns None (caller falls back)."""
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    model = os.getenv("NVIDIA_MODEL", "").strip().split(",")[0]
    if not api_key or not model:
        return None
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _ROUTE_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0,
        "max_tokens": 20,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=_NVIDIA_TIMEOUT, trust_env=False) as client:
            response = await client.post(
                _NVIDIA_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if response.status_code != 200:
            return None
        content = response.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        route = json.loads(match.group(0)).get("route")
        if route in ("rag",) + _TOOL_ROUTES:
            return route
        return None
    except Exception:
        return None


def _nvidia_args(route: str, question: str) -> dict:
    """Best-effort arguments for a tool route chosen by NVIDIA."""
    if route == "tool:time":
        return {"city": extract_city(question) or ""}
    if route == "tool:epoch":
        return {"value": extract_epoch(question) or ""}
    if route == "tool:decode":
        return {"payload": extract_decode_payload(question) or ""}
    if route == "tool:dns":
        return {"host": extract_domain(question) or ""}
    if route == "tool:ip":
        return {"ip": extract_ip(question) or ""}
    return {}


async def route_question(question: str) -> RouteDecision:
    """Decide where a question goes. Never raises; never blocks long."""
    # Order matters: specific tool intents first, generic API words last.
    if _DECODE_MARKERS.search(question):
        return RouteDecision(
            route="tool:decode", via="rule", args={"payload": extract_decode_payload(question) or ""}
        )
    # Educational DNS questions with no domain ("what does DNS mean?") must
    # still reach the knowledge base, so only route when a domain is present.
    if _DNS_MARKERS.search(question) and extract_domain(question):
        return RouteDecision(
            route="tool:dns", via="rule", args={"host": extract_domain(question)}
        )
    if _IP_PHRASE_MARKERS.search(question):
        return RouteDecision(
            route="tool:ip", via="rule", args={"ip": extract_ip(question) or ""}
        )
    if _API_MARKERS.search(question):
        return RouteDecision(route="rag", via="rule")

    if _TIME_MARKERS.search(question):
        return RouteDecision(
            route="tool:time", via="rule", args={"city": extract_city(question) or ""}
        )
    if _EPOCH_MARKERS.search(question):
        return RouteDecision(
            route="tool:epoch", via="rule", args={"value": extract_epoch(question) or ""}
        )
    bare_ip = extract_ip(question)
    if bare_ip:
        return RouteDecision(route="tool:ip", via="rule", args={"ip": bare_ip})

    # Ambiguous: let NVIDIA decide, defaulting to the knowledge base.
    routed = await _nvidia_route(question)
    if routed and routed in _TOOL_ROUTES:
        return RouteDecision(route=routed, via="nvidia", args=_nvidia_args(routed, question))
    return RouteDecision(route="rag", via="nvidia" if routed == "rag" else "fallback")
