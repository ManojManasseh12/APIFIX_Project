"""A minimal Streamable-HTTP MCP client (JSON-RPC 2.0).

Google's remote MCP servers speak the Model Context Protocol over HTTPS:
each call is a POST of a JSON-RPC message, and the response is either
``application/json`` or a ``text/event-stream`` with ``data:`` frames.

This client does exactly what the Ask tab needs:

    initialize  →  notifications/initialized  →  tools/call

No session is kept between calls. That costs one extra round trip per
tool call but keeps the backend stateless, which matters because Render
can restart a worker at any time.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "apifix-ai", "version": "1.0"}
_TIMEOUT = httpx.Timeout(45.0, connect=10.0)


class MCPError(Exception):
    """The MCP server could not answer (network, protocol, or tool error)."""


class MCPAuthError(MCPError):
    """The access token was rejected (401) — refresh or reconnect."""


def _headers(token: str, session_id: str | None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _messages(response: httpx.Response) -> list[dict[str, Any]]:
    """JSON-RPC messages from a JSON or SSE response body."""
    content_type = response.headers.get("content-type", "")
    text = response.text or ""

    if "text/event-stream" in content_type:
        messages: list[dict[str, Any]] = []
        for block in text.split("\n\n"):
            data = [
                line[5:].strip()
                for line in block.splitlines()
                if line.startswith("data:")
            ]
            if not data:
                continue
            try:
                parsed = json.loads("\n".join(data))
            except ValueError:
                continue
            if isinstance(parsed, list):
                messages.extend(parsed)
            elif isinstance(parsed, dict):
                messages.append(parsed)
        return messages

    try:
        parsed = json.loads(text)
    except ValueError:
        return []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return [parsed] if isinstance(parsed, dict) else []


def _explain_status(response: httpx.Response) -> str:
    if response.status_code == 401:
        return "Google rejected the access token (401). Reconnect Google in the Connections sidebar."
    if response.status_code == 403:
        return (
            "Google denied the MCP request (403). The OAuth client must have "
            "'Use this client for an AI-powered agent' enabled, and the "
            "project must be in the Google Workspace Developer Preview Program."
        )
    if response.status_code == 404:
        return "The Google MCP endpoint was not found (404). Check the server URL."
    return f"Google MCP returned HTTP {response.status_code}."


async def _post(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    message: dict[str, Any],
    session_id: str | None,
) -> tuple[httpx.Response, list[dict[str, Any]]]:
    try:
        response = await client.post(
            url, json=message, headers=_headers(token, session_id)
        )
    except httpx.HTTPError as exc:
        raise MCPError(f"Google MCP is unreachable ({type(exc).__name__}).") from exc

    if response.status_code == 401:
        raise MCPAuthError(_explain_status(response))
    if response.status_code >= 400:
        raise MCPError(_explain_status(response))
    return response, _messages(response)


def _pick(messages: list[dict[str, Any]], request_id: int) -> dict[str, Any] | None:
    for message in messages:
        if message.get("id") == request_id:
            return message
    return None


async def call_tool(
    server_url: str,
    access_token: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one MCP tool and return its raw ``result`` object."""
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, trust_env=False, follow_redirects=True
    ) as client:
        response, messages = await _post(
            client,
            server_url,
            access_token,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
            },
            None,
        )
        session_id = response.headers.get("mcp-session-id")
        init = _pick(messages, 1)
        if init and init.get("error"):
            error = init["error"]
            raise MCPError(
                f"MCP initialize failed: {error.get('message') or error}"
            )

        # Required by the protocol before any tool call.
        await _post(
            client,
            server_url,
            access_token,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id,
        )

        _, messages = await _post(
            client,
            server_url,
            access_token,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments or {}},
            },
            session_id,
        )

    reply = _pick(messages, 2)
    if reply is None:
        raise MCPError("Google MCP returned no response for the tool call.")
    if reply.get("error"):
        error = reply["error"]
        raise MCPError(
            f"MCP tool '{tool_name}' failed: {error.get('message') or error}"
        )

    result = reply.get("result")
    if not isinstance(result, dict):
        raise MCPError("Google MCP returned an unexpected tool result.")

    if result.get("isError"):
        text = _text_of(result) or "the tool reported an error"
        raise MCPError(f"Google MCP tool '{tool_name}' failed: {text}")
    return result


def _text_of(result: dict[str, Any]) -> str:
    parts = [
        str(item.get("text"))
        for item in result.get("content") or []
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part)


def payload_of(result: dict[str, Any]) -> Any:
    """Structured content when present, else the JSON text, else the text."""
    structured = result.get("structuredContent")
    if structured:
        return structured

    text = _text_of(result)
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


async def list_tools(server_url: str, access_token: str) -> list[dict[str, Any]]:
    """The server's advertised tools (used by the connection detail view)."""
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, trust_env=False, follow_redirects=True
    ) as client:
        response, messages = await _post(
            client,
            server_url,
            access_token,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
            },
            None,
        )
        session_id = response.headers.get("mcp-session-id")
        await _post(
            client,
            server_url,
            access_token,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id,
        )
        _, messages = await _post(
            client,
            server_url,
            access_token,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            session_id,
        )

    reply = _pick(messages, 2) or {}
    result = reply.get("result") or {}
    tools = result.get("tools") if isinstance(result, dict) else None
    return tools if isinstance(tools, list) else []
