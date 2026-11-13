"""Router classification, argument extraction, and the seven router tools."""

import base64
import json

import httpx
import pytest

# Captured before any monkeypatching; the MockTransport factory must build REAL clients.
_RealAsyncClient = httpx.AsyncClient

from app.ai.router import (
    extract_city,
    extract_decode_payload,
    extract_domain,
    extract_epoch,
    extract_fx,
    extract_ip,
    route_question,
)
from app.ai.tools import (
    ToolError,
    convert_currency,
    convert_epoch,
    decode_payload,
    dns_lookup,
    get_time,
    get_weather,
    ip_lookup,
)


# ── Router rules ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_api_question_routes_to_rag_without_llm():
    decision = await route_question("Why does this POST return HTTP 422?")
    assert decision.route == "rag"
    assert decision.via == "rule"


@pytest.mark.asyncio
async def test_decode_question_wins_over_api_markers():
    token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    )
    decision = await route_question(f"decode this JWT {token}")
    assert decision.route == "tool:decode"
    assert decision.via == "rule"
    assert decision.args["payload"] == token


@pytest.mark.asyncio
async def test_dns_question_wins_over_api_markers():
    decision = await route_question("check dns of api.github.com")
    assert decision.route == "tool:dns"
    assert decision.args["host"] == "api.github.com"


@pytest.mark.asyncio
async def test_educational_dns_question_without_domain_goes_to_rag():
    decision = await route_question("what does DNS mean in API design?")
    assert decision.route == "rag"


@pytest.mark.asyncio
async def test_ip_phrase_and_bare_ip_route_to_ip_tool():
    decision = await route_question("where is 8.8.8.8 hosted?")
    assert decision.route == "tool:ip"
    assert decision.args["ip"] == "8.8.8.8"

    bare = await route_question("8.8.8.8")
    assert bare.route == "tool:ip"
    assert bare.args["ip"] == "8.8.8.8"


@pytest.mark.asyncio
async def test_time_question_routes_to_clock_tool():
    decision = await route_question("what time is it in Tokyo?")
    assert decision.route == "tool:time"
    assert decision.args["city"].lower() == "tokyo"


@pytest.mark.asyncio
async def test_epoch_question_routes_to_timestamp_tool():
    decision = await route_question("what is 1730000000 in date?")
    assert decision.route == "tool:epoch"
    assert decision.args["value"] == "1730000000"


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


def test_extract_domain_and_ip():
    assert extract_domain("check dns of api.github.com") == "api.github.com"
    assert extract_domain("is example.org up?") == "example.org"
    assert extract_domain("no host here") is None
    assert extract_ip("where is 192.168.1.1 hosted?") == "192.168.1.1"
    assert extract_ip("999.999.999.999 is not an ip") is None
    assert extract_ip("no address") is None


def test_extract_epoch_picks_the_longest_number():
    assert extract_epoch("what is 1730000000 in date?") == "1730000000"
    assert extract_epoch("convert 1730000000000") == "1730000000000"
    assert extract_epoch("no numbers") is None


def test_extract_decode_payload_prefers_jwt_then_quotes():
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig"
    assert extract_decode_payload(f"decode this JWT {token}") == token
    assert extract_decode_payload('decode "SGVsbG8gd29ybGQ=" in base64') == "SGVsbG8gd29ybGQ="
    assert extract_decode_payload("plain question") is None


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
        if host == "dns.google":
            return httpx.Response(
                200,
                json={"Status": 0, "Answer": [
                    {"name": "api.github.com.", "type": 1, "TTL": 60, "data": "140.82.121.4"},
                ]},
            )
        if host == "ipwho.is":
            return httpx.Response(
                200,
                json={
                    "ip": "8.8.8.8",
                    "success": True,
                    "city": "Mountain View",
                    "region": "California",
                    "country": "United States",
                    "latitude": 37.386,
                    "longitude": -122.0838,
                    "timezone": {"id": "America/Los_Angeles"},
                    "connection": {"isp": "Google LLC", "org": "Google LLC"},
                },
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


# ── Local tools (no network at all) ──────────────────────────────────────────

def test_get_time_returns_local_clock_for_known_city():
    result = get_time("Tokyo")
    assert result["city"] == "Tokyo"
    assert result["timezone"] == "Asia/Tokyo"
    assert result["utc_offset"] == "+09:00"
    assert result["date"] and result["time_12h"] and result["weekday"]

    with pytest.raises(ToolError):
        get_time("Atlantis")


def test_convert_epoch_handles_seconds_and_milliseconds():
    from datetime import datetime, timezone as dt_timezone

    expected = datetime.fromtimestamp(1730000000, tz=dt_timezone.utc)
    result = convert_epoch(1730000000)
    assert result["precision"] == "seconds"
    assert result["iso_utc"] == expected.strftime("%Y-%m-%d %H:%M:%S UTC")
    assert result["weekday"] == expected.strftime("%A")

    ms = convert_epoch(1730000000000)
    assert ms["precision"] == "milliseconds"
    assert ms["date_utc"] == result["date_utc"]

    with pytest.raises(ToolError):
        convert_epoch("not a number")


_SAMPLE_JWT = (
    base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    + "."
    + base64.urlsafe_b64encode(b'{"sub":"1234567890","exp":1730000000}').rstrip(b"=").decode()
    + ".sig"
)


def test_decode_jwt_peeks_at_claims():
    result = decode_payload(_SAMPLE_JWT)
    assert result["kind"] == "jwt"
    assert result["header"]["alg"] == "HS256"
    assert result["payload"]["sub"] == "1234567890"
    assert result["payload"]["exp"] == 1730000000
    assert "NOT verified" in result["note"]


def test_decode_base64_and_url():
    base64_result = decode_payload(base64.b64encode(b"hello world").decode())
    assert base64_result == {"kind": "base64", "decoded": "hello world"}

    url_result = decode_payload("q=api%20debugging%26tools")
    assert url_result == {"kind": "url", "decoded": "q=api debugging&tools"}

    with pytest.raises(ToolError):
        # Non-UTF-8 base64: valid b64 bytes that are not text, so both
        # decoders fail and the tool must refuse cleanly.
        decode_payload("//////8=")

    with pytest.raises(ToolError):
        decode_payload("")


# ── Network tools: dns + ip over the mock transport ──────────────────────────

@pytest.mark.asyncio
async def test_dns_lookup_returns_a_records(patched_http):
    result = await dns_lookup("api.github.com")
    assert result["host"] == "api.github.com"
    assert result["addresses"] == ["140.82.121.4"]
    assert result["ttl"] == 60
    assert patched_http, "dns.google was called"


@pytest.mark.asyncio
async def test_dns_lookup_nxdomain_raises(patched_http, monkeypatch):
    async def nxdomain(url, params):
        return {"Status": 3}

    monkeypatch.setattr("app.ai.tools._get_json", nxdomain)
    with pytest.raises(ToolError, match="NXDOMAIN"):
        await dns_lookup("this-does-not-exist.example")

    with pytest.raises(ToolError):
        await dns_lookup("not a domain")


@pytest.mark.asyncio
async def test_ip_lookup_geolocates(patched_http):
    result = await ip_lookup("8.8.8.8")
    assert result["ip"] == "8.8.8.8"
    assert result["city"] == "Mountain View"
    assert result["country"] == "United States"
    assert result["timezone"] == "America/Los_Angeles"
    assert result["isp"] == "Google LLC"

    with pytest.raises(ToolError):
        await ip_lookup("999.1.1.1")


# ── /api/ask dispatch (regression: executors must get args, not the dict) ────

@pytest.mark.asyncio
async def test_execute_tool_passes_individual_args(monkeypatch):
    import app.routes.ask as ask_module

    captured = {}

    def fake(name):
        async def run(*values):
            captured[name] = values[0] if len(values) == 1 else values
            return {"ok": True}
        return run

    for name in ("get_weather", "convert_currency", "get_time", "convert_epoch",
                 "decode_payload", "dns_lookup", "ip_lookup"):
        monkeypatch.setattr(ask_module, name, fake(name))

    cases = {
        "weather": ({"city": "Chennai"}, "Chennai"),
        "currency": ({"amount": 5, "base": "INR", "target": "USD"}, (5.0, "INR", "USD")),
        "time": ({"city": "Tokyo"}, "Tokyo"),
        "epoch": ({"value": "1730000000"}, "1730000000"),
        "decode": ({"payload": "abc"}, "abc"),
        "dns": ({"host": "api.github.com"}, "api.github.com"),
        "ip": ({"ip": "8.8.8.8"}, "8.8.8.8"),
    }
    tool_to_fn = {
        "weather": "get_weather",
        "currency": "convert_currency",
        "time": "get_time",
        "epoch": "convert_epoch",
        "decode": "decode_payload",
        "dns": "dns_lookup",
        "ip": "ip_lookup",
    }
    for tool, (args, expected_arg) in cases.items():
        result = await ask_module._execute_tool(tool, args)
        assert result == {"ok": True}
        assert captured[tool_to_fn[tool]] == expected_arg

    with pytest.raises(ToolError):
        await ask_module._execute_tool("nonexistent", {})


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

    assert format_tool_result(
        "time",
        {"city": "Tokyo", "timezone": "Asia/Tokyo", "date": "2026-11-13", "time_12h": "9:14 PM",
         "weekday": "Friday", "utc_offset": "+09:00"},
    ) == "Tokyo — Friday, 2026-11-13 9:14 PM (Asia/Tokyo, UTC+09:00)"

    assert format_tool_result(
        "epoch",
        {"epoch": 1730000000, "precision": "seconds", "iso_utc": "2024-10-27 03:33:20 UTC",
         "date_utc": "2024-10-27", "time_utc": "03:33:20", "weekday": "Sunday"},
    ) == "1730000000 = 2024-10-27 03:33:20 UTC, a Sunday"

    decoded = format_tool_result(
        "decode",
        {"kind": "jwt", "header": {"alg": "HS256"}, "payload": {"sub": "1234567890", "exp": 1730000000},
         "note": "Claims peek only — the signature was NOT verified."},
    )
    assert "sub: " in decoded and "HS256" in decoded and "NOT verified" in decoded
    # exp claim gets a human date
    assert "2024-10-27" in decoded

    assert format_tool_result(
        "dns", {"host": "api.github.com", "addresses": ["140.82.121.4"], "cnames": [], "ttl": 60}
    ) == "api.github.com resolves to · A records: 140.82.121.4 · TTL 60s"

    assert format_tool_result(
        "ip", {"ip": "8.8.8.8", "city": "Mountain View", "region": "California",
               "country": "United States", "isp": "Google LLC", "org": None}
    ) == "8.8.8.8 — Mountain View, California, United States (Google LLC)"
