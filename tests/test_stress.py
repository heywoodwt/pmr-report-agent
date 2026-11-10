"""Input changes the synthetic 1Q26 quarter does not make.

synthetic.py and perturb.py were written alongside the pipeline, so they share its assumptions.
These tests change the inputs along other axes: column order, file names, sheet order and a
renamed sleeve. Each must either produce the same report or stop with a blocker, never a quietly
wrong report.
"""

import shutil

import openpyxl

from pmr.llm import Claude
from pmr.pipeline import run

ROLES = {"Redwood Logistics Trust": ["largest contributor", "second-largest individual position"],
         "Ironwood Value-Add Fund III": ["second-largest contributor"],
         "Meridian Opportunity Fund V": ["largest detractor"],
         "Cornerstone Core Property Fund": ["second-largest detractor", "largest individual position"]}


def _copy(inputs, tmp_path):
    folder = tmp_path / "inputs"
    shutil.copytree(inputs, folder)
    return folder


def _run(folder, tmp_path):
    return run(folder, "CPERS", "4Q25", tmp_path / "out", Claude(enabled=False))


def _blockers(facts) -> list[str]:
    return [i["message"] for i in facts["issues"] if i["level"] == "blocker"]


def _verified(facts) -> bool:
    v = facts["verification"]
    return not v["numbers"]["unsourced"] and not v["numbers"]["dropped"] and not v["locators"]["mismatched"]


def test_reordered_columns_give_the_same_report(inputs, tmp_path):
    folder = _copy(inputs, tmp_path)
    path = folder / "flash_4Q25.xlsx"
    wb = openpyxl.load_workbook(path)
    ws = wb["CashActivity(Agg)"]
    # Reverse every labelled column after the first, so each figure sits under a different letter.
    h = next(r for r in range(1, 10) if ws.cell(r, 1).value == "Investment")
    cols = [c for c in range(2, ws.max_column + 1) if ws.cell(h, c).value]
    grid = {(r, c): ws.cell(r, c).value for r in range(h, ws.max_row + 1) for c in cols}
    for r in range(h, ws.max_row + 1):
        for new, old in zip(cols, reversed(cols)):
            ws.cell(r, new).value = grid[(r, old)]
    wb.save(path)
    facts, _ = _run(folder, tmp_path)
    assert {f["fund"]: f["roles"] for f in facts["featured"]} == ROLES
    assert not _blockers(facts) and _verified(facts)


def test_renamed_files_and_shuffled_sheets_are_found_by_content(inputs, tmp_path):
    folder = _copy(inputs, tmp_path)
    for old, new in [("flash_4Q25.xlsx", "CPERS Q4-2025 data dump.xlsx"), ("ic_log_2025.xlsx", "IC approvals.xlsx"),
                     ("allocation_history.xlsx", "alloc.xlsx"), ("prior_pmr_3Q25.pdf", "CPERS report Q3.pdf"),
                     ("market_outlook_4Q25.pptx", "House View Dec 2025.pptx")]:
        (folder / old).rename(folder / new)
    path = folder / "CPERS Q4-2025 data dump.xlsx"
    wb = openpyxl.load_workbook(path)
    wb._sheets.reverse()
    wb.save(path)
    facts, _ = _run(folder, tmp_path)
    used = {d["file"] for d in facts["files"] if d["selected"]}
    assert {"CPERS Q4-2025 data dump.xlsx", "IC approvals.xlsx", "alloc.xlsx", "CPERS report Q3.pdf",
            "House View Dec 2025.pptx"} <= used
    assert {f["fund"]: f["roles"] for f in facts["featured"]} == ROLES
    # The market update's cache key includes the deck's name, so with no API key it is a marked
    # placeholder (a warning); every figure must still be sourced.
    assert not _blockers(facts) and _verified(facts)


def test_renamed_sleeve_stops_with_blockers(inputs, tmp_path):
    folder = _copy(inputs, tmp_path)
    path = folder / "flash_4Q25.xlsx"
    wb = openpyxl.load_workbook(path)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value == "Strategic Investments":
                    c.value = "Core Investments"
    wb.save(path)
    facts, _ = _run(folder, tmp_path)
    # The prior PMR's "Strategic Portfolio" section and its leverage limit no longer match a sleeve.
    # Guessing would misreport, so the run blocks approval instead.
    blockers = " ".join(_blockers(facts))
    assert "Strategic Portfolio" in blockers and "cannot map limits" in blockers
