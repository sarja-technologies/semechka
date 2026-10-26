"""Real document generation with maintained libraries (python-docx, openpyxl, python-pptx). Pure functions: a validated
structure in, file bytes out. Nothing here touches storage or permissions; the caller saves the bytes as a new file
or file version."""
from __future__ import annotations

import io

MAX_BLOCKS = 500
MAX_CELLS = 200_000
MAX_SLIDES = 100


class DocError(ValueError):
    """The requested structure is invalid; the message is written for the model to correct itself."""


def _s(v, limit=20000) -> str:
    return "" if v is None else str(v)[:limit]


def make_docx(title: str, blocks: list[dict]) -> bytes:
    from docx import Document
    if not isinstance(blocks, list) or not blocks:
        raise DocError("blocks must be a non-empty list of {type, ...} objects.")
    if len(blocks) > MAX_BLOCKS:
        raise DocError(f"At most {MAX_BLOCKS} blocks.")
    d = Document()
    if title:
        d.add_heading(_s(title, 200), 0)
    for i, b in enumerate(blocks, 1):
        if not isinstance(b, dict) or "type" not in b:
            raise DocError(f"Block {i} must be an object with a 'type'.")
        t = b["type"]
        if t == "heading":
            d.add_heading(_s(b.get("text"), 300), max(1, min(int(b.get("level", 1)), 4)))
        elif t == "paragraph":
            d.add_paragraph(_s(b.get("text")))
        elif t == "bullets":
            for item in b.get("items") or []:
                d.add_paragraph(_s(item, 2000), style="List Bullet")
        elif t == "numbered":
            for item in b.get("items") or []:
                d.add_paragraph(_s(item, 2000), style="List Number")
        elif t == "table":
            rows = b.get("rows") or []
            if not rows or not all(isinstance(r, list) for r in rows):
                raise DocError(f"Block {i}: a table needs 'rows' as a list of lists.")
            width = max(len(r) for r in rows)
            if width * len(rows) > 5000:
                raise DocError(f"Block {i}: table too large.")
            tbl = d.add_table(rows=len(rows), cols=width)
            tbl.style = "Table Grid"
            for ri, r in enumerate(rows):
                for ci, v in enumerate(r):
                    tbl.cell(ri, ci).text = _s(v, 1000)
        elif t == "source_list":
            d.add_heading("Sources", 2)
            for item in b.get("items") or []:
                d.add_paragraph(_s(item, 500), style="List Bullet")
        else:
            raise DocError(f"Block {i}: unknown type '{t}'. Use heading, paragraph, bullets, numbered, table or source_list.")
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def make_xlsx(sheets: dict) -> bytes:
    from openpyxl import Workbook
    if not isinstance(sheets, dict) or not sheets:
        raise DocError("sheets must be an object mapping sheet name to a list of rows.")
    wb = Workbook()
    wb.remove(wb.active)
    cells = 0
    for name, rows in sheets.items():
        if not isinstance(rows, list):
            raise DocError(f"Sheet '{name}': rows must be a list of lists.")
        ws = wb.create_sheet(_s(name, 31).replace("/", "-").replace("\\", "-").replace("*", "").replace("?", "")
                             .replace("[", "(").replace("]", ")").replace(":", "-") or "Sheet")
        for r in rows:
            if not isinstance(r, list):
                raise DocError(f"Sheet '{name}': every row must be a list.")
            cells += len(r)
            if cells > MAX_CELLS:
                raise DocError("Too many cells.")
            ws.append([v if isinstance(v, (int, float, bool)) or v is None else _s(v, 2000) for v in r])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def make_pptx(title: str, slides: list[dict]) -> bytes:
    from pptx import Presentation
    if not isinstance(slides, list) or not slides:
        raise DocError("slides must be a non-empty list of {title, bullets, notes} objects.")
    if len(slides) > MAX_SLIDES:
        raise DocError(f"At most {MAX_SLIDES} slides.")
    p = Presentation()
    if title:
        s0 = p.slides.add_slide(p.slide_layouts[0])
        s0.shapes.title.text = _s(title, 200)
    for i, sl in enumerate(slides, 1):
        if not isinstance(sl, dict):
            raise DocError(f"Slide {i} must be an object.")
        s = p.slides.add_slide(p.slide_layouts[1])
        s.shapes.title.text = _s(sl.get("title"), 200)
        body = s.placeholders[1].text_frame
        body.clear()
        for j, item in enumerate(sl.get("bullets") or []):
            para = body.paragraphs[0] if j == 0 else body.add_paragraph()
            para.text = _s(item, 500)
        if sl.get("notes"):
            s.notes_slide.notes_text_frame.text = _s(sl["notes"], 4000)
    out = io.BytesIO()
    p.save(out)
    return out.getvalue()
