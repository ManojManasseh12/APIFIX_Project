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


def check_embedding() -> None:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    vector = model.encode(["hello world"])[0]
    print("minilm:", len(vector), "dims")


async def main() -> None:
    providers = get_providers()
    if not providers:
        print(
            "No providers configured. Set GEMINI_API_KEY, GROQ_API_KEY or "
            "MISTRAL_API_KEY in .env."
        )
        return
    for provider in providers:
        await check_provider(provider)
    check_embedding()


if __name__ == "__main__":
    asyncio.run(main())
