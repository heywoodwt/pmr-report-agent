"""Source evidence for a locator: the workbook cell in context, the PDF page, or the slide.

A locator is "<file>#<where>", as the pipeline writes it:
  flash.xlsx#Sheet!F11       a workbook cell
  flash.xlsx#Sheet           a whole sheet
  report.pdf#p10             a PDF page
  deck.pptx#slide4/image1    a slide, or one picture on it

Every view is read from the uploaded file itself, never from the pipeline's output, so the
reviewer compares the printed figure with the source and not with the pipeline's own reading.
"""

import base64
import re
from functools import lru_cache
from pathlib import Path

import pymupdf as fitz
import openpyxl
from fasthtml.common import Div, Img, P, Span, Table, Tbody, Td, Th, Thead, Tr
from monsterui.all import CodeSpan, TextT
from openpyxl.utils import column_index_from_string, get_column_letter

from pmr import deck
from pmr.narrative import norm

CELL = re.compile(r"^(?P<sheet>.+)!(?P<col>[A-Z]{1,3})(?P<row>\d+)$")


def split(loc: str) -> tuple[str, str]:
    file, _, where = loc.partition("#")
    return file, where


def resolve(inputs: Path, file: str) -> Path | None:
    """The uploaded file a locator names, or None if it is missing or outside the inputs folder."""
    path = (inputs / file).resolve()
    if not path.is_relative_to(inputs.resolve()) or not path.is_file():
        return None
    return path


@lru_cache(maxsize=16)
def _workbook(path: str, mtime: float):
    # mtime is part of the cache key, so a re-uploaded file is read again.
    return openpyxl.load_workbook(path, data_only=True)


@lru_cache(maxsize=8)
def _slides(path: str, mtime: float) -> list[deck.Slide]:
    return deck.load(Path(path))


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _png(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def cell_view(path: Path, where: str):
    """The cited cell, highlighted, with the rows and columns around it and its row and column labels."""
    wb = _workbook(str(path), path.stat().st_mtime)
    m = CELL.match(where)
    sheet = m["sheet"] if m else where
    # The pipeline cites sheets by their trimmed name, so "Log" finds a tab saved as "Log ".
    sheet = next((n for n in wb.sheetnames if n == sheet), None) or next(
        (n for n in wb.sheetnames if n.strip() == sheet.strip()), sheet)
    if sheet not in wb.sheetnames:
        return P(f"Sheet {sheet!r} is not in this workbook.", cls=TextT.error)
    ws = wb[sheet]
    if not m:
        # A whole-sheet citation: show its first rows, where the title and period are.
        r, c = 1, 1
        rows, cols = range(1, min(ws.max_row, 8) + 1), range(1, min(ws.max_column, 6) + 1)
    else:
        r, c = int(m["row"]), column_index_from_string(m["col"])
        rows = range(max(1, r - 4), min(ws.max_row, r + 4) + 1)
        # Column A carries the row labels in every flash sheet, so it is always shown.
        cols = sorted({1, *range(max(1, c - 3), min(ws.max_column, c + 3) + 1)})
    # The labels a reader would use: the leftmost text in the row, the nearest text above in the column.
    row_label = next((ws.cell(r, k).value for k in range(1, c) if isinstance(ws.cell(r, k).value, str)), None)
    col_label = next((ws.cell(k, c).value for k in range(r - 1, 0, -1) if isinstance(ws.cell(k, c).value, str)), None)
    head = Thead(Tr(Th(""), *[Th(get_column_letter(k)) for k in cols]))
    body = Tbody(*[Tr(Th(str(i)), *[Td(_fmt(ws.cell(i, k).value),
                                         cls="ev-hit" if m and (i, k) == (r, c) else "")
                                      for k in cols]) for i in rows])
    value = ws.cell(r, c).value if m else None
    return Div(
        Div(Span("Cell ", cls="ev-k"), CodeSpan(f"{sheet.strip()}!{where.split('!')[-1]}" if m else sheet.strip()),
            *([Span(" value ", cls="ev-k"), Span(_fmt(value), cls="num ev-val")] if m else []), cls="ev-line"),
        *([Div(Span("Row ", cls="ev-k"), Span(str(row_label)), cls="ev-line")] if row_label else []),
        *([Div(Span("Column ", cls="ev-k"), Span(str(col_label)), cls="ev-line")] if col_label else []),
        Div(Table(head, body, cls="ev-grid"), cls="ev-scroll"),
    )


def _search(page, quote: str) -> list:
    """Where a quote sits on a PDF page: the full quote, else its opening words, else nothing."""
    text = " ".join(quote.split())
    for attempt in (text, " ".join(text.split()[:8]), " ".join(text.split()[:4])):
        if attempt and (hits := page.search_for(attempt)):
            return hits
    return []


def pdf_view(path: Path, where: str, quotes: list[str]):
    m = re.match(r"^p(\d+)$", where)
    if not m:
        return P(f"Unrecognized PDF locator {where!r}.", cls=TextT.error)
    # Opened from the file and never saved: highlights exist only in the rendered image.
    with fitz.open(path) as doc:
        n = int(m[1])
        if not 1 <= n <= doc.page_count:
            return P(f"The PDF has no page {n}.", cls=TextT.error)
        page = doc[n - 1]
        found = 0
        for q in quotes:
            hits = _search(page, q) if q else []
            found += bool(hits)
            for rect in hits:
                # A light tint of the brand accent, so the quoted text stays readable.
                mark = page.add_highlight_annot(rect)
                mark.set_colors(stroke=(0.78, 0.85, 0.93))
                mark.update()
        png = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), annots=True).tobytes("png")
    note = (f"{found} of {len([q for q in quotes if q])} quoted passages located and highlighted."
            if any(quotes) else "Whole page cited.")
    return Div(Div(Span("Page ", cls="ev-k"), Span(str(n), cls="num"), Span(f"  {note}", cls="ev-k"), cls="ev-line"),
               Img(src=_png(png), alt=f"Page {n} of {path.name}", cls="ev-page"))


def slide_view(path: Path, where: str, quotes: list[str]):
    m = re.match(r"^slide(\d+)(?:/image(\d+))?$", where)
    if not m:
        return P(f"Unrecognized slide locator {where!r}.", cls=TextT.error)
    slides = {s.number: s for s in _slides(str(path), path.stat().st_mtime)}
    s = slides.get(int(m[1]))
    if s is None:
        return P(f"The deck has no slide {m[1]}.", cls=TextT.error)
    wanted = [norm(q).strip(" .") for q in quotes if q]
    # A slide line is highlighted when a quote is in it or it is part of a longer quote.
    lines = [P(line, cls="ev-hit-line" if any(w and (w in norm(line) or norm(line) in w) for w in wanted) else "")
             for line in s.lines]
    image = None
    if m[2] and int(m[2]) - 1 < len(s.images):
        blob, media = s.images[int(m[2]) - 1]
        image = Img(src=f"data:{media};base64,{base64.b64encode(blob).decode()}", alt=f"Chart on slide {s.number}",
                    cls="ev-page")
    return Div(Div(Span("Slide ", cls="ev-k"), Span(str(s.number), cls="num"), Span(f"  {s.title}"), cls="ev-line"),
               *([image] if image else []), Div(*lines, cls="ev-slide"))


def view(inputs: Path, loc: str, quotes: list[str]):
    """One locator's evidence, or a clear message saying why it cannot be shown."""
    file, where = split(loc)
    path = resolve(inputs, file)
    if path is None:
        return P("The cited file is not among this report's inputs: ", CodeSpan(file), cls=TextT.error)
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        return cell_view(path, where)
    if ext == ".pdf":
        return pdf_view(path, where, quotes)
    if ext == ".pptx":
        return slide_view(path, where, quotes)
    return P("No viewer for this file type: ", CodeSpan(loc))
