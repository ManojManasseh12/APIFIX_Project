import asyncio
import inspect
import logging
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Protocol

from dotenv import load_dotenv

from app.core.errors import AllProvidersFailed, StreamInterrupted

# Provider adapters read os.environ directly, so load .env here as well. This
# keeps get_providers() usable from scripts and tests, not only after the
# FastAPI app has imported the settings module.
load_dotenv()

logger = logging.getLogger(__name__)

# HTTP statuses that are worth retrying on another model/provider.
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
AUTH_STATUS = {401, 403}

_QUOTA_MARKERS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "quota",
    "resource exhausted",
    "resource_exhausted",
    "too many requests",
    "insufficient_quota",
    "exceeded your current",
    "free tier",
    "billing",
    "credit",
)
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "connection reset",
    "temporarily unavailable",
    "service unavailable",
    "overloaded",
    "server error",
    "bad gateway",
    "gateway timeout",
    "try again",
    "unavailable",
)


class Provider(Protocol):
    name: str
    label: str

    def available(self) -> bool: ...

    async def stream(self, system: str, user: str) -> AsyncIterator[str]: ...


@dataclass
class GeminiProvider:
    api_key: str = ""
    model: str = ""
    name: str = "gemini"

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def available(self) -> bool:
        return bool(self.api_key and self.model)

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        from google import genai

        client = genai.Client(api_key=self.api_key)
        stream = client.aio.models.generate_content_stream(
            model=self.model,
            contents=f"{system}\n\n{user}",
        )
        if inspect.isawaitable(stream):
            stream = await stream
        async for chunk in stream:
            text = getattr(chunk, "text", None)
            if text:
                yield text


@dataclass
class GroqProvider:
    api_key: str = ""
    model: str = ""
    name: str = "groq"

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def available(self) -> bool:
        return bool(self.api_key and self.model)

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        from groq import AsyncGroq

        client = AsyncGroq(api_key=self.api_key)
        stream = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            stream=True,
        )
        async for event in stream:
            delta = event.choices[0].delta.content
            if delta:
                yield delta


@dataclass
class MistralProvider:
    api_key: str = ""
    model: str = ""
    name: str = "mistral"

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def available(self) -> bool:
        return bool(self.api_key and self.model)

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        try:
            from mistralai import Mistral
        except ImportError:  # mistralai >= 3.6 exposes the client here
            from mistralai.client import Mistral

        client = Mistral(api_key=self.api_key)
        stream = await client.chat.stream_async(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        async for event in stream:
            delta = event.data.choices[0].delta.content
            if delta:
                yield delta


def _split_env(name: str) -> list[str]:
    return _split_value(os.getenv(name, ""))


def _split_value(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _keys(single: str, plural: str) -> list[str]:
    """Read comma-separated keys, falling back to the single-key variable."""
    keys = _split_env(plural)
    if not keys:
        value = os.getenv(single, "").strip()
        if value:
            keys = [value]
    return keys


def _series(
    provider_cls: type[GeminiProvider] | type[GroqProvider] | type[MistralProvider],
    key_env: str,
    keys_env: str,
    model_env: str,
    default_models: str,
) -> list[Provider]:
    """Build one provider attempt per (model, key) pair.

    Quotas are usually enforced per model and/or per key, so listing several
    models (or keys) lets the chain move to a fresh quota automatically.
    """
    keys = _keys(key_env, keys_env)
    models = _split_env(model_env) or _split_value(default_models)
    if not keys or not models:
        return []
    return [
        provider_cls(api_key=key, model=model)
        for model in models
        for key in keys
    ]


_FAMILIES: dict[str, tuple[type, str, str, str, str]] = {
    "gemini": (
        GeminiProvider,
        "GEMINI_API_KEY",
        "GEMINI_API_KEYS",
        "GEMINI_MODEL",
        "gemini-flash-latest,gemini-3.5-flash",
    ),
    "groq": (
        GroqProvider,
        "GROQ_API_KEY",
        "GROQ_API_KEYS",
        "GROQ_MODEL",
        "openai/gpt-oss-120b,qwen/qwen3.8-27b",
    ),
    "mistral": (
        MistralProvider,
        "MISTRAL_API_KEY",
        "MISTRAL_API_KEYS",
        "MISTRAL_MODEL",
        "mistral-small-latest",
    ),
}


def get_providers() -> list[Provider]:
    """Configured provider/model attempts in the order they should be tried."""
    order = [
        name.strip().lower()
        for name in os.getenv("PROVIDER_ORDER", "gemini,groq,mistral").split(",")
        if name.strip()
    ]
    # Any configured family missing from PROVIDER_ORDER still gets a turn.
    order.extend(name for name in _FAMILIES if name not in order)

    providers: list[Provider] = []
    for family in order:
        spec = _FAMILIES.get(family)
        if spec is None:
            continue
        provider_cls, key_env, keys_env, model_env, default_models = spec
        providers.extend(
            _series(provider_cls, key_env, keys_env, model_env, default_models)
        )
    return providers


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int) and 100 <= code <= 599:
        return code
    return None


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".lower()


def is_quota_error(exc: BaseException) -> bool:
    """True when the failure is a rate limit / exhausted quota."""
    if _status_code(exc) == 429:
        return True
    text = _error_text(exc)
    return any(marker in text for marker in _QUOTA_MARKERS)


def is_retryable_error(exc: BaseException) -> bool:
    """True for failures where switching to another model/key can help."""
    status = _status_code(exc)
    if status in RETRYABLE_STATUS:
        return True
    if status in AUTH_STATUS:
        return False
    text = _error_text(exc)
    if any(marker in text for marker in _QUOTA_MARKERS):
        return True
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def error_reason(exc: BaseException) -> str:
    """Short machine-readable label for the frontend/logs."""
    if is_quota_error(exc):
        return "quota_exceeded"
    status = _status_code(exc)
    if status in AUTH_STATUS:
        return "auth_error"
    if status is not None and status >= 500:
        return "server_error"
    if is_retryable_error(exc):
        return "transient_error"
    return "provider_error"


async def _timed_stream(
    provider: Provider,
    system: str,
    user: str,
    first_token_timeout: float,
    overall_timeout: float,
) -> AsyncIterator[str]:
    """Stream one provider with hang protection.

    A provider that never sends its first token (or stalls mid-stream) must
    fail fast so the quota fallback can try the next model.
    """
    iterator = provider.stream(system, user)
    started_at = time.monotonic()
    seen_token = False
    try:
        while True:
            elapsed = time.monotonic() - started_at
            timeout = (
                overall_timeout - elapsed
                if seen_token
                else first_token_timeout
            )
            if timeout <= 0:
                raise TimeoutError(
                    f"{provider.label} exceeded {overall_timeout:g}s"
                )
            try:
                token = await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
            except StopAsyncIteration:
                return
            seen_token = True
            yield token
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            with suppress(Exception):
                await aclose()


async def stream_with_fallback(
    providers: list[Provider],
    system: str,
    user: str,
    on_skip: Callable[[str, str, bool, bool], None] | None = None,
    restart_after_partial: bool = False,
) -> AsyncIterator[tuple[str, str]]:
    """Stream tokens, switching model/provider on quota or transient failures.

    Tokens are emitted as soon as a provider sends them. By default a fallback
    only happens before the first token, so two models' answers are never
    spliced together. `on_skip(provider, reason, has_next, output_started)` is
    called when an attempt fails and the next model/key is about to be tried.

    With `restart_after_partial=True` (used by the Q&A endpoint), a retryable
    failure after the first token discards the partial answer and restarts on
    the next model. The caller must clear its buffer when
    `output_started=True`.
    """
    first_token_timeout = float(os.getenv("LLM_FIRST_TOKEN_TIMEOUT_S", "20"))
    overall_timeout = float(os.getenv("LLM_TIMEOUT_S", "120"))
    errors: list[str] = []
    for index, provider in enumerate(providers):
        started = False
        try:
            async for token in _timed_stream(
                provider, system, user, first_token_timeout, overall_timeout
            ):
                if not started:
                    started = True
                    logger.info("LLM streaming from %s", provider.label)
                yield provider.label, token
            return
        except Exception as exc:
            detail = f"{provider.label}: {type(exc).__name__}: {exc}"
            errors.append(detail)
            has_next = index + 1 < len(providers)
            reason = error_reason(exc)

            if started:
                if not (restart_after_partial and has_next and is_retryable_error(exc)):
                    raise StreamInterrupted(
                        "Provider failed after output began; restart the answer."
                    ) from exc
                logger.warning(
                    "LLM provider %s failed after output (%s); restarting on next model",
                    provider.label,
                    reason,
                )
            else:
                logger.warning(
                    "LLM provider %s failed (%s): %s", provider.label, reason, exc
                )

            if on_skip is not None:
                on_skip(provider.label, reason, has_next, started)
            # Never retry the same model/key; move on to the next attempt.
            continue

    raise AllProvidersFailed("; ".join(errors) or "no providers configured")
