import asyncio
from typing import Sequence

from app.ai.rag.models import Chunk
from app.core.config import settings
from scripts.chunk_text import get_model


class Embedding:
    def __init__(self, batch_size: int = 64):
        self.batch_size = batch_size

    @staticmethod
    def _load_model():
        model = get_model(settings.embed_model)
        model.max_seq_length = 512
        return model

    async def embed_chunks(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        if not chunks:
            return []
        texts = [chunk.content for chunk in chunks]

        def encode() -> list[list[float]]:
            model = self._load_model()
            vectors = []
            for start in range(0, len(texts), self.batch_size):
                batch = model.encode(
                    texts[start : start + self.batch_size],
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                vectors.extend(batch.tolist())
            return vectors

        return await asyncio.to_thread(encode)

    async def embed_prompt(self, text: str) -> list[float]:
        def encode() -> list[float]:
            model = self._load_model()
            vector = model.encode(
                [text], normalize_embeddings=True, show_progress_bar=False
            )[0]
            return vector.tolist()

        return await asyncio.to_thread(encode)


embedding = Embedding()
