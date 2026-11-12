"""Real tools the router can invoke instead of the knowledge base.

Two tools, both keyless public APIs with FIXED hosts:

- Weather: Open-Meteo geocoding + forecast (geocoding-api.open-meteo.com,
  api.open-meteo.com)
- Currency conversion: Frankfurter ECB reference rates (api.frankfurter.dev)

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

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

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
    return json.dumps(result, ensure_ascii=False)
