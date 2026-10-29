from typing import Any

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    id: str
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    chunk_id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float
