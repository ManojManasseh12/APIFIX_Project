import html
import re
import sys
from pathlib import Path


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    codes: list[str] = []

    def keep_code(match):
        codes.append(f"<code>{match.group(1)}</code>")
        return f"\u0000CODE{len(codes)-1}\u0000"

    text = re.sub(r"`([^`]+)`", keep_code, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    for index, code in enumerate(codes):
        text = text.replace(f"\u0000CODE{index}\u0000", code)
    return text


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def convert(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    in_code = False
    code: list[str] = []
    table: list[list[str]] = []
    list_items: list[str] = []
    list_kind: str | None = None

    def flush_paragraph():
        nonlocal paragraph
        if paragraph:
            out.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph = []

    def flush_table():
        nonlocal table
        if not table:
            return
        header = table[0]
        body = table[1:]
        widths = {2: [28, 72], 3: [24, 38, 38], 4: [16, 22, 30, 32], 5: [13, 10, 19, 38, 20]}.get(len(header), [])
        out.append(f'<table class="cols-{len(header)}">')
        cells = []
        for index, cell in enumerate(header):
            width = widths[index] if index < len(widths) else round(100 / len(header))
            cells.append(f'<th style="width:{width}%">{inline(cell)}</th>')
        out.append("<thead><tr>" + "".join(cells) + "</tr></thead>")
        out.append("<tbody>")
        for row in body:
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
        out.append("</tbody></table>")
        table = []

    def flush_list():
        nonlocal list_items, list_kind
        if list_items:
            tag = "ol" if list_kind == "ol" else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(item)}</li>" for item in list_items) + f"</{tag}>")
            list_items = []
            list_kind = None

    for raw in lines:
        line = raw.rstrip("\n")

        if line.strip().startswith("```"):
            flush_paragraph()
            flush_table()
            flush_list()
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = []
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code.append(line)
            continue

        if not line.strip():
            flush_paragraph()
            flush_table()
            flush_list()
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_paragraph()
            flush_table()
            flush_list()
            level = len(heading.group(1))
            text = heading.group(2)
            out.append(f'<h{level} id="{slug(text)}">{inline(text)}</h{level}>')
            continue

        if re.match(r"^\s*---+\s*$", line):
            flush_paragraph()
            flush_table()
            flush_list()
            out.append("<hr/>")
            continue

        if line.lstrip().startswith("|"):
            flush_paragraph()
            flush_list()
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(re.match(r"^:?-{2,}:?$", cell) for cell in cells if cell):
                continue
            table.append(cells)
            continue

        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        ordered = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if bullet or ordered:
            flush_paragraph()
            flush_table()
            kind = "ol" if ordered else "ul"
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            list_items.append((ordered or bullet).group(1))
            continue

        if line.startswith(">"):
            flush_paragraph()
            flush_table()
            flush_list()
            out.append(f"<blockquote>{inline(line.lstrip('> '))}</blockquote>")
            continue

        flush_table()
        flush_list()
        paragraph.append(line.strip())

    flush_paragraph()
    flush_table()
    flush_list()
    if in_code and code:
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    return "\n".join(out)


def wrap(body: str, title: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/>
<title>{html.escape(title)}</title>
<style>
@page {{ size: A4; margin: 15mm 14mm 16mm 14mm; }}
body {{ font-family: "DejaVu Sans", Arial, sans-serif; font-size: 9.4pt; line-height: 1.42; color: #1f2937; }}
h1 {{ font-size: 19pt; color: #0f172a; border-bottom: 3px solid #10b981; padding-bottom: 6px; margin-top: 0; }}
h2 {{ font-size: 14pt; color: #0f172a; border-bottom: 1px solid #cbd5e1; padding-bottom: 3px; margin-top: 18px; page-break-after: avoid; }}
h3 {{ font-size: 11.5pt; color: #065f46; margin-top: 14px; page-break-after: avoid; }}
h4 {{ font-size: 10.2pt; color: #111827; margin-top: 12px; page-break-after: avoid; }}
p {{ margin: 5px 0; }}
ul, ol {{ margin: 5px 0 8px 18px; padding: 0; }}
li {{ margin: 2px 0; }}
code {{ font-family: "DejaVu Sans Mono", monospace; font-size: 8.2pt; background: #f1f5f9; padding: 1px 3px; border-radius: 3px; }}
pre {{ background: #f8fafc; color: #0f172a; border: 1px solid #cbd5e1; padding: 8px 10px; border-radius: 5px; white-space: pre-wrap; word-wrap: break-word; page-break-inside: avoid; }}
pre code {{ background: transparent; color: #0f172a; padding: 0; font-size: 7.8pt; }}
table {{ width: 100%; border-collapse: collapse; margin: 8px 0 12px 0; table-layout: fixed; font-size: 8.1pt; page-break-inside: auto; }}
th, td {{ border: 1px solid #cbd5e1; padding: 4px 5px; vertical-align: top; word-wrap: break-word; overflow-wrap: break-word; }}
th {{ background: #e2e8f0; text-align: left; color: #0f172a; word-break: keep-all; overflow-wrap: normal; }}
table.cols-2 th:nth-child(1), table.cols-2 td:nth-child(1) {{ width: 28%; }}
table.cols-3 th:nth-child(1), table.cols-3 td:nth-child(1) {{ width: 24%; }}
table.cols-3 th:nth-child(2), table.cols-3 td:nth-child(2) {{ width: 38%; }}
table.cols-4 th:nth-child(1), table.cols-4 td:nth-child(1) {{ width: 16%; }}
table.cols-4 th:nth-child(2), table.cols-4 td:nth-child(2) {{ width: 22%; }}
table.cols-5 th:nth-child(1), table.cols-5 td:nth-child(1) {{ width: 13%; }}
table.cols-5 th:nth-child(2), table.cols-5 td:nth-child(2) {{ width: 10%; }}
table.cols-5 th:nth-child(3), table.cols-5 td:nth-child(3) {{ width: 19%; }}
table.cols-5 th:nth-child(4), table.cols-5 td:nth-child(4) {{ width: 38%; }}
table.cols-5 th:nth-child(5), table.cols-5 td:nth-child(5) {{ width: 20%; }}
tr:nth-child(even) td {{ background: #f8fafc; }}
blockquote {{ border-left: 3px solid #10b981; margin: 8px 0; padding: 4px 10px; background: #f0fdf4; }}
hr {{ border: 0; border-top: 1px solid #cbd5e1; margin: 12px 0; }}
a {{ color: #047857; text-decoration: none; }}
strong {{ color: #0f172a; }}
</style></head><body>
{body}
</body></html>"""


def main():
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    body = convert(source.read_text(encoding="utf-8"))
    target.write_text(wrap(body, source.stem), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
