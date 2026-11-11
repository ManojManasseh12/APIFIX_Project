import asyncio

import pytest

from app.ai.rag.data_loader import SUPPORTED_EXTENSIONS, DataProcess


class FakeTokenizer:
    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        return text.split()

    def decode(self, tokens, skip_special_tokens: bool = True) -> str:
        return " ".join(tokens)


class FakeModel:
    tokenizer = FakeTokenizer()


@pytest.fixture(autouse=True)
def fake_tokenizer(monkeypatch):
    from scripts import chunk_text

    monkeypatch.setattr(
        chunk_text, "get_model", lambda model_name=None: FakeModel()
    )


def load(path):
    return asyncio.run(DataProcess().load_file(path))


def joined(chunks) -> str:
    return "\n".join(chunk.content for chunk in chunks)


def test_supported_extensions_include_office_and_web_formats():
    assert {
        ".docx",
        ".xlsx",
        ".pptx",
        ".html",
        ".htm",
        ".xml",
        ".csv",
        ".tsv",
    } <= SUPPORTED_EXTENSIONS


def test_docx_ingestion_reads_paragraphs_tables_and_headings(tmp_path):
    from docx import Document

    path = tmp_path / "auth.docx"
    document = Document()
    document.add_heading("401 Unauthorized", level=1)
    document.add_paragraph("Missing Authorization header.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Status"
    table.cell(0, 1).text = "Meaning"
    table.cell(1, 0).text = "401"
    table.cell(1, 1).text = "Unauthorized"
    document.save(path)

    chunks = load(path)
    text = joined(chunks)

    assert "401 Unauthorized" in text
    assert "Missing Authorization header." in text
    assert "Status | Meaning" in text
    assert "401 | Unauthorized" in text
    assert all(chunk.metadata["source"] == "auth.docx" for chunk in chunks)


def test_xlsx_ingestion_reads_each_sheet(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "statuses.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Statuses"
    sheet.append(["code", "meaning"])
    sheet.append([401, "Unauthorized"])
    workbook.save(path)

    chunks = load(path)
    text = joined(chunks)

    assert "Statuses" in text
    assert "code | meaning" in text
    assert "401 | Unauthorized" in text
    assert all(chunk.metadata["source"] == "statuses.xlsx" for chunk in chunks)


def test_pptx_ingestion_reads_slides_and_text(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    path = tmp_path / "auth.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "401 Unauthorized"
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    box.text_frame.text = "Missing credentials."
    presentation.save(path)

    chunks = load(path)
    text = joined(chunks)

    assert "401 Unauthorized" in text
    assert "Missing credentials." in text
    assert all(chunk.metadata["source"] == "auth.pptx" for chunk in chunks)


def test_html_ingestion_extracts_text_and_skips_noise(tmp_path):
    path = tmp_path / "auth.html"
    path.write_text(
        """
        <html>
          <head>
            <title>Auth docs</title>
            <style>.hidden { display: none; }</style>
          </head>
          <body>
            <nav>menu</nav>
            <h1>401 Unauthorized</h1>
            <p>Missing Authorization header.</p>
            <script>secret()</script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    chunks = load(path)
    text = joined(chunks)

    assert "401 Unauthorized" in text
    assert "Missing Authorization header." in text
    assert "menu" not in text
    assert "secret()" not in text
    assert chunks[0].title.startswith("Auth docs")


def test_xml_ingestion_flattens_tags_and_attributes(tmp_path):
    path = tmp_path / "api.xml"
    path.write_text(
        """
        <api>
          <endpoint method="GET">
            <path>/v1/profile</path>
            <error code="401">Missing credentials</error>
          </endpoint>
        </api>
        """,
        encoding="utf-8",
    )

    text = joined(load(path))

    assert "/v1/profile" in text
    assert "method=GET" in text
    assert "code=401" in text
    assert "Missing credentials" in text


def test_xml_rejects_dtd_and_entities(tmp_path):
    path = tmp_path / "unsafe.xml"
    path.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE api [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        '<api><name>&xxe;</name></api>\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="DTDs and entities are not supported"):
        load(path)


@pytest.mark.parametrize(
    ("suffix", "delimiter"), [(".csv", ","), (".tsv", "\t")]
)
def test_csv_and_tsv_ingestion(tmp_path, suffix, delimiter):
    path = tmp_path / f"statuses{suffix}"
    path.write_text(
        f"code{delimiter}meaning\n401{delimiter}Unauthorized\n",
        encoding="utf-8",
    )

    text = joined(load(path))

    assert "code | meaning" in text
    assert "401 | Unauthorized" in text
