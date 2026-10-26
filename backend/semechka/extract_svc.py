"""Text extraction from uploaded documents. Pure functions: bytes in, (location, text) segments out.

Office files are zip archives and may be hostile, so they are inspected before any parser sees them: entry count,
declared and compressed sizes, ratio, names. Nothing is ever extracted to disk. Unsupported or unreadable files report
an honest reason instead of empty text.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass, field

EXTRACTOR_VERSION = 1  # bump to make the sweeper re-ingest everything
MAX_TEXT_CHARS = 5_000_000
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 300 * 1024 * 1024
MAX_ZIP_RATIO = 150
MAX_SHEET_ROWS = 50_000
MAX_SHEET_CELLS = 1_000_000


class Unsupported(Exception):
    """The file is fine but this release cannot read text from it (images without OCR, video, archives...)."""


class Unreadable(Exception):
    """The file claims a type it is not, is encrypted, or is damaged."""


@dataclass
class Extracted:
    segments: list[tuple[str, str]]  # (location label for citations, text)
    extractor: str
    meta: dict = field(default_factory=dict)

    @property
    def n_chars(self) -> int:
        return sum(len(t) for _, t in self.segments)


def check_zip(data: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise Unreadable("This is not a valid Office file (damaged or not a zip container).") from None
    infos = z.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise Unreadable("This file contains too many parts to process safely.")
    total = 0
    for i in infos:
        if i.filename.startswith(("/", "\\")) or ".." in i.filename.replace("\\", "/").split("/"):
            raise Unreadable("This file contains unsafe internal paths.")
        total += i.file_size
        if i.compress_size and i.file_size / max(i.compress_size, 1) > MAX_ZIP_RATIO and i.file_size > 1_000_000:
            raise Unreadable("This file expands suspiciously much when opened and was refused.")
    if total > MAX_ZIP_UNCOMPRESSED:
        raise Unreadable("This file is too large once opened to process safely.")
    if any(i.flag_bits & 0x1 for i in infos):
        raise Unreadable("This file is password-protected.")
    return z


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return data.decode("latin-1")


def _rows_to_segments(label: str, rows: list[list[str]], per: int = 25) -> list[tuple[str, str]]:
    """Group table rows into passages that repeat the header, so a passage is understandable on its own."""
    if not rows:
        return []
    head, body = rows[0], rows[1:] or []
    out = []
    for i in range(0, max(len(body), 1), per):
        part = body[i:i + per]
        lines = [" | ".join(head)] + [" | ".join(r) for r in part]
        lo, hi = i + 2, i + 1 + len(part)  # 1-based spreadsheet row numbers (header is row 1)
        out.append((f"{label} rows {lo}-{hi}" if part else label, "\n".join(lines)))
    return out


def extract_text(data: bytes) -> Extracted:
    return Extracted([("", _decode(data)[:MAX_TEXT_CHARS])], "text")


def extract_csv(data: bytes, delimiter: str = ",") -> Extracted:
    text = _decode(data)
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in r)]
    if not rows:
        raise Unreadable("This file has no rows.")
    return Extracted(_rows_to_segments("rows", rows[:MAX_SHEET_ROWS]), "csv", {"rows": len(rows)})


def extract_json(data: bytes) -> Extracted:
    text = _decode(data)
    try:
        pretty = json.dumps(json.loads(text), indent=1, ensure_ascii=False)
    except ValueError:
        pretty = text  # JSON Lines and near-JSON stay searchable as text
    return Extracted([("", pretty[:MAX_TEXT_CHARS])], "json")


def extract_pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    try:
        r = PdfReader(io.BytesIO(data))
        if r.is_encrypted:
            raise Unreadable("This PDF is password-protected.")
        pages = [(f"p. {i}", (p.extract_text() or "").strip()) for i, p in enumerate(r.pages, 1)]
    except Unreadable:
        raise
    except (PyPdfError, ValueError, KeyError, OSError, RecursionError):
        raise Unreadable("This PDF could not be read.") from None
    segs = [s for s in pages if s[1]]
    if not segs:
        raise Unsupported("This PDF has no text layer (a scan). OCR is not available in this release.")
    return Extracted(segs, "pdf", {"pages": len(pages)})


def extract_docx(data: bytes) -> Extracted:
    check_zip(data)
    from docx import Document
    try:
        d = Document(io.BytesIO(data))
    except Exception:  # noqa: BLE001  parsers raise many types for damaged packages
        raise Unreadable("This Word document could not be read.") from None
    segs, cur_loc, buf = [], "start", []

    def flush():
        if buf:
            segs.append((cur_loc, "\n".join(buf)))
            buf.clear()

    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        if (p.style.name or "").lower().startswith(("heading", "title")):
            flush()
            cur_loc = t[:60]
        buf.append(t)
    flush()
    for ti, tbl in enumerate(d.tables, 1):
        rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in tbl.rows]
        segs += _rows_to_segments(f"table {ti}", [r for r in rows if any(r)])
    if not segs:
        raise Unreadable("This Word document has no text.")
    return Extracted(segs, "docx", {"paragraphs": len(d.paragraphs), "tables": len(d.tables)})


def extract_xlsx(data: bytes) -> Extracted:
    check_zip(data)
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001  parsers raise many types for damaged packages
        raise Unreadable("This spreadsheet could not be read.") from None
    segs, cells, sheets = [], 0, []
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v).strip() for v in r]
            if any(vals):
                rows.append(vals)
                cells += len(vals)
            if len(rows) >= MAX_SHEET_ROWS or cells > MAX_SHEET_CELLS:
                break
        sheets.append({"name": ws.title, "rows": len(rows)})
        segs += _rows_to_segments(f"sheet {ws.title}", rows)
        if cells > MAX_SHEET_CELLS:
            break
    wb.close()
    if not segs:
        raise Unreadable("This spreadsheet has no data.")
    return Extracted(segs, "xlsx", {"sheets": sheets, "truncated": cells > MAX_SHEET_CELLS})


def extract_pptx(data: bytes) -> Extracted:
    check_zip(data)
    from pptx import Presentation
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception:  # noqa: BLE001  parsers raise many types for damaged packages
        raise Unreadable("This presentation could not be read.") from None
    segs = []
    for i, slide in enumerate(prs.slides, 1):
        parts = []
        for sh in slide.shapes:
            if sh.has_text_frame:
                parts.append(sh.text_frame.text.strip())
            if getattr(sh, "has_table", False) and sh.has_table:
                parts += [" | ".join(c.text.strip() for c in r.cells) for r in sh.table.rows]
        if slide.has_notes_slide:
            parts.append("Notes: " + slide.notes_slide.notes_text_frame.text.strip())
        t = "\n".join(p for p in parts if p)
        if t:
            segs.append((f"slide {i}", t))
    if not segs:
        raise Unreadable("This presentation has no text.")
    return Extracted(segs, "pptx", {"slides": len(prs.slides)})


EXTRACTORS = {
    "txt": extract_text, "md": extract_text, "markdown": extract_text, "rst": extract_text, "log": extract_text,
    "tex": extract_text, "org": extract_text, "csv": extract_csv, "json": extract_json, "jsonl": extract_json,
    "pdf": extract_pdf, "docx": extract_docx, "xlsx": extract_xlsx, "pptx": extract_pptx,
    "tsv": lambda d: extract_csv(d, "\t"),
}


def extract(name: str, kind: str, data: bytes) -> Extracted:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    fn = EXTRACTORS.get(ext)
    if fn is None and kind == "code":
        fn = extract_text
    if fn is None:
        if kind in ("image", "video", "audio"):
            raise Unsupported(f"Text cannot be read from {kind} files in this release (no OCR or transcription service is configured).")
        if kind == "archive":
            raise Unsupported("Archives are stored but not opened.")
        raise Unsupported(f"Text cannot be read from .{ext or 'this'} files in this release.")
    return fn(data)
