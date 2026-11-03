import asyncio
import logging
import random
import re
from collections.abc import Sequence

from google import genai

from app.ai.llm import is_retryable_error
from app.ai.rag.models import Chunk
from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_EMBEDDING_ATTEMPTS = 3
MAX_RETRY_DELAY_S = 8.0
# Gemini rate limits can ask for ~60s on the free tier. Waiting less just burns
# another attempt on the same quota window.
_MAX_QUOTA_RETRY_DELAY_S = 65.0
_RETRY_DELAY_RE = re.compile(r"retry in (\d+(?:\.\d+)?)s", re.IGNORECASE)


def _retry_delay(exc: BaseException) -> float | None:
    """Return the server's suggested retry delay, if Gemini provided one."""
    match = _RETRY_DELAY_RE.search(str(exc))
    if not match:
        return None
    return min(float(match.group(1)), _MAX_QUOTA_RETRY_DELAY_S)


def _api_key() -> str:
    """Return the first configured Gemini key, supporting both env formats."""
    key = (settings.gemini_api_key or "").strip()
    if key:
        return key
    keys = [part.strip() for part in (settings.gemini_api_keys or "").split(",")]
    return next((part for part in keys if part), "")


class Embedding:
    """Gemini embedding client (same model family as cloud_llm)."""

    def __init__(self, batch_size: int = 64, prompt_cache_size: int = 256):
        self.batch_size = batch_size
        self.prompt_cache_size = prompt_cache_size
        self._client: genai.Client | None = None
        self._prompt_cache: dict[tuple[str, int, str], tuple[float, ...]] = {}

    @property
    def model_name(self) -> str:
        return settings.embed_model

    @property
    def dimensions(self) -> int:
        return settings.embed_dim

    def _get_client(self) -> genai.Client:
        if self._client is None:
            api_key = _api_key()
            if not api_key:
                raise RuntimeError(
                    "GEMINI_API_KEY is required for Gemini embeddings. "
                    "Add it to the project .env file."
                )
            self._client = genai.Client(api_key=api_key)
        return self._client

    async def embed_chunks(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        """Embed document chunks for retrieval indexing."""
        if not chunks:
            return []
        return await self.embed_texts(
            [chunk.content for chunk in chunks],
            task_type="RETRIEVAL_DOCUMENT",
        )

    async def embed_prompt(self, text: str) -> list[float]:
        """Embed a user query for retrieval, reusing identical queries."""
        key = (self.model_name, self.dimensions, text)
        cached = self._prompt_cache.get(key)
        if cached is not None:
            return list(cached)

        vectors = await self.embed_texts([text], task_type="RETRIEVAL_QUERY")
        vector = vectors[0]
        if len(self._prompt_cache) >= self.prompt_cache_size:
            self._prompt_cache.pop(next(iter(self._prompt_cache)))
        self._prompt_cache[key] = tuple(vector)
        return vector

    async def embed_texts(
        self,
        texts: Sequence[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
        *,
        max_attempts: int | None = None,
        batch_size: int | None = None,
    ) -> list[list[float]]:
        """Embed texts in batches, retrying transient Gemini failures."""
        if not texts:
            return []

        attempts = MAX_EMBEDDING_ATTEMPTS if max_attempts is None else max_attempts
        size = batch_size or self.batch_size
        if attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if size < 1:
            raise ValueError("batch_size must be at least 1")

        client = self._get_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), size):
            batch = list(texts[start : start + size])
            vectors.extend(
                await self._embed_batch(client, batch, task_type, attempts)
            )
        return vectors

    async def _embed_batch(
        self,
        client: genai.Client,
        batch: list[str],
        task_type: str,
        attempts: int,
    ) -> list[list[float]]:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.aio.models.embed_content(
                    model=self.model_name,
                    contents=batch,
                    config={
                        "task_type": task_type,
                        "output_dimensionality": self.dimensions,
                    },
                )
                embeddings = list(response.embeddings or [])
                if len(embeddings) != len(batch):
                    raise ValueError(
                        f"Gemini returned {len(embeddings)} embeddings for "
                        f"{len(batch)} input chunks."
                    )
                vectors = [list(item.values) for item in embeddings]
                for vector in vectors:
                    if len(vector) != self.dimensions:
                        raise ValueError(
                            f"Expected {self.dimensions}-dimensional vectors "
                            f"from {self.model_name}, received {len(vector)}."
                        )
                return vectors
            except Exception as exc:
                if attempt >= attempts or not is_retryable_error(exc):
                    raise
                delay = _retry_delay(exc)
                if delay is None:
                    delay = min(
                        MAX_RETRY_DELAY_S,
                        2 ** (attempt - 1),
                    ) * (0.5 + random.random())
                logger.warning(
                    "Gemini embedding attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("Gemini embedding failed without an exception")


embedding = Embedding()
