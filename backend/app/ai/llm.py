import inspect
import os
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from app.core.errors import AllProvidersFailed, StreamInterrupted


class Provider(Protocol):
    name: str

    def available(self) -> bool: ...

    async def stream(self, system: str, user: str) -> AsyncIterator[str]: ...


@dataclass
class GeminiProvider:
    name: str = "gemini"

    def available(self) -> bool:
        return bool(os.getenv("GEMINI_API_KEY") and os.getenv("GEMINI_MODEL"))

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        from google import genai

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        stream = client.aio.models.generate_content_stream(
            model=os.environ["GEMINI_MODEL"],
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
    name: str = "groq"

    def available(self) -> bool:
        return bool(os.getenv("GROQ_API_KEY") and os.getenv("GROQ_MODEL"))

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        from groq import AsyncGroq

        client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
        stream = await client.chat.completions.create(
            model=os.environ["GROQ_MODEL"],
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
    name: str = "mistral"

    def available(self) -> bool:
        return bool(os.getenv("MISTRAL_API_KEY") and os.getenv("MISTRAL_MODEL"))

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        from mistralai import Mistral

        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        stream = await client.chat.stream_async(
            model=os.environ["MISTRAL_MODEL"],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        async for event in stream:
            delta = event.data.choices[0].delta.content
            if delta:
                yield delta


def get_providers() -> list[Provider]:
    candidates: list[Provider] = [GeminiProvider(), GroqProvider(), MistralProvider()]
    return [provider for provider in candidates if provider.available()]


async def stream_with_fallback(
    providers: list[Provider], system: str, user: str
) -> AsyncIterator[tuple[str, str]]:
    errors: list[str] = []
    for provider in providers:
        started = False
        try:
            async for token in provider.stream(system, user):
                started = True
                yield provider.name, token
            return
        except Exception as exc:
            errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")
            if started:
                raise StreamInterrupted(
                    "Provider failed after output began; restart the diagnosis."
                ) from exc
            continue
    raise AllProvidersFailed("; ".join(errors) or "no providers configured")

