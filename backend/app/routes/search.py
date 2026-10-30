from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.ai.rag.pipeline import ensure_seeded, pipeline

router = APIRouter(tags=["knowledge-base"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["dense", "keyword", "hybrid"] = "hybrid"
    k: int = Field(default=5, ge=1, le=50)


@router.post("/search")
async def search(payload: SearchRequest):
    await ensure_seeded()
    hits = await pipeline.retrieve(
        payload.query, mode=payload.mode, k=payload.k
    )
    return {
        "hits": [
            {
                "chunk_id": hit.chunk_id,
                "source": hit.metadata.get("source"),
                "content": hit.content,
                "metadata": hit.metadata,
                "score": hit.score,
            }
            for hit in hits
        ]
    }
