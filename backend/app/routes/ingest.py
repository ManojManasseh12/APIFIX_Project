from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ai.rag.pipeline import ensure_seeded, pipeline
from app.core.config import settings
from app.routes.upload import validate_upload_name

router = APIRouter(tags=["knowledge-base"])


class IngestRequest(BaseModel):
    files: list[str] | None = Field(default=None, min_length=1)


@router.post("/ingest")
async def ingest_files(payload: IngestRequest | None = None):
    # Seed first so the bundled knowledge base and user uploads coexist in
    # Chroma instead of the first upload replacing the seed corpus.
    await ensure_seeded()
    source_files = None
    if payload is not None and payload.files is not None:
        source_files = [validate_upload_name(name) for name in payload.files]
        for name in source_files:
            if not (settings.upload_dir / name).is_file():
                raise HTTPException(status_code=404, detail=f"File not found: {name}")
    try:
        return await pipeline.ingest(
            Path(settings.upload_dir), source_files=source_files
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/kb/stats")
async def knowledge_base_stats():
    return await ensure_seeded()
