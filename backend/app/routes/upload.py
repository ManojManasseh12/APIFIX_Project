import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.ai.rag.data_loader import SUPPORTED_EXTENSIONS
from app.ai.rag.vector_store import vector_store
from app.core.config import settings

router = APIRouter(tags=["knowledge-base"])


def validate_upload_name(filename: str | None) -> str:
    if (
        not filename
        or "\x00" in filename
        or "/" in filename
        or "\\" in filename
        or Path(filename).name != filename
        or filename in {".", ".."}
    ):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file extension")
    return filename


@router.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    names = [validate_upload_name(upload.filename) for upload in files]
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    saved: list[dict[str, str | int]] = []
    paths: list[Path] = []
    max_bytes = settings.max_upload_mb * 1024 * 1024
    try:
        for upload, name in zip(files, names):
            original = Path(name)
            destination = settings.upload_dir / (
                f"{original.stem}-{uuid.uuid4().hex[:8]}{original.suffix.lower()}"
            )
            size = 0
            with destination.open("xb") as output:
                paths.append(destination)
                while contents := await upload.read(64 * 1024):
                    size += len(contents)
                    if size > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"{name} exceeds the {settings.max_upload_mb} MB limit",
                        )
                    output.write(contents)
            saved.append({"name": destination.name, "size": size})
    except HTTPException:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    except OSError:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    finally:
        for upload in files:
            await upload.close()

    return {"files": saved}


@router.get("/files")
async def list_files():
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    files = [
        {"name": path.name, "size": path.stat().st_size}
        for path in sorted(settings.upload_dir.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return {"files": files}


@router.delete("/files/{name}")
async def delete_file(name: str):
    safe_name = validate_upload_name(name)
    path = settings.upload_dir / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    chunks_deleted = await vector_store.delete_source(safe_name)
    path.unlink()
    return {"deleted": safe_name, "chunks_deleted": chunks_deleted}
