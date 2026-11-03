import asyncio
import csv
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

from app.ai.rag.models import Chunk
from app.core.config import settings
from scripts.chunk_text import chunk_by_tokens
from scripts.parse_openapi import parse_openapi

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".json",
    ".yaml",
    ".yml",
    ".jsonl",
    ".md",
    ".txt",
    ".html",
    ".htm",
    ".xml",
    ".csv",
    ".tsv",
    ".docx",
    ".xlsx",
    ".pptx",
}
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


class _HTMLTextExtractor(HTMLParser):
    """Extract readable text from HTML without pulling in a parser dependency."""

    _SKIP = {"script", "style", "noscript", "nav", "footer", "svg"}
    _BLOCK = {
        "p",
        "div",
        "section",
        "article",
        "main",
        "ul",
        "ol",
        "table",
        "tr",
        "br",
        "hr",
        "pre",
        "blockquote",
        "li",
    }
    _HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._title_parts: list[str] = []
        self._skip_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._title_depth += 1
        elif tag in self._HEADINGS:
            self._parts.append("\n\n" + "#" * self._HEADINGS[tag] + " ")
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag in {"td", "th"}:
            self._parts.append(" | ")
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._title_depth = max(0, self._title_depth - 1)
        elif tag in self._HEADINGS or tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._title_depth:
            self._title_parts.append(data)
        else:
            self._parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(self._title_parts).strip()

    def text(self) -> str:
        lines = []
        for line in "".join(self._parts).splitlines():
            cleaned = re.sub(r"[ \t]+", " ", line).strip()
            if cleaned:
                lines.append(cleaned)
            elif lines and lines[-1]:
                lines.append("")
        return "\n".join(lines).strip()


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

    async def load_docx(self, path: str | Path) -> list[Chunk]:
        from docx import Document

        source_path = Path(path)

        def read_document() -> list[dict[str, Any]]:
            document = Document(str(source_path))
            blocks: list[str] = []
            for paragraph in document.paragraphs:
                text = paragraph.text.strip()
                if not text:
                    continue
                style = getattr(paragraph.style, "name", "") or ""
                if style.lower().startswith("heading"):
                    level = next((int(ch) for ch in style if ch.isdigit()), 2)
                    blocks.append(f"{'#' * min(level, 6)} {text}")
                else:
                    blocks.append(text)
            for table in document.tables:
                rows = []
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    blocks.append("\n".join(rows))
            content = "\n\n".join(blocks).strip()
            if not content:
                raise ValueError(f"{source_path}: no extractable text")
            return [
                {
                    "title": source_path.name,
                    "content": content,
                    "metadata": self._metadata({}, source_path),
                }
            ]

        return await self.chunk(await asyncio.to_thread(read_document))

    async def load_xlsx(self, path: str | Path) -> list[Chunk]:
        from openpyxl import load_workbook

        source_path = Path(path)

        def read_workbook() -> list[dict[str, Any]]:
            workbook = load_workbook(
                str(source_path), read_only=True, data_only=True
            )
            documents = []
            try:
                for worksheet in workbook.worksheets:
                    rows = []
                    for row in worksheet.iter_rows(values_only=True):
                        values = [
                            str(value).strip() if value is not None else ""
                            for value in row
                        ]
                        if any(values):
                            rows.append(" | ".join(values))
                    if not rows:
                        continue
                    documents.append(
                        {
                            "title": f"{source_path.name} - {worksheet.title}",
                            "content": f"# {worksheet.title}\n\n" + "\n".join(rows),
                            "metadata": self._metadata(
                                {"section": worksheet.title}, source_path
                            ),
                        }
                    )
            finally:
                workbook.close()
            if not documents:
                raise ValueError(f"{source_path}: no extractable rows")
            return documents

        return await self.chunk(await asyncio.to_thread(read_workbook))

    async def load_pptx(self, path: str | Path) -> list[Chunk]:
        from pptx import Presentation

        source_path = Path(path)

        def shape_parts(shapes) -> list[str]:
            parts: list[str] = []
            for shape in shapes:
                nested = getattr(shape, "shapes", None)
                if nested is not None:
                    parts.extend(shape_parts(nested))
                    continue
                if getattr(shape, "has_text_frame", False):
                    text = shape.text_frame.text.strip()
                    if text:
                        parts.append(text)
                if getattr(shape, "has_table", False):
                    rows = []
                    for row in shape.table.rows:
                        cells = [cell.text.strip() for cell in row.cells]
                        if any(cells):
                            rows.append(" | ".join(cells))
                    if rows:
                        parts.append("\n".join(rows))
            return parts

        def read_presentation() -> list[dict[str, Any]]:
            presentation = Presentation(str(source_path))
            documents = []
            for slide_number, slide in enumerate(presentation.slides, start=1):
                parts = shape_parts(slide.shapes)
                if slide.has_notes_slide:
                    notes = slide.notes_slide.notes_text_frame.text.strip()
                    if notes:
                        parts.append(f"Notes: {notes}")
                if not parts:
                    continue
                documents.append(
                    {
                        "title": f"{source_path.name} (slide {slide_number})",
                        "content": "\n\n".join(parts),
                        "metadata": self._metadata(
                            {"page": slide_number, "section": f"Slide {slide_number}"},
                            source_path,
                        ),
                    }
                )
            if not documents:
                raise ValueError(f"{source_path}: no extractable text")
            return documents

        return await self.chunk(await asyncio.to_thread(read_presentation))

    async def load_html(self, path: str | Path) -> list[Chunk]:
        source_path = Path(path)

        def read_html() -> list[dict[str, Any]]:
            parser = _HTMLTextExtractor()
            parser.feed(
                source_path.read_text(encoding="utf-8", errors="replace")
            )
            parser.close()
            content = parser.text()
            if not content:
                raise ValueError(f"{source_path}: no extractable text")
            title = parser.title or source_path.name
            if not content.lstrip().startswith("#"):
                content = f"# {title}\n\n{content}"
            return [
                {
                    "title": title,
                    "content": content,
                    "metadata": self._metadata({"source_title": title}, source_path),
                }
            ]

        return await self.chunk(await asyncio.to_thread(read_html))

    async def load_xml(self, path: str | Path) -> list[Chunk]:
        import xml.etree.ElementTree as ElementTree

        source_path = Path(path)

        def read_xml() -> list[dict[str, Any]]:
            raw = source_path.read_text(encoding="utf-8", errors="replace")
            if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
                raise ValueError(
                    f"{source_path}: DTDs and entities are not supported"
                )
            root = ElementTree.fromstring(raw)
            lines: list[str] = []

            def walk(element, trail: list[str]) -> None:
                tag = (
                    element.tag.split("}")[-1]
                    if isinstance(element.tag, str)
                    else "node"
                )
                label = "/".join(trail + [tag])
                if element.attrib:
                    attributes = " ".join(
                        f"{key}={value}" for key, value in element.attrib.items()
                    )
                    lines.append(f"{label} attributes: {attributes}")
                text = (element.text or "").strip()
                if text:
                    lines.append(f"{label}: {text}")
                for child in element:
                    walk(child, trail + [tag])

            walk(root, [])
            content = "\n".join(lines).strip()
            if not content:
                raise ValueError(f"{source_path}: no extractable text")
            return [
                {
                    "title": source_path.name,
                    "content": content,
                    "metadata": self._metadata({}, source_path),
                }
            ]

        return await self.chunk(await asyncio.to_thread(read_xml))

    async def load_csv(self, path: str | Path) -> list[Chunk]:
        source_path = Path(path)
        delimiter = "\t" if source_path.suffix.lower() == ".tsv" else ","

        def read_csv() -> list[dict[str, Any]]:
            rows = []
            with source_path.open(
                "r", encoding="utf-8-sig", newline=""
            ) as file:
                for row in csv.reader(file, delimiter=delimiter):
                    values = [value.strip() for value in row]
                    if any(values):
                        rows.append(" | ".join(values))
            content = "\n".join(rows).strip()
            if not content:
                raise ValueError(f"{source_path}: no extractable rows")
            return [
                {
                    "title": source_path.name,
                    "content": content,
                    "metadata": self._metadata({}, source_path),
                }
            ]

        return await self.chunk(await asyncio.to_thread(read_csv))

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
                                    "source_type",
                                    "source_title",
                                    "source_url",
                                    "api_name",
                                    "api_version",
                                    "method",
                                    "path",
                                    "section",
                                    "page",
                                    "license",
                                    "retrieved_at",
                                    "token_count",
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
                            model_name=settings.chunk_tokenizer_model,
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

    def select_files(
        self, folder: str | Path, source_files: list[str] | None = None
    ) -> list[Path]:
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
        return files

    async def load_file(self, path: str | Path) -> list[Chunk]:
        """Load and chunk one supported file."""
        source_path = Path(path)
        suffix = source_path.suffix.lower()
        if suffix == ".pdf":
            return await self.chunk(await self.load_pdf(source_path))
        if suffix == ".docx":
            return await self.load_docx(source_path)
        if suffix == ".xlsx":
            return await self.load_xlsx(source_path)
        if suffix == ".pptx":
            return await self.load_pptx(source_path)
        if suffix in {".html", ".htm"}:
            return await self.load_html(source_path)
        if suffix == ".xml":
            return await self.load_xml(source_path)
        if suffix in {".csv", ".tsv"}:
            return await self.load_csv(source_path)
        if suffix == ".jsonl":
            return await self.load_jsonl(source_path)
        if suffix in {".json", ".yaml", ".yml"}:
            parsed = await asyncio.to_thread(self._read_structured, source_path)
            if isinstance(parsed, dict) and isinstance(parsed.get("paths"), dict):
                return await self.load_openapi(source_path)
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
            return await self.chunk(
                [
                    {
                        "title": source_path.name,
                        "content": text,
                        "metadata": self._metadata({}, source_path),
                    }
                ]
            )

        text = await asyncio.to_thread(
            source_path.read_text, encoding="utf-8"
        )
        return await self.chunk(
            [
                {
                    "title": source_path.name,
                    "content": text,
                    "metadata": self._metadata({}, source_path),
                }
            ]
        )

    async def process_folder(
        self, folder: str | Path, source_files: list[str] | None = None
    ) -> list[Chunk]:
        files = self.select_files(folder, source_files)
        chunks = []
        for path in files:
            chunks.extend(await self.load_file(path))
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
