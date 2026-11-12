"""Router classification, argument extraction, and the weather/currency tools."""

import json

import httpx
import pytest

# Captured before any monkeypatching; the MockTransport factory must build REAL clients.
_RealAsyncClient = httpx.AsyncClient

from app.ai.router import (
    extract_city,
    extract_fx,
    route_question,
)
from app.ai.tools import ToolError, convert_currency, get_weather


# ── Router rules ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_api_question_routes_to_rag_without_llm():
    decision = await route_question("Why does this POST return HTTP 422?")
    assert decision.route == "rag"
    assert decision.via == "rule"


@pytest.mark.asyncio
async def test_weather_question_routes_to_weather_tool():
    decision = await route_question("What's the weather in Chennai today?")
    assert decision.route == "tool:weather"
    assert decision.via == "rule"
    assert decision.args["city"].lower() == "chennai"


@pytest.mark.asyncio
async def test_currency_question_routes_to_currency_tool():
    decision = await route_question("convert 5000 INR to USD")
    assert decision.route == "tool:currency"
    assert decision.via == "rule"
    assert decision.args == {"amount": 5000.0, "base": "INR", "target": "USD"}


@pytest.mark.asyncio
async def test_ambiguous_question_falls_back_to_rag_when_nvidia_unavailable(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    decision = await route_question("Tell me something interesting about servers.")
    assert decision.route == "rag"
    assert decision.via == "fallback"


# ── Argument extraction ──────────────────────────────────────────────────────

def test_extract_city_strips_filler_words():
    assert extract_city("what's the weather in Chennai today?") == "Chennai"
    assert extract_city("weather report for New York right now") == "New York"
    assert extract_city("is it raining in Oslo?") == "Oslo"


def test_extract_fx_handles_words_and_defaults():
    assert extract_fx("convert 5000 inr to usd") == {
        "amount": 5000.0,
        "base": "INR",
        "target": "USD",
    }
    assert extract_fx("100 dollars to rupees")["target"] == "INR"
    assert extract_fx("inr to usd")["amount"] == 1.0
    assert extract_fx("what is idempotency") is None


# ── Tools (mocked HTTP, no network) ──────────────────────────────────────────

@pytest.fixture
def patched_http(monkeypatch):
    """Route tool HTTP calls through a MockTransport with canned responses."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), dict(request.url.params)))
        host = request.url.host
        if host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200,
                json={"results": [{"name": "Chennai", "country": "India", "latitude": 13.08, "longitude": 80.27}]},
            )
        if host == "api.open-meteo.com":
            return httpx.Response(
                200,
                json={"current": {
                    "temperature_2m": 31.4,
                    "apparent_temperature": 36.0,
                    "relative_humidity_2m": 68,
                    "weather_code": 2,
                    "wind_speed_10m": 12.1,
                }},
            )
        if host == "api.frankfurter.dev":
            return httpx.Response(
                200,
                json={"base": "INR", "rates": {"USD": 0.0113}, "date": "2026-11-12"},
            )
        return httpx.Response(404)

    def factory(**kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.ai.tools.httpx.AsyncClient", lambda **kw: factory(**kw))
    return calls


@pytest.mark.asyncio
async def test_get_weather_returns_current_conditions(patched_http):
    result = await get_weather("Chennai")
    assert result["city"] == "Chennai"
    assert result["country"] == "India"
    assert result["temperature_c"] == 31.4
    assert result["conditions"] == "Partly cloudy"  # WMO code 2
    assert patched_http, "geocoding + forecast calls were made"


@pytest.mark.asyncio
async def test_convert_currency_computes_amount(patched_http):
    result = await convert_currency(5000, "INR", "USD")
    assert result == {
        "base": "INR",
        "target": "USD",
        "amount": 5000,
        "rate": 0.0113,
        "converted": 56.5,
        "date": "2026-11-12",
    }


@pytest.mark.asyncio
async def test_unknown_city_raises_tool_error(patched_http, monkeypatch):
    async def empty_geo(url, params):
        return {"results": []}

    monkeypatch.setattr("app.ai.tools._get_json", empty_geo)
    with pytest.raises(ToolError):
        await get_weather("Atlantis")


@pytest.mark.asyncio
async def test_unsupported_currency_raises_tool_error(patched_http, monkeypatch):
    async def bad_rates(url, params):
        return {"base": "XXX", "rates": {}, "date": "2026-11-12"}

    monkeypatch.setattr("app.ai.tools._get_json", bad_rates)
    with pytest.raises(ToolError):
        await convert_currency(1, "XXX", "USD")


def test_tool_result_is_json_serializable():
    from app.ai.prompts import build_tool_context

    context = build_tool_context("currency", {"amount": 5}, {"converted": 0.06})
    json.loads(context.split("TOOL_RESULT: ", 1)[1].splitlines()[0])


def test_format_tool_result_reads_like_a_sentence():
    from app.ai.tools import format_tool_result

    currency = format_tool_result(
        "currency",
        {"base": "INR", "target": "USD", "amount": 5000.0, "rate": 0.010673, "converted": 53.37, "date": "2026-11-11"},
    )
    assert currency == "5000.0 INR = 53.37 USD (ECB reference rate 1 INR = 0.010673, 2026-11-11)"

    weather = format_tool_result(
        "weather",
        {"city": "Chennai", "country": "India", "temperature_c": 30.9, "feels_like_c": 33.2,
         "humidity_percent": 45, "wind_kmh": 14.7, "conditions": "Overcast"},
    )
    assert weather == "Chennai, India: Overcast, 30.9°C (feels like 33.2°C), humidity 45%, wind 14.7 km/h"
