"""Check the rendered report against its manifest and the manifest against the inputs (REQ 1.8, 1.17).

1. Every number printed in the PDF must appear in a manifest entry (nothing reaches the page
   without a source).
2. Every printed figure in the manifest must appear in the PDF (nothing recorded was dropped).
3. Every manifest figure whose locator names a workbook cell must hold the same value as
   that cell (the locator really is the source).
"""

from pathlib import Path

import openpyxl
import pymupdf

from pmr.manifest import Manifest, numbers_in
from pmr.model import Issue
from pmr.prior_pmr import squash

def check_pdf(pdf: Path, m: Manifest) -> tuple[dict, list[Issue]]:
    # Every number string the manifest says the report may print.
    known = m.numbers()
    with pymupdf.open(pdf) as doc:
        # Plain text of each page, exactly as a reader (or a text extractor) sees it.
        pages = [p.get_text() for p in doc]
    # Check 1: walk every number on every page; any number no manifest entry holds has no source.
    unsourced, checked = [], 0
    for i, text in enumerate(pages, start=1):
        for n in numbers_in(text):
            checked += 1
            if n not in known:
                unsourced.append({"page": i, "number": n})
    # Check 2: the reverse direction. Every figure the renderer recorded must actually be on the page;
    # a figure missing from the PDF means it was dropped, truncated or wrapped mid-number.
    body = squash(" ".join(pages))
    dropped = [e for e in m.entries if e["kind"] == "figure" and e["text"] and "not printed" not in e["label"]
               and squash(e["text"]) not in body]
    # Both failures are blockers: a client report cannot ship with an unsourced or missing number.
    issues = [Issue("blocker", "number_unsourced", f"Page {u['page']} prints {u['number']}, which no manifest entry holds")
              for u in unsourced]
    issues += [Issue("blocker", "figure_dropped", f"{e['label'] or e['section']}: {e['text']!r} is not in the PDF",
                     (e["locs"] or [""])[0]) for e in dropped]
    return {"pages": len(pages), "numbers_checked": checked, "unsourced": unsourced,
            "dropped": [{"text": e["text"], "label": e["label"]} for e in dropped]}, issues


def check_locators(m: Manifest, inputs: Path) -> tuple[dict, list[Issue]]:
    """Re-open each workbook cell a figure cites and compare values."""
    # Workbooks opened so far, so each file is loaded once however many cells it is cited for.
    books: dict[str, openpyxl.Workbook] = {}
    checked, mismatched = 0, []
    for e in m.entries:
        # Only figures read straight from one cell can be compared with that cell; computed figures
        # (which carry a formula) and quotes are traced through their inputs instead.
        if e["kind"] != "figure" or e["formula"] is not None or len(e["locs"]) != 1:
            continue
        # A locator looks like "book.xlsx#CashActivity(Agg)!F11": file, then sheet!cell.
        file, _, where = e["locs"][0].partition("#")
        # PDF and slide locators ("#p3", "#slide4/image1") have no cell to re-read.
        if not file.lower().endswith((".xlsx", ".xlsm")) or "!" not in where:
            continue
        sheet, cell = where.rsplit("!", 1)
        if file not in books:
            books[file] = openpyxl.load_workbook(inputs / file, data_only=True)
        wb = books[file]
        # IC log sheet titles may carry stray spaces; locators store them stripped.
        ws = next((w for w in wb.worksheets if w.title.strip() == sheet.strip()), None)
        # Re-read the cell fresh from disk and compare it with the value the report printed from.
        actual = ws[cell].value if ws is not None else None
        checked += 1
        if actual != e["value"]:
            mismatched.append({"loc": e["locs"][0], "manifest": e["value"], "cell": actual})
    # Release file handles (matters on Windows, where open files cannot be replaced).
    for wb in books.values():
        wb.close()
    issues = [Issue("blocker", "locator_mismatch", f"{x['loc']} holds {x['cell']!r}, manifest says {x['manifest']!r}",
                    x["loc"]) for x in mismatched]
    return {"checked": checked, "mismatched": mismatched}, issues
