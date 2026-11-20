"""Encryption for stored OAuth tokens.

A Google refresh token is a long-lived credential: anyone who reads the
database could read the user's mail and calendar with it. Tokens are
therefore encrypted with ``MCP_TOKEN_ENCRYPTION_KEY`` (Fernet: AES-128-CBC
+ HMAC) before they are written, and decrypted only in memory.

The key never leaves the backend. If it is missing or malformed, the
Google connectors report that honestly instead of storing plaintext.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

KEY_HELP = (
    "Generate one with: python -c "
    '"from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)


class CryptoNotConfigured(Exception):
    """MCP_TOKEN_ENCRYPTION_KEY is missing or not a valid Fernet key."""


def _fernet() -> Fernet:
    key = (settings.mcp_token_encryption_key or "").strip()
    if not key:
        raise CryptoNotConfigured(
            f"MCP_TOKEN_ENCRYPTION_KEY is not set, so Google tokens cannot be "
            f"encrypted. {KEY_HELP}"
        )
    try:
        return Fernet(key.encode())
    except Exception as exc:  # ValueError for a malformed key
        raise CryptoNotConfigured(
            f"MCP_TOKEN_ENCRYPTION_KEY is not a valid Fernet key. {KEY_HELP}"
        ) from exc


def is_valid_key() -> bool:
    """True when a usable encryption key is configured."""
    try:
        _fernet()
        return True
    except CryptoNotConfigured:
        return False


def encrypt(value: str | None) -> str | None:
    """Encrypt a token (None/empty passes through unchanged)."""
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    """Decrypt a stored token; a wrong key is an explainable error."""
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise CryptoNotConfigured(
            "Stored Google tokens cannot be decrypted with the current "
            "MCP_TOKEN_ENCRYPTION_KEY. If the key changed, reconnect Google."
        ) from exc
