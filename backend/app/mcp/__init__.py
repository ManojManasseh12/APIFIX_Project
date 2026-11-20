"""Google Workspace connectors (Calendar + Gmail) over remote MCP.

The pieces live in this package:

- ``crypto``  — Fernet encryption for stored OAuth tokens
- ``store``   — ``mcp_connections`` table (Postgres or local SQLite)
- ``client``  — a small Streamable-HTTP MCP JSON-RPC client
- ``google``  — OAuth flow, token refresh, read-only tool allowlist

Nothing here trusts a URL from the browser: the MCP server URLs come from
settings, and only allowlisted tool names are ever called.
"""
