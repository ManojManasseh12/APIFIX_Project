"""Question router: send a question to RAG or to a real tool.

Layered decision, cheapest first:

1. Fast rules answer the obvious cases instantly (no network): clear
   API/documentation questions -> "rag"; unmistakable weather or
   currency requests -> their tool.
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

Route = Literal["rag", "tool:weather", "tool:currency"]

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
_WEATHER_MARKERS = re.compile(
    r"\b(weather|temperature|forecast|raining|rain|snow|humidity|wind|"
    r"how (?:hot|cold|warm)|weather report)\b",
    re.IGNORECASE,
)
_CURRENCY_PATTERNS = [
    re.compile(
        r"\b(?:convert|exchange|how much is)\b.*\b(?:usd|inr|eur|gbp|jpy|aud|cad|"
        r"aed|sgd|chf|cny|sek|nzd|zar|rupees?|dollars?|euros?|pounds?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:usd|inr|eur|gbp|jpy|aud|cad|aed|sgd|chf|cny|sek|nzd|zar|"
        r"rupees?|dollars?|euros?|pounds?)\s*(?:to|in|into|vs|versus)\s*"
        r"(?:usd|inr|eur|gbp|jpy|aud|cad|aed|sgd|chf|cny|sek|nzd|zar|"
        r"rupees?|dollars?|euros?|pounds?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:inr|usd)\s*(?:to|in|into)\s*[a-z]{3}\b", re.IGNORECASE),
]

# ── Argument extraction ──────────────────────────────────────────────────────

_CITY_FILLER = re.compile(
    r"\b(today|tomorrow|tonight|now|right now|currently|current|this week|"
    r"this weekend|please|tell me|give me|show me|the)\b",
    re.IGNORECASE,
)
_CURRENCY_WORDS = {
    "rupee": "INR",
    "rupees": "INR",
    "rs": "INR",
    "dollar": "USD",
    "dollars": "USD",
    "usd": "USD",
    "euro": "EUR",
    "euros": "EUR",
    "pound": "GBP",
    "pounds": "GBP",
    "yen": "JPY",
    "dirham": "AED",
    "dirhams": "AED",
}
_FX_CODE = r"(?:rupees?|dollars?|euros?|pounds?|yen|dirhams?|Rs\.?|[A-Za-z]{3})"
_FX_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(" + _FX_CODE + r")", re.IGNORECASE)
_FX_PAIR = re.compile(
    r"(" + _FX_CODE + r")\s*(?:to|in|into|vs|versus)\s*(" + _FX_CODE + r")",
    re.IGNORECASE,
)


def _code(word: str) -> str | None:
    """'Rupees'/'dollars'/'inr'/'Rs.' -> 3-letter currency code."""
    lowered = word.lower().rstrip(".")
    if lowered in _CURRENCY_WORDS:
        return _CURRENCY_WORDS[lowered]
    if re.fullmatch(r"[a-z]{3}", lowered):
        return lowered.upper()
    return None


@dataclass
class RouteDecision:
    route: Route
    via: str  # "rule" or "nvidia"
    args: dict = field(default_factory=dict)


def extract_city(question: str) -> str | None:
    """City name after a weather phrase: 'weather in Chennai', 'is it raining in Oslo?'."""
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


def extract_fx(question: str) -> dict | None:
    """Amount + currency pair from 'convert 5000 inr to usd', 'inr to usd', '100 usd in eur'."""
    pair = _FX_PAIR.search(question)
    if not pair:
        return None
    base = _code(pair.group(1))
    target = _code(pair.group(2))
    if not base or not target:
        return None

    amount_match = _FX_AMOUNT.search(question)
    amount = 1.0
    if amount_match:
        amount_code = _code(amount_match.group(2))
        if amount_code in (base, target):
            amount = float(amount_match.group(1).replace(",", ""))
    return {"amount": amount, "base": base, "target": target}


# ── NVIDIA classification (for everything the rules cannot settle) ───────────

_ROUTE_PROMPT = (
    "You are the router of an API debugging assistant. Classify the user question "
    "into exactly one of: 'rag' (anything about APIs, HTTP, status codes, auth, "
    "webhooks, documentation, or the assistant's own knowledge base), "
    "'tool:weather' (weather conditions for a place), or 'tool:currency' "
    "(converting money between currencies). Answer with ONLY a JSON object: "
    '{"route": "..."}. No explanation.'
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
        if route in ("rag", "tool:weather", "tool:currency"):
            return route
        return None
    except Exception:
        return None


async def route_question(question: str) -> RouteDecision:
    """Decide where a question goes. Never raises; never blocks long."""
    if _API_MARKERS.search(question):
        return RouteDecision(route="rag", via="rule")

    weather_hit = bool(_WEATHER_MARKERS.search(question))
    currency_hit = any(p.search(question) for p in _CURRENCY_PATTERNS)

    if weather_hit and not currency_hit:
        return RouteDecision(
            route="tool:weather", via="rule", args={"city": extract_city(question) or ""}
        )
    if currency_hit and not weather_hit:
        fx = extract_fx(question)
        if fx:
            return RouteDecision(route="tool:currency", via="rule", args=fx)

    # Ambiguous: let NVIDIA decide, defaulting to the knowledge base.
    routed = await _nvidia_route(question)
    if routed == "tool:weather":
        return RouteDecision(route=routed, via="nvidia", args={"city": extract_city(question) or ""})
    if routed == "tool:currency":
        return RouteDecision(
            route=routed, via="nvidia", args=extract_fx(question) or {"amount": 1.0, "base": "INR", "target": "USD"}
        )
    return RouteDecision(route="rag", via="nvidia" if routed == "rag" else "fallback")
