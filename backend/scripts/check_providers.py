import asyncio
import os

from dotenv import load_dotenv

load_dotenv()


async def check_gemini():
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ["GEMINI_MODEL"]
    result = await client.aio.models.generate_content(
        model=model, contents="Reply with the single word OK."
    )
    print("gemini:", model, "->", result.text.strip()[:40])


async def check_groq():
    from groq import AsyncGroq

    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    model = os.environ["GROQ_MODEL"]
    result = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with OK."}],
        max_tokens=5,
    )
    print("groq:", model, "->", result.choices[0].message.content.strip())


async def check_mistral():
    from mistralai import Mistral

    client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
    model = os.environ["MISTRAL_MODEL"]
    result = await client.chat.complete_async(
        model=model,
        messages=[{"role": "user", "content": "Reply with OK."}],
        max_tokens=5,
    )
    print("mistral:", model, "->", result.choices[0].message.content.strip())


def check_embedding():
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    vector = model.encode(["hello world"])[0]
    print("minilm:", len(vector), "dims")


async def main():
    for fn in (check_gemini, check_groq, check_mistral):
        try:
            await fn()
        except Exception as exc:
            print(fn.__name__, "FAILED:", type(exc).__name__, exc)
    check_embedding()


if __name__ == "__main__":
    asyncio.run(main())
