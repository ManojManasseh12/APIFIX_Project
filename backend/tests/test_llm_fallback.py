import asyncio

import pytest

from app.ai.llm import (
    error_reason,
    get_providers,
    is_quota_error,
    is_retryable_error,
    stream_with_fallback,
)
from app.core.errors import StreamInterrupted
from app.core.config import settings


@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch):
    """Keep real .env provider credentials out of provider-selection tests."""
    for name in (
        "PROVIDER_ORDER",
        "GEMINI_API_KEY",
        "GEMINI_API_KEYS",
        "GEMINI_MODEL",
        "GROQ_API_KEY",
        "GROQ_API_KEYS",
        "GROQ_MODEL",
        "MISTRAL_API_KEY",
        "MISTRAL_API_KEYS",
        "MISTRAL_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "llm_max_attempts_per_provider", 3)
    monkeypatch.setattr(settings, "llm_retry_base_delay_s", 0.0)
    monkeypatch.setattr(settings, "llm_retry_max_delay_s", 0.0)


class FakeProvider:
    def __init__(self, name: str, tokens: list[str], error: Exception | None = None):
        self.name = name
        self.label = name
        self.tokens = tokens
        self.error = error
        self.attempts = 0

    def available(self) -> bool:
        return True

    async def stream(self, system: str, user: str):
        self.attempts += 1
        for token in self.tokens:
            yield token
        if self.error is not None:
            raise self.error


class QuotaError(Exception):
    status_code = 429


class AuthError(Exception):
    status_code = 401


def collect(providers, on_skip=None):
    async def run():
        chunks = []
        async for chunk in stream_with_fallback(
            providers, "system", "user", on_skip=on_skip
        ):
            chunks.append(chunk)
        return chunks

    return asyncio.run(run())


def test_detects_quota_and_rate_limit_errors():
    assert is_quota_error(QuotaError("too many requests"))
    assert is_quota_error(Exception("RESOURCE_EXHAUSTED: quota exceeded"))
    assert is_retryable_error(QuotaError("rate limited"))
    assert is_retryable_error(Exception("Service unavailable"))
    assert not is_retryable_error(AuthError("invalid api key"))
    assert error_reason(QuotaError("rate limit")) == "quota_exceeded"
    assert error_reason(AuthError("bad key")) == "auth_error"


def test_falls_back_to_next_model_before_first_token():
    skipped = []
    providers = [
        FakeProvider("gemini:model-a", [], QuotaError("429 quota")),
        FakeProvider("gemini:model-b", ["hello", " world"]),
    ]

    chunks = collect(
        providers, on_skip=lambda n, r, h, started: skipped.append((n, r, h, started))
    )

    assert chunks == [
        ("gemini:model-b", "hello"),
        ("gemini:model-b", " world"),
    ]
    assert skipped == [("gemini:model-a", "quota_exceeded", True, False)]
    assert providers[0].attempts == 3


def test_retries_same_model_then_succeeds(monkeypatch):
    class FlakyProvider:
        name = "gemini"
        label = "gemini:model-a"

        def __init__(self):
            self.attempts = 0

        def available(self) -> bool:
            return True

        async def stream(self, system: str, user: str):
            self.attempts += 1
            if self.attempts < 3:
                raise QuotaError("429 quota")
            yield "recovered"

    provider = FlakyProvider()
    skipped = []

    chunks = collect(
        [provider],
        on_skip=lambda *args: skipped.append(args),
    )

    assert chunks == [("gemini:model-a", "recovered")]
    assert provider.attempts == 3
    assert skipped == []


def test_does_not_retry_auth_errors():
    provider = FakeProvider("gemini:model-a", [], AuthError("bad key"))
    with pytest.raises(Exception):
        collect([provider])
    assert provider.attempts == 1


def test_restarts_on_next_model_after_partial_output_when_enabled():
    providers = [
        FakeProvider("gemini:model-a", ["partial"], QuotaError("429 quota")),
        FakeProvider("gemini:model-b", ["clean answer"]),
    ]

    chunks = []

    async def run():
        async for chunk in stream_with_fallback(
            providers, "system", "user", restart_after_partial=True
        ):
            chunks.append(chunk)

    asyncio.run(run())

    assert chunks == [
        ("gemini:model-a", "partial"),
        ("gemini:model-b", "clean answer"),
    ]


def test_does_not_splice_after_stream_started():
    providers = [
        FakeProvider("gemini:model-a", ["partial"], QuotaError("429 quota")),
        FakeProvider("gemini:model-b", ["other answer"]),
    ]

    with pytest.raises(StreamInterrupted):
        collect(providers)


def test_get_providers_expands_models_and_keys(monkeypatch):
    monkeypatch.setenv("PROVIDER_ORDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEYS", "key-1,key-2")
    monkeypatch.setenv("GEMINI_MODEL", "model-a,model-b")

    labels = [provider.label for provider in get_providers()]

    assert labels == [
        "gemini:model-a",
        "gemini:model-a",
        "gemini:model-b",
        "gemini:model-b",
    ]


def test_ask_context_keeps_only_last_four_messages():
    from types import SimpleNamespace

    from app.ai.context import build_ask_context

    history = [
        SimpleNamespace(role="user", content=f"message-{index}")
        for index in range(6)
    ]
    retrieval = SimpleNamespace(chunks=[])

    context = build_ask_context("current question", history, retrieval)

    assert "message-0" not in context
    assert "message-1" not in context
    assert "message-2" in context
    assert "message-5" in context


def test_get_providers_uses_default_model_for_key_only(monkeypatch):
    monkeypatch.setenv("PROVIDER_ORDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "key-1")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    labels = [provider.label for provider in get_providers()]

    assert labels == [
        "gemini:gemini-flash-latest",
        "gemini:gemini-3.5-flash",
    ]
