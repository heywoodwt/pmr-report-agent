"""Build a structurally different flash to prove the pipeline reads labels, not positions.

The copy has blank rows inserted at the top of every sheet (so every cell address moves) and
one extra fund in the Tactical sleeve with subtotals and totals updated so the books still
foot. The new fund is sized to become the largest detractor.
"""

import shutil
from pathlib import Path

import openpyxl

from pmr import flash as fl
from pmr import xlsx

NEW_FUND = "Test Opportunistic Fund I"
SLEEVE = "Tactical Investments"
NEW_MV = 9_100_000.0
FUNDING_VALUES = {"Vintage": 2025, "Commitment Amount": 10_000_000.0, "Funded Amount": 10_000_000.0,
                  "Unfunded Commitments": 0.0, "Capital Returned": 0.0, "Market Value ($)": NEW_MV,
                  "Market Value (%)": 0.0}
CASH_VALUES = {fl.BEGIN: 10_000_000.0, fl.CONTRIB: 0.0, fl.DISTRIB: 0.0, fl.WITHDRAW: 0.0, fl.INCOME: 0.0,
               fl.FEES: 0.0, fl.APPREC: -900_000.0, fl.END: NEW_MV, fl.LTV: 0.0}
RETURNS_VALUES = {"Market Value ($)": NEW_MV, "Quarter NET": -9.0}


def _add_fund(ws, header_labels, sub_header, values, additive):
    hr = xlsx.find_row(ws, *header_labels)
    keys = xlsx.column_keys(ws, hr, hr + 1 if sub_header else None)
    col = {k: c for c, k in keys.items()}
    label_col = min(keys)

    def rows_labelled(label):
        return [r for r in range(hr + 1, ws.max_row + 1) if xlsx.text(ws.cell(r, label_col).value) == label]

    subtotal = rows_labelled(SLEEVE)[-1]
    ws.insert_rows(subtotal)
    ws.cell(subtotal, label_col, NEW_FUND)
    for k, v in values.items():
        ws.cell(subtotal, col[k], v)
    for r in (rows_labelled(SLEEVE)[-1], rows_labelled(fl.TOTAL_LABEL)[0]):
        for k in additive:
            ws.cell(r, col[k], (ws.cell(r, col[k]).value or 0) + values[k])


def perturbed_inputs(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    path = dst / "flash_4Q25.xlsx"
    wb = openpyxl.load_workbook(path)

    _add_fund(wb[fl.FUNDING], ("Investment", "Market Value ($)"), False, FUNDING_VALUES, fl.FUNDING_ADDITIVE)
    _add_fund(wb[fl.CASH], ("Investment", fl.BEGIN), False, CASH_VALUES, fl.CASH_ADDITIVE)
    _add_fund(wb[fl.RETURNS], ("Investment", "Market Value ($)"), True, RETURNS_VALUES, [])
    ws = wb[fl.FUNDING]
    comp = xlsx.find_row(ws, "Total Plan Assets")
    mv_col = next(c for c, k in xlsx.column_keys(ws, comp).items() if k == "Market Value")
    ws.cell(comp + 1, mv_col, ws.cell(comp + 1, mv_col).value + NEW_MV)

    for ws in wb.worksheets:
        for rng in list(ws.merged_cells.ranges):
            ws.unmerge_cells(str(rng))
        ws.insert_rows(1, amount=2)
    wb.save(path)
    return dst
