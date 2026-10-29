import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from app.ai.rag.models import Chunk
from app.core.config import settings
from scripts.chunk_text import chunk_by_tokens
from scripts.parse_openapi import parse_openapi

SUPPORTED_EXTENSIONS = {".pdf", ".json", ".yaml", ".yml", ".jsonl", ".md", ".txt"}
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


class DataProcess:
    def __init__(self, target_tokens: int = 500, overlap_tokens: int = 80):
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens

    async def load_pdf(self, path: str | Path) -> list[dict[str, Any]]:
        from pypdf import PdfReader

        source_path = Path(path)

        def read_pages() -> list[dict[str, Any]]:
            reader = PdfReader(str(source_path))
            documents = []
            for page_number, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    documents.append(
                        {
                            "title": f"{source_path.name} (page {page_number})",
                            "content": text,
                            "metadata": {
                                "source": source_path.name,
                                "path": str(source_path),
                                "page": page_number,
                                "api_name": "",
                                "method": "",
                                "path": str(source_path),
                                "section": "",
                            },
                        }
                    )
            if not documents:
                raise ValueError(f"{source_path}: no extractable text")
            return documents

        return await asyncio.to_thread(read_pages)

    async def load_openapi(self, path: str | Path) -> list[Chunk]:
        source_path = Path(path)
        records = await asyncio.to_thread(parse_openapi, str(source_path))
        if not records:
            raise ValueError(f"{source_path} contains no OpenAPI operations")

        chunks = []
        for record in records:
            content = str(record["chunk_text"])
            metadata = {
                **record,
                "source": source_path.name,
                "file_path": str(source_path),
                "page": "",
            }
            chunk_id = "KB-" + hashlib.sha256(
                f"{source_path.name}:{record['chunk_id']}".encode("utf-8")
            ).hexdigest().upper()
            chunks.append(
                Chunk(
                    id=chunk_id,
                    title=str(record["source_title"]),
                    content=content,
                    metadata=metadata,
                )
            )
        return chunks

    async def load_jsonl(self, path: str | Path) -> list[Chunk]:
        source_path = Path(path)

        def read_records() -> list[Chunk]:
            chunks = []
            with source_path.open("r", encoding="utf-8") as file:
                for line_number, line in enumerate(file, start=1):
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError(
                            f"{source_path}:{line_number} must be a JSON object"
                        )
                    content = record.get(
                        "content", record.get("chunk_text", record.get("text"))
                    )
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError(
                            f"{source_path}:{line_number} has no chunk content"
                        )
                    metadata = record.get("metadata", {})
                    if not isinstance(metadata, dict):
                        raise ValueError(
                            f"{source_path}:{line_number} metadata must be an object"
                        )
                    metadata = self._metadata(
                        {
                            **metadata,
                            **{
                                key: record[key]
                                for key in (
                                    "source_title",
                                    "source_url",
                                    "api_name",
                                    "method",
                                    "path",
                                    "section",
                                    "page",
                                )
                                if key in record and key not in metadata
                            },
                        },
                        source_path,
                    )
                    chunk_id = str(
                        record.get("id")
                        or record.get("chunk_id")
                        or self._chunk_id(
                            source_path.name, line_number, content
                        )
                    )
                    chunks.append(
                        Chunk(
                            id=chunk_id,
                            title=str(record.get("title") or source_path.name),
                            content=content.strip(),
                            metadata=metadata,
                        )
                    )
            return chunks

        return await asyncio.to_thread(read_records)

    async def chunk(self, documents: list[dict[str, Any]]) -> list[Chunk]:
        def make_chunks() -> list[Chunk]:
            chunks = []
            for document_index, document in enumerate(documents):
                content = str(document.get("content") or "")
                if not content.strip():
                    continue
                source_path = Path(
                    str(document.get("metadata", {}).get("path") or "unknown")
                )
                metadata = self._metadata(document.get("metadata", {}), source_path)
                for section_index, (section, section_text) in enumerate(
                    self._sections(content)
                ):
                    for part_index, part in enumerate(
                        chunk_by_tokens(
                            section_text,
                            target=self.target_tokens,
                            overlap=self.overlap_tokens,
                            model_name=settings.embed_model,
                        )
                    ):
                        if not part.strip():
                            continue
                        chunk_metadata = dict(metadata)
                        chunk_metadata["section"] = (
                            section or chunk_metadata["section"]
                        )
                        title = str(document.get("title") or metadata["source"])
                        if section:
                            title = f"{title} - {section}"
                        chunk_id = self._chunk_id(
                            metadata["source"],
                            document_index,
                            section_index,
                            part_index,
                            part,
                        )
                        chunks.append(
                            Chunk(
                                id=chunk_id,
                                title=title,
                                content=part,
                                metadata=chunk_metadata,
                            )
                        )
            return chunks

        return await asyncio.to_thread(make_chunks)

    async def process_folder(
        self, folder: str | Path, source_files: list[str] | None = None
    ) -> list[Chunk]:
        root = Path(folder)
        if not root.is_dir():
            raise FileNotFoundError(f"Upload folder does not exist: {root}")

        selected = set(source_files) if source_files is not None else None
        files = sorted(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and (selected is None or path.name in selected)
        )
        if selected is not None:
            found = {path.name for path in files}
            missing = sorted(selected - found)
            if missing:
                raise FileNotFoundError(
                    "Uploaded file(s) not found: " + ", ".join(missing)
                )

        chunks = []
        for path in files:
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                chunks.extend(await self.chunk(await self.load_pdf(path)))
            elif suffix == ".jsonl":
                chunks.extend(await self.load_jsonl(path))
            elif suffix in {".json", ".yaml", ".yml"}:
                parsed = await asyncio.to_thread(self._read_structured, path)
                if isinstance(parsed, dict) and isinstance(parsed.get("paths"), dict):
                    chunks.extend(await self.load_openapi(path))
                else:
                    text = json.dumps(parsed, ensure_ascii=False, indent=2)
                    chunks.extend(
                        await self.chunk(
                            [
                                {
                                    "title": path.name,
                                    "content": text,
                                    "metadata": self._metadata({}, path),
                                }
                            ]
                        )
                    )
            else:
                text = await asyncio.to_thread(path.read_text, encoding="utf-8")
                chunks.extend(
                    await self.chunk(
                        [
                            {
                                "title": path.name,
                                "content": text,
                                "metadata": self._metadata({}, path),
                            }
                        ]
                    )
                )
        return chunks

    @staticmethod
    def _read_structured(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as file:
            if path.suffix.lower() in {".yaml", ".yml"}:
                return yaml.safe_load(file)
            return json.load(file)

    @staticmethod
    def _metadata(metadata: dict[str, Any], source_path: Path) -> dict[str, Any]:
        result = dict(metadata)
        source = result.get("source") or source_path.name
        document_path = result.get("path") or str(source_path)
        return {
            **result,
            "source": str(source),
            "source_title": str(
                result.get("source_title") or result.get("title") or source
            ),
            "source_url": str(result.get("source_url") or ""),
            "page": result.get("page", ""),
            "api_name": str(result.get("api_name") or ""),
            "method": str(result.get("method") or ""),
            "path": str(document_path),
            "section": str(result.get("section") or ""),
        }

    @staticmethod
    def _sections(text: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        current_lines: list[str] = []
        current_heading = ""
        fence_character = None
        fence_length = 0
        for line in text.splitlines():
            fence = re.match(r"^\s*(`{3,}|~{3,})", line)
            if fence_character is not None:
                current_lines.append(line)
                if (
                    fence
                    and fence.group(1)[0] == fence_character
                    and len(fence.group(1)) >= fence_length
                ):
                    fence_character = None
                    fence_length = 0
                continue
            if fence:
                marker = fence.group(1)
                fence_character = marker[0]
                fence_length = len(marker)
                current_lines.append(line)
                continue

            match = _HEADING.match(line)
            if match:
                if current_lines:
                    sections.append((current_heading, "\n".join(current_lines)))
                current_heading = match.group(2).strip()
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            sections.append((current_heading, "\n".join(current_lines)))
        return sections or [("", text)]

    @staticmethod
    def _chunk_id(*parts: Any) -> str:
        value = "\0".join(str(part) for part in parts)
        return "KB-" + hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


data_process = DataProcess()
