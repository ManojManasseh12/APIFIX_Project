import asyncio

from app.ai.llm import get_providers


async def check_provider(provider) -> None:
    try:
        tokens = [
            token
            async for token in provider.stream(
                "You are a health check. Reply with the single word OK.",
                "Reply with OK.",
            )
        ]
        print(f"{provider.label}: ok -> {''.join(tokens).strip()[:40]}")
    except Exception as exc:
        print(f"{provider.label}: FAILED {type(exc).__name__}: {exc}")


async def check_embedding() -> None:
    from app.ai.rag.embedding import embedding

    try:
        vector = await embedding.embed_prompt("hello world")
        print(
            f"{embedding.model_name}: ok -> {len(vector)} dims"
        )
    except Exception as exc:
        print(f"{embedding.model_name}: FAILED {type(exc).__name__}: {exc}")


async def main() -> None:
    await check_embedding()

    providers = get_providers()
    if not providers:
        print(
            "No LLM providers configured. Set GEMINI_API_KEY, GROQ_API_KEY or "
            "MISTRAL_API_KEY in .env."
        )
        return
    for provider in providers:
        await check_provider(provider)


if __name__ == "__main__":
    asyncio.run(main())
