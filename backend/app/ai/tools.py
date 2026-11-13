"""Real tools the router can invoke instead of the knowledge base.

Seven tools. Network ones are keyless public APIs with FIXED hosts:

- Weather: Open-Meteo geocoding + forecast (geocoding-api.open-meteo.com,
  api.open-meteo.com)
- Currency conversion: Frankfurter ECB reference rates (api.frankfurter.dev)
- DNS lookup: DNS-over-HTTPS at dns.google
- IP geolocation: ipwho.is
- World clock, unix timestamp converter, and JWT/base64/URL decoder are
  pure local computation (zoneinfo / stdlib) — no network at all.

SSRF note: unlike the request tester, these calls never take a
user-supplied URL — the host is a hardcoded constant and only the
query parameters vary. The SSRF guard in core/security.py exists for
user-controlled URLs; tool endpoints are safe by construction.
"""

from __future__ import annotations

import json

import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FX_URL = "https://api.frankfurter.dev/v1/latest"
DNS_URL = "https://dns.google/resolve"
IP_URL = "https://ipwho.is"

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Popular cities -> IANA zones (local computation; no network needed).
CITY_TZ = {
    "chennai": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata",
    "new delhi": "Asia/Kolkata", "bangalore": "Asia/Kolkata", "bengaluru": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata", "kolkata": "Asia/Kolkata", "india": "Asia/Kolkata",
    "london": "Europe/London", "paris": "Europe/Paris", "berlin": "Europe/Berlin",
    "madrid": "Europe/Madrid", "rome": "Europe/Rome", "amsterdam": "Europe/Amsterdam",
    "zurich": "Europe/Zurich", "moscow": "Europe/Moscow", "istanbul": "Europe/Istanbul",
    "dubai": "Asia/Dubai", "riyadh": "Asia/Riyadh", "karachi": "Asia/Karachi",
    "dhaka": "Asia/Dhaka", "singapore": "Asia/Singapore", "hong kong": "Asia/Hong_Kong",
    "shanghai": "Asia/Shanghai", "beijing": "Asia/Shanghai", "seoul": "Asia/Seoul",
    "tokyo": "Asia/Tokyo", "bangkok": "Asia/Bangkok", "jakarta": "Asia/Jakarta",
    "kuala lumpur": "Asia/Kuala_Lumpur", "manila": "Asia/Manila",
    "sydney": "Australia/Sydney", "auckland": "Pacific/Auckland",
    "new york": "America/New_York", "boston": "America/New_York",
    "washington": "America/New_York", "toronto": "America/Toronto",
    "chicago": "America/Chicago", "dallas": "America/Chicago",
    "denver": "America/Denver", "phoenix": "America/Phoenix",
    "los angeles": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "mexico city": "America/Mexico_City",
    "sao paulo": "America/Sao_Paulo", "johannesburg": "Africa/Johannesburg",
    "nairobi": "Africa/Nairobi", "lagos": "Africa/Lagos", "cairo": "Africa/Cairo",
    "utc": "UTC", "gmt": "UTC",
}

# WMO weather interpretation codes (467-1), trimmed to the common ones.
_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


class ToolError(Exception):
    """A tool could not produce a result (city unknown, bad currency, API down)."""


async def _get_json(url: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
        response = await client.get(url, params=params)
    if response.status_code != 200:
        raise ToolError(f"{url.split('/')[2]} returned HTTP {response.status_code}")
    return response.json()


async def get_weather(city: str) -> dict:
    """Resolve a city name and return its current conditions."""
    city = city.strip().strip("?").strip()
    if not city:
        raise ToolError("No city given for the weather lookup.")

    geo = await _get_json(
        GEOCODING_URL,
        {"name": city, "count": 1, "language": "en", "format": "json"},
    )
    results = geo.get("results") or []
    if not results:
        raise ToolError(f"Could not find a city named '{city}'.")
    place = results[0]

    current = (
        await _get_json(
            FORECAST_URL,
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "wind_speed_unit": "kmh",
            },
        )
    ).get("current") or {}

    code = current.get("weather_code")
    return {
        "city": place["name"],
        "country": place.get("country", ""),
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_kmh": current.get("wind_speed_10m"),
        "conditions": _WEATHER_CODES.get(code, f"weather code {code}"),
    }


async def convert_currency(amount: float, base: str, target: str) -> dict:
    """Convert an amount between currencies using ECB reference rates."""
    base = base.strip().upper()
    target = target.strip().upper()
    if len(base) != 3 or len(target) != 3 or not base.isalpha() or not target.isalpha():
        raise ToolError("Currencies must be 3-letter codes such as INR or USD.")

    data = await _get_json(FX_URL, {"base": base, "symbols": target})
    rates = data.get("rates") or {}
    if target not in rates:
        raise ToolError(f"'{target}' is not a supported currency code.")
    rate = rates[target]
    return {
        "base": base,
        "target": target,
        "amount": amount,
        "rate": rate,
        "converted": round(amount * rate, 2),
        "date": data.get("date"),
    }


def get_time(city: str) -> dict:
    """World clock — pure local computation, no network."""
    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    city = (city or "").strip().strip("?").strip()
    if not city:
        raise ToolError("Name a city for the world clock, e.g. 'time in Tokyo'.")
    lowered = city.lower()
    zone_name = CITY_TZ.get(lowered)
    if zone_name is None and ("/" in city or city.upper() in ("UTC", "GMT")):
        zone_name = city  # direct IANA input such as 'Asia/Kolkata'
    if zone_name is None:
        raise ToolError(f"I don't know the timezone for '{city}'. Try a major city or an IANA zone like Asia/Kolkata.")
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        raise ToolError(f"Unknown timezone '{zone_name}'.")
    now = datetime.now(zone)
    return {
        "city": city.title() if city.lower() not in ("utc", "gmt") else city.upper(),
        "timezone": zone_name,
        "date": now.strftime("%Y-%m-%d"),
        "time_12h": now.strftime("%I:%M %p").lstrip("0"),
        "weekday": now.strftime("%A"),
        "utc_offset": now.strftime("%z")[:3] + ":" + now.strftime("%z")[3:5] if now.strftime("%z") else "+00:00",
    }


def convert_epoch(value) -> dict:
    """Unix timestamp → human dates. Accepts seconds (10 digits) or milliseconds (13)."""
    from datetime import datetime, timezone as dt_timezone

    try:
        epoch = int(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        raise ToolError("Give a numeric unix timestamp, e.g. 'what is 1730000000 in date?'.")
    if len(str(epoch)) >= 13:
        epoch_seconds, precision = epoch / 1000, "milliseconds"
    else:
        epoch_seconds, precision = epoch, "seconds"
    utc = datetime.fromtimestamp(epoch_seconds, tz=dt_timezone.utc)
    return {
        "epoch": epoch,
        "precision": precision,
        "iso_utc": utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "date_utc": utc.strftime("%Y-%m-%d"),
        "time_utc": utc.strftime("%H:%M:%S"),
        "weekday": utc.strftime("%A"),
    }


def decode_payload(text: str) -> dict:
    """Decode a JWT (claims peek, no signature check), base64, or URL-encoded text."""
    import base64 as b64
    import urllib.parse

    text = (text or "").strip().strip("'\"")
    if not text:
        raise ToolError("Paste the JWT, base64, or URL-encoded text to decode.")

    segments = text.split(".")
    if len(segments) == 3 and all(segments) and all(s.startswith(("ey", "e", "Q")) for s in segments[:2]):
        def _b64url(segment: str) -> dict:
            padded = segment + "=" * (-len(segment) % 4)
            try:
                return json.loads(b64.urlsafe_b64decode(padded))
            except Exception as exc:
                raise ToolError("That JWT segment is not valid base64 JSON.")
        return {
            "kind": "jwt",
            "header": _b64url(segments[0]),
            "payload": _b64url(segments[1]),
            "note": "Claims peek only — the signature was NOT verified.",
        }

    if "%" in text:
        return {"kind": "url", "decoded": urllib.parse.unquote_plus(text)}

    padded = text + "=" * (-len(text) % 4)
    for decoder in (b64.urlsafe_b64decode, b64.b64decode):
        try:
            decoded = decoder(padded).decode("utf-8")
            return {"kind": "base64", "decoded": decoded}
        except Exception:
            continue
    raise ToolError("Could not decode the input as JWT, base64, or URL encoding.")


async def dns_lookup(host: str) -> dict:
    """DNS A/CNAME lookup via DNS-over-HTTPS (dns.google)."""
    import re as _re

    host = (host or "").strip().lower().rstrip(".")
    if not _re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host):
        raise ToolError(f"'{host}' is not a valid domain name.")
    data = await _get_json(DNS_URL, {"name": host, "type": "A"})
    if data.get("Status") == 3:
        raise ToolError(f"'{host}' does not exist (NXDOMAIN).")
    answers = data.get("Answer") or []
    addresses = [a["data"] for a in answers if a.get("type") == 1]
    cnames = [a["data"] for a in answers if a.get("type") == 5]
    ttls = [a.get("TTL") for a in answers if a.get("type") == 1 and a.get("TTL")]
    if not addresses and not cnames:
        raise ToolError(f"No A or CNAME records found for '{host}'.")
    return {
        "host": host,
        "addresses": addresses,
        "cnames": cnames,
        "ttl": min(ttls) if ttls else None,
    }


async def ip_lookup(ip: str) -> dict:
    """IP geolocation + network owner via ipwho.is (keyless)."""
    import ipaddress as _ipaddress

    ip = (ip or "").strip()
    try:
        _ipaddress.ip_address(ip)
    except ValueError:
        raise ToolError(f"'{ip}' is not a valid IP address.")
    data = await _get_json(f"{IP_URL}/{ip}", {})
    if not data.get("success", True):
        raise ToolError(data.get("message") or f"Lookup for '{ip}' failed.")
    connection = data.get("connection") or {}
    return {
        "ip": data.get("ip", ip),
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": (data.get("timezone") or {}).get("id") if isinstance(data.get("timezone"), dict) else data.get("timezone"),
        "isp": connection.get("isp"),
        "org": connection.get("org"),
    }


def format_tool_result(tool_name: str, result: dict) -> str:
    """A human sentence from a tool result — the no-LLM fallback presentation."""
    if tool_name == "weather":
        city = result.get("city", "")
        country = result.get("country", "")
        place = f"{city}, {country}" if city and country else (city or country or "Unknown place")
        conditions = result.get("conditions", "unknown conditions")
        temp = result.get("temperature_c")
        feels = result.get("feels_like_c")
        parts = [f"{place}: {conditions}"]
        if temp is not None:
            feels_note = f" (feels like {feels}°C)" if feels is not None else ""
            parts.append(f"{temp}°C{feels_note}")
        if result.get("humidity_percent") is not None:
            parts.append(f"humidity {result['humidity_percent']}%")
        if result.get("wind_kmh") is not None:
            parts.append(f"wind {result['wind_kmh']} km/h")
        return ", ".join(parts) if parts else "No weather data."
    if tool_name == "currency":
        date = result.get("date", "")
        return (
            f"{result.get('amount')} {result.get('base')} = "
            f"{result.get('converted')} {result.get('target')} "
            f"(ECB reference rate 1 {result.get('base')} = {result.get('rate')}"
            f"{f', {date}' if date else ''})"
        )
    if tool_name == "time":
        return (
            f"{result.get('city')} — {result.get('weekday')}, {result.get('date')} "
            f"{result.get('time_12h')} ({result.get('timezone')}, UTC{result.get('utc_offset')})"
        )
    if tool_name == "epoch":
        precision_note = f" (input read as {result.get('precision')})" if result.get("precision") == "milliseconds" else ""
        return (
            f"{result.get('epoch')} = {result.get('iso_utc')}"
            f"{precision_note}, a {result.get('weekday')}"
        )
    if tool_name == "decode":
        kind = result.get("kind", "text")
        if kind == "jwt":
            payload = result.get("payload") or {}
            claims = ", ".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in list(payload.items())[:8])
            exp = payload.get("exp")
            exp_note = ""
            if exp:
                try:
                    expiry = convert_epoch(exp)
                    exp_note = f" — exp {exp} is {expiry['iso_utc']}"
                except ToolError:
                    pass
            return f"JWT payload ({len(payload)} claims): {claims}. Header alg: {result.get('header', {}).get('alg')}. {result.get('note', '')}{exp_note}"
        return f"Decoded {kind}: {result.get('decoded', '')}"
    if tool_name == "dns":
        parts = [f"{result.get('host')} resolves to"]
        if result.get("addresses"):
            parts.append(f"A records: {', '.join(result['addresses'])}")
        if result.get("cnames"):
            parts.append(f"CNAME: {', '.join(result['cnames'])}")
        if result.get("ttl") is not None:
            parts.append(f"TTL {result['ttl']}s")
        return " · ".join(parts)
    if tool_name == "ip":
        where = ", ".join(str(part) for part in (result.get("city"), result.get("region"), result.get("country")) if part)
        owner = result.get("isp") or result.get("org") or ""
        return f"{result.get('ip')} — {where or 'location unknown'}" + (f" ({owner})" if owner else "")
    return json.dumps(result, ensure_ascii=False)
