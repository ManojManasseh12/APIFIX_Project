"""Supabase Auth token verification (FastAPI dependency).

The Supabase project issues ES256/RS256 access tokens after login. This
backend never sees passwords — it only verifies
the JWT the frontend sends on every request:

1. Read the ``Authorization: Bearer <token>`` header.
2. Fetch the project's public keys from its JWKS endpoint (cached).
3. Verify signature, expiry, and issuer; reject anything else with 401.
4. Return ``{"user_id": ..., "email": ...}`` from the token's ``sub`` claim,
   after mirroring the account into ``public.profiles`` (best effort).

When ``SUPABASE_AUTH_JWKS_URL`` is not configured the dependency is a
pass-through that returns ``None``, so local development and the test
suite keep working without authentication. Production sets the env var,
which switches every protected router into verified mode.
"""

import logging
import threading
from typing import Any

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWK, PyJWKClient

from app.core.config import settings
from app.db.profiles import record_login

logger = logging.getLogger(__name__)

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}

_jwk_client: PyJWKClient | None = None
_jwk_client_lock = threading.Lock()

# How long the fetched JWKS document stays cached before a refresh.
_JWKS_CACHE_LIFETIME_S = 3600

# Clock skew tolerated when checking the exp claim.
_TOKEN_LEEWAY_S = 30


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        with _jwk_client_lock:
            if _jwk_client is None:
                _jwk_client = PyJWKClient(
                    settings.supabase_auth_jwks_url,
                    cache_jwk_set=True,
                    lifespan=_JWKS_CACHE_LIFETIME_S,
                )
    return _jwk_client


def _decode_token(token: str) -> dict[str, Any]:
    """Verify one access token and return its claims.

    The allowed algorithm is taken from the matched JWKS key itself, so a
    token signed with an unexpected algorithm can never pass. Raises any
    PyJWT error; callers turn failures into a uniform 401.
    """
    signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=[getattr(signing_key, "algorithm_name", None) or "ES256"],
        audience="authenticated",
        issuer=settings.auth_issuer,
        leeway=_TOKEN_LEEWAY_S,
        options={"require": ["exp", "sub"]},
    )


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers=_UNAUTHORIZED_HEADERS)


def get_current_user(request: Request) -> dict[str, Any] | None:
    """FastAPI dependency: the verified user, or None when auth is off.

    Attach with ``Depends(get_current_user)`` — either in the handler
    signature (when the user_id is needed) or in
    ``APIRouter(dependencies=...)`` for enforcement-only routers.
    """
    if not settings.auth_enabled:
        return None

    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()

    try:
        claims = _decode_token(token.strip())
    except jwt.PyJWKClientError:
        # JWKS unreachable or no matching key: fail closed, but log why.
        logger.warning("Could not verify token against the auth JWKS", exc_info=True)
        raise _unauthorized("Token verification failed")
    except jwt.PyJWTError:
        # Bad signature, expired, wrong issuer/audience, missing claims.
        logger.info("Rejected invalid access token", exc_info=True)
        raise _unauthorized("Invalid or expired token")

    user = {
        "user_id": claims["sub"],
        "email": claims.get("email"),
        "role": claims.get("role"),
        "app_metadata": claims.get("app_metadata"),
    }
    # Mirror the account into public.profiles so signed-in users show up
    # in the Table Editor (Supabase's own auth.users is not exposed there).
    record_login(user)
    return user
