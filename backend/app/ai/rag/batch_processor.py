"""Batched, quota-aware ingestion modelled on cloud_llm's batch processor.

Files are prepared concurrently, chunks are deduplicated within a batch, and
all chunks from the prepared files are embedded in token/count-bounded requests
before being stored per file. This keeps a large upload from making one huge
Gemini request or losing every file when one file fails.
"""

import asyncio
import hashlib
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.ai.rag.embedding import embedding
from app.ai.rag.models import Chunk
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class BatchSettings:
    file_batch_size: int = 10
    max_concurrency: int = 4
    max_chunks_per_embedding_request: int = 64
    max_tokens_per_embedding_request: int = 8_000
    embedding_requests_per_minute: int = 60
    embedding_tokens_per_minute: int = 100_000
    embedding_max_retries: int = 3
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 8.0

    @classmethod
    def from_settings(cls) -> "BatchSettings":
        return cls(
            file_batch_size=settings.batch_file_size,
            max_concurrency=settings.batch_max_concurrency,
            max_chunks_per_embedding_request=settings.embed_max_chunks_per_request,
            max_tokens_per_embedding_request=settings.embed_max_tokens_per_request,
            embedding_requests_per_minute=settings.embed_requests_per_minute,
            embedding_tokens_per_minute=settings.embed_tokens_per_minute,
            embedding_max_retries=settings.embed_max_retries,
            retry_base_delay_seconds=settings.embed_retry_base_delay_s,
            retry_max_delay_seconds=settings.embed_retry_max_delay_s,
        )


class Embedder(Protocol):
    async def embed_texts(
        self,
        texts: Sequence[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
        *,
        max_attempts: int | None = None,
        batch_size: int | None = None,
    ) -> list[list[float]]: ...


class IngestStore(Protocol):
    async def insert(
        self, chunks: list[Chunk], vectors: list[list[float]]
    ) -> int: ...


class BatchProcessor:
    def __init__(
        self,
        batch_settings: BatchSettings | None = None,
        embedder: Embedder | None = None,
    ):
        self.settings = batch_settings or BatchSettings.from_settings()
        self.embedder = embedder or embedding
        self._request_usage: deque[tuple[float, int]] = deque()
        self._quota_lock = asyncio.Lock()

    def _validate_settings(self) -> None:
        positive_settings = {
            "file_batch_size": self.settings.file_batch_size,
            "max_concurrency": self.settings.max_concurrency,
            "max_chunks_per_embedding_request": (
                self.settings.max_chunks_per_embedding_request
            ),
            "max_tokens_per_embedding_request": (
                self.settings.max_tokens_per_embedding_request
            ),
            "embedding_requests_per_minute": (
                self.settings.embedding_requests_per_minute
            ),
            "embedding_tokens_per_minute": self.settings.embedding_tokens_per_minute,
        }
        invalid = [name for name, value in positive_settings.items() if value < 1]
        if invalid:
            raise ValueError(
                "Batch settings must be at least 1: " + ", ".join(invalid)
            )
        if self.settings.embedding_max_retries < 0:
            raise ValueError("embedding_max_retries cannot be negative")
        if self.settings.retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds cannot be negative")
        if self.settings.retry_max_delay_seconds < 0:
            raise ValueError("retry_max_delay_seconds cannot be negative")

    @staticmethod
    def _count_tokens(text: str) -> int:
        # Gemini's tokenizer is not available locally; use a byte-based estimate
        # for the configurable request/token throttles.
        return max(1, math.ceil(len(text.encode("utf-8")) / 4))

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    async def _wait_for_embedding_quota(self, token_count: int) -> None:
        batch = self.settings
        if token_count > batch.embedding_tokens_per_minute:
            raise ValueError(
                f"Embedding request contains {token_count} tokens, exceeding the "
                f"per-minute token limit of {batch.embedding_tokens_per_minute}."
            )

        while True:
            async with self._quota_lock:
                now = time.monotonic()
                while (
                    self._request_usage
                    and now - self._request_usage[0][0] >= 60
                ):
                    self._request_usage.popleft()

                request_wait = 0.0
                if (
                    len(self._request_usage)
                    >= batch.embedding_requests_per_minute
                ):
                    request_wait = max(
                        0.0, self._request_usage[0][0] + 60 - now
                    )

                used_tokens = sum(tokens for _, tokens in self._request_usage)
                token_wait = 0.0
                if used_tokens + token_count > batch.embedding_tokens_per_minute:
                    tokens_to_free = (
                        used_tokens
                        + token_count
                        - batch.embedding_tokens_per_minute
                    )
                    freed_tokens = 0
                    for timestamp, used in self._request_usage:
                        freed_tokens += used
                        if freed_tokens >= tokens_to_free:
                            token_wait = max(0.0, timestamp + 60 - now)
                            break

                wait_seconds = max(request_wait, token_wait)
                if wait_seconds == 0:
                    self._request_usage.append((now, token_count))
                    return

            await asyncio.sleep(wait_seconds)

    async def _embed_with_retry(
        self, texts: list[str], token_count: int
    ) -> list[list[float]]:
        batch = self.settings
        for attempt in range(batch.embedding_max_retries + 1):
            await self._wait_for_embedding_quota(token_count)
            logger.info(
                "Embedding %d chunks (%d estimated tokens), attempt %d/%d",
                len(texts),
                token_count,
                attempt + 1,
                batch.embedding_max_retries + 1,
            )
            try:
                vectors = await self.embedder.embed_texts(
                    texts,
                    task_type="RETRIEVAL_DOCUMENT",
                    # Retries are handled here so the quota and backoff logic
                    # lives in one place, matching cloud_llm.
                    max_attempts=1,
                    batch_size=len(texts),
                )
                if len(vectors) != len(texts):
                    raise ValueError(
                        f"Embedding count mismatch: received {len(vectors)} "
                        f"embeddings for {len(texts)} chunks."
                    )
                return vectors
            except Exception:
                if attempt >= batch.embedding_max_retries:
                    raise

                delay = min(
                    batch.retry_max_delay_seconds,
                    batch.retry_base_delay_seconds * (2**attempt),
                )
                logger.warning(
                    "Embedding request failed (attempt %d/%d); retrying in "
                    "%.2f seconds",
                    attempt + 1,
                    batch.embedding_max_retries + 1,
                    delay,
                    exc_info=True,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("Embedding retries exhausted unexpectedly")

    async def embed_texts(
        self, texts: Sequence[str]
    ) -> list[list[float]]:
        """Embed arbitrary texts in count/token-bounded, quota-aware batches."""
        self._validate_settings()
        if not texts:
            return []

        text_batches: list[list[str]] = []
        token_batches: list[int] = []
        current_texts: list[str] = []
        current_tokens = 0

        for text in texts:
            token_count = await asyncio.to_thread(self._count_tokens, text)
            if token_count > self.settings.max_tokens_per_embedding_request:
                raise ValueError(
                    f"Chunk contains {token_count} estimated tokens, exceeding "
                    "the per-request limit of "
                    f"{self.settings.max_tokens_per_embedding_request}."
                )

            if current_texts and (
                len(current_texts)
                >= self.settings.max_chunks_per_embedding_request
                or current_tokens + token_count
                > self.settings.max_tokens_per_embedding_request
            ):
                text_batches.append(current_texts)
                token_batches.append(current_tokens)
                current_texts = []
                current_tokens = 0

            current_texts.append(text)
            current_tokens += token_count

        if current_texts:
            text_batches.append(current_texts)
            token_batches.append(current_tokens)

        vectors: list[list[float]] = []
        for batch_texts, token_count in zip(text_batches, token_batches):
            vectors.extend(
                await self._embed_with_retry(batch_texts, token_count)
            )
        return vectors

    async def process_files(
        self,
        paths: Sequence[Path],
        loader: Callable[[Path], Awaitable[list[Chunk]]],
        store: IngestStore,
    ) -> list[tuple[Path, dict[str, Any]]]:
        """Prepare files concurrently, embed the batch, then store per file."""
        self._validate_settings()
        results: dict[Path, dict[str, Any]] = {}
        prepared_files: list[tuple[Path, list[Chunk]]] = []
        semaphore = asyncio.Semaphore(self.settings.max_concurrency)

        async def hash_file(path: Path):
            async with semaphore:
                try:
                    return path, await asyncio.to_thread(self._file_hash, path)
                except Exception as error:
                    logger.exception(
                        "Failed to hash '%s' for batch ingestion", path.name
                    )
                    return path, error

        # Hash first and resolve duplicates in input order so the same file
        # batch always reports the same completed/skipped pair, regardless of
        # which file happens to finish reading first.
        hashed = await asyncio.gather(*(hash_file(path) for path in paths))
        batch_hashes: set[str] = set()
        load_paths: list[Path] = []
        for path, file_hash in hashed:
            if isinstance(file_hash, Exception):
                results[path] = {
                    "status": "failed",
                    "message": str(file_hash),
                    "chunks_added": 0,
                }
            elif file_hash in batch_hashes:
                results[path] = {
                    "status": "skipped",
                    "message": (
                        f"File '{path.name}' duplicates another file in this "
                        "batch."
                    ),
                    "chunks_added": 0,
                }
            else:
                batch_hashes.add(file_hash)
                load_paths.append(path)

        async def prepare_file(path: Path):
            async with semaphore:
                try:
                    chunks = await loader(path)
                    if not chunks:
                        return path, {
                            "status": "empty",
                            "message": (
                                f"File '{path.name}' has no extractable text."
                            ),
                            "chunks_added": 0,
                        }, None
                    return path, None, chunks
                except Exception as error:
                    logger.exception(
                        "Failed to prepare '%s' for batch ingestion", path.name
                    )
                    return path, {
                        "status": "failed",
                        "message": str(error),
                        "chunks_added": 0,
                    }, None

        prepared_results = await asyncio.gather(
            *(prepare_file(path) for path in load_paths)
        )
        for path, result, chunks in prepared_results:
            if result is not None:
                results[path] = result
            elif chunks is not None:
                prepared_files.append((path, chunks))

        if prepared_files:
            all_chunks = [
                chunk for _, chunks in prepared_files for chunk in chunks
            ]
            logger.info(
                "[batch] Prepared %d files with %d chunks; embedding model=%s",
                len(prepared_files),
                len(all_chunks),
                settings.embed_model,
            )
            try:
                all_vectors = await self.embed_texts(
                    [chunk.content for chunk in all_chunks]
                )
            except Exception as error:
                logger.exception(
                    "Failed to generate embeddings for file batch"
                )
                for path, _ in prepared_files:
                    results[path] = {
                        "status": "failed",
                        "message": str(error),
                        "chunks_added": 0,
                    }
            else:
                offset = 0
                for path, chunks in prepared_files:
                    count = len(chunks)
                    try:
                        added = await store.insert(
                            chunks, all_vectors[offset : offset + count]
                        )
                    except Exception as error:
                        logger.exception("Failed to store '%s'", path.name)
                        results[path] = {
                            "status": "failed",
                            "message": str(error),
                            "chunks_added": 0,
                        }
                    else:
                        results[path] = {
                            "status": "completed",
                            "message": (
                                f"File '{path.name}' ingested with "
                                f"{added} chunks."
                            ),
                            "chunks_added": added,
                        }
                    offset += count

        return [(path, results[path]) for path in paths]


batch_processor = BatchProcessor()
