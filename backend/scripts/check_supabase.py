import asyncio
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.rag.pipeline import pipeline  # noqa: E402
from app.core.config import settings  # noqa: E402


def describe_database(url: str) -> str:
    if not url.strip():
        return "(unset)"
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError:
        return "(invalid DATABASE_URL; check the URL format and password encoding)"

    if not hostname:
        return "(invalid DATABASE_URL; hostname is missing)"
    if "<" in hostname or ">" in hostname:
        return "(invalid DATABASE_URL; replace the placeholder host)"

    try:
        port = parsed.port or 5432
    except ValueError:
        return "(invalid DATABASE_URL; check the port and password encoding)"

    return f"{hostname}:{port}{parsed.path or ''}"


async def main() -> int:
    print(f"RETRIEVAL_BACKEND = {settings.retrieval_backend}")
    print(f"DATABASE_URL       = {describe_database(settings.database_url)}")
    print(f"store              = {type(pipeline.store).__name__}")

    if type(pipeline.store).__name__ == "VectorStore":
        print(
            "\nWARNING: the app fell back to local Chroma. Check DATABASE_URL, "
            "the password encoding, sslmode=require, and that the schema was run."
        )

    stats = await pipeline.store.stats()
    print(f"\nchunks = {stats['chunk_count']}")
    for source in stats["sources"][:10]:
        print(f"  {source['source']}: {source['count']}")

    if stats["chunk_count"] == 0:
        print(
            "\nThe knowledge base is empty. Run "
            "`python backend/scripts/ingest.py`, or start the API to let the "
            "seed corpus load automatically."
        )
        return 1

    hits = await pipeline.retrieve(
        "expired bearer token returns 401", mode="hybrid", k=3
    )
    print("\nTop hybrid hits:")
    for hit in hits:
        title = hit.metadata.get("source_title") or hit.metadata.get("source")
        print(f"  {hit.chunk_id:<24} {hit.score:.4f}  {title}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
