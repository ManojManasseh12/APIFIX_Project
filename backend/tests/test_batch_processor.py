import asyncio

from app.ai.rag.batch_processor import BatchProcessor, BatchSettings
from app.ai.rag.models import Chunk


class FakeEmbedder:
    def __init__(self, fail_attempts: int = 0):
        self.batches: list[list[str]] = []
        self.fail_attempts = fail_attempts
        self.attempts = 0

    async def embed_texts(
        self,
        texts,
        task_type="RETRIEVAL_DOCUMENT",
        *,
        max_attempts=None,
        batch_size=None,
    ):
        self.attempts += 1
        if self.attempts <= self.fail_attempts:
            raise RuntimeError("temporary embedding failure")
        texts = list(texts)
        self.batches.append(texts)
        return [[float(len(text)), 1.0] for text in texts]


class FakeStore:
    def __init__(self):
        self.inserted: list[list[str]] = []

    async def insert(self, chunks, vectors):
        assert len(chunks) == len(vectors)
        self.inserted.append([chunk.id for chunk in chunks])
        return len(chunks)


def make_settings(**overrides):
    values = {
        "max_concurrency": 2,
        "max_chunks_per_embedding_request": 64,
        "max_tokens_per_embedding_request": 8_000,
        "embedding_requests_per_minute": 1000,
        "embedding_tokens_per_minute": 1_000_000,
        "embedding_max_retries": 0,
        "retry_base_delay_seconds": 0.0,
        "retry_max_delay_seconds": 0.0,
    }
    values.update(overrides)
    return BatchSettings(**values)


def loader_for():
    async def load(path):
        return [
            Chunk(
                id=f"{path.name}-1",
                title=path.name,
                content=path.read_text(encoding="utf-8"),
                metadata={"source": path.name},
            )
        ]

    return load


def test_embeds_chunks_in_count_bounded_batches(tmp_path):
    paths = []
    for index in range(3):
        path = tmp_path / f"doc{index}.txt"
        path.write_text(f"text {index}", encoding="utf-8")
        paths.append(path)
    embedder = FakeEmbedder()
    store = FakeStore()
    processor = BatchProcessor(
        make_settings(max_chunks_per_embedding_request=2),
        embedder=embedder,
    )

    results = asyncio.run(
        processor.process_files(paths, loader_for(), store)
    )

    assert [len(batch) for batch in embedder.batches] == [2, 1]
    assert [result["status"] for _, result in results] == ["completed"] * 3
    assert sum(len(ids) for ids in store.inserted) == 3


def test_batches_respect_token_limit(tmp_path):
    path = tmp_path / "long.txt"
    # 40 ASCII characters ~= 10 estimated tokens.
    path.write_text("x" * 40, encoding="utf-8")
    embedder = FakeEmbedder()
    processor = BatchProcessor(
        make_settings(max_tokens_per_embedding_request=10),
        embedder=embedder,
    )

    asyncio.run(
        processor.process_files([path], loader_for(), FakeStore())
    )

    assert embedder.batches == [["x" * 40]]


def test_duplicate_files_are_skipped_within_a_batch(tmp_path):
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("identical content", encoding="utf-8")
    second.write_text("identical content", encoding="utf-8")
    embedder = FakeEmbedder()
    store = FakeStore()
    processor = BatchProcessor(make_settings(), embedder=embedder)

    results = asyncio.run(
        processor.process_files(
            [first, second], loader_for(), store
        )
    )

    statuses = {path.name: result["status"] for path, result in results}
    assert statuses == {"a.txt": "completed", "b.txt": "skipped"}
    assert len(embedder.batches) == 1
    assert len(store.inserted) == 1


def test_embedding_retries_before_failing_a_file(tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("retry me", encoding="utf-8")
    embedder = FakeEmbedder(fail_attempts=2)
    store = FakeStore()
    processor = BatchProcessor(
        make_settings(
            embedding_max_retries=2,
            retry_base_delay_seconds=0.0,
            retry_max_delay_seconds=0.0,
        ),
        embedder=embedder,
    )

    results = asyncio.run(
        processor.process_files([path], loader_for(), store)
    )

    assert embedder.attempts == 3
    assert results[0][1]["status"] == "completed"


def test_embedding_failure_marks_every_prepared_file_failed(tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("will fail", encoding="utf-8")
    embedder = FakeEmbedder(fail_attempts=1)
    store = FakeStore()
    processor = BatchProcessor(
        make_settings(embedding_max_retries=0), embedder=embedder
    )

    results = asyncio.run(
        processor.process_files([path], loader_for(), store)
    )

    assert results[0][1]["status"] == "failed"
    assert store.inserted == []
