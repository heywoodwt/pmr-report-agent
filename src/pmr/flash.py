"""Parse the quarterly flash workbook by labels and check that it foots (REQ 1.2-1.3, 1.8-1.9).

Sheet names, column headers and the structural labels below are the file contract stated in
REQ 1.2 and 1.9. Client names, sleeve names, fund names and fund counts are all read from
the workbook.
"""

from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from pmr import xlsx
from pmr.model import Fact, Issue
from pmr.periods import Quarter

# Sheet names.
FUNDING = "FundingStatus(Agg)"  # commitments, funded, market value per fund
RETURNS = "ReturnsMultiples(Agg)"  # time-weighted returns per fund
CASH = "CashActivity(Agg)"  # quarterly cash flows and LTV per fund
PROPERTY = "All Property"  # property type mix
GEOGRAPHIC = "All Geographic"  # region mix
ANNUAL = "AnnReturns(Agg)"  # portfolio and benchmark annualized returns

# Row labels for the grand total and the non-US sleeve.
TOTAL_LABEL = "Corvane Initiated Investments"
EX_US_LABEL = "Ex-US Portfolio"
GEO_EX_US = "Ex-US"  # look-through column in All Geographic (REQ 1.9)

# CashActivity column headers.
BEGIN, CONTRIB, DISTRIB, WITHDRAW = "Beginning Market Value ($)", "Contributions", "Distributions", "Withdrawals"
INCOME, FEES, APPREC, END, LTV = "Gross Income", "Manager Fees", "Appreciation", "Market Value ($)", "LTV"
# Columns that must sum from funds to sleeves to total.
CASH_ADDITIVE = [BEGIN, CONTRIB, DISTRIB, WITHDRAW, INCOME, FEES, APPREC, END]
FUNDING_ADDITIVE = ["Commitment Amount", "Funded Amount", "Unfunded Commitments", "Capital Returned", "Market Value ($)"]
FUNDING_PCT = "Market Value (%)"

DOLLAR_TOL = 1.00  # flash dollars carry cents; $1 absorbs rounding in subtotals
PCT_TOL = 0.01  # percentage columns are rounded to 3 decimals per fund


# "X System Portfolio" -> "X System", so title variants compare equal.
def strip_portfolio(label: str) -> str:
    s = xlsx.text(label)
    return s[: -len(" Portfolio")] if s.endswith(" Portfolio") else s


def num(f: Fact | None) -> float:
    """Numeric value of a flow cell; a blank flow cell means no flow."""
    return float(f.value) if f is not None and xlsx.is_number(f.value) else 0.0


# One fund: its row from each of the three per-fund sheets.
@dataclass
class Fund:
    name: str
    sleeve: str  # the sleeve (sub-portfolio) the fund sits in
    funding: dict[str, Fact]  # FundingStatus row
    cash: dict[str, Fact] = field(default_factory=dict)  # CashActivity row
    returns: dict[str, Fact] = field(default_factory=dict)  # ReturnsMultiples row
    name_loc: str = ""  # FundingStatus cell holding the fund's name

    def contribution(self) -> float:
        """REQ 1.3: income + appreciation - manager fees, in dollars."""
        return num(self.cash.get(INCOME)) + num(self.cash.get(APPREC)) - num(self.cash.get(FEES))


# Everything parsed from one flash workbook.
@dataclass
class Flash:
    file: str  # path relative to inputs/
    client_name: str
    quarter: Quarter
    sleeves: list[str]  # sleeve names in sheet order
    funds: list[Fund]
    # Non-fund rows (sleeve subtotals, total, Ex-US) keyed by label, per sheet.
    funding_rows: dict[str, dict[str, Fact]]
    cash_rows: dict[str, dict[str, Fact]]
    returns_rows: dict[str, dict[str, Fact]]
    annual_rows: dict[str, dict[str, Fact]]  # includes benchmark rows
    composition: dict[str, Fact]  # Total Plan Assets block
    performance: dict[str, Fact]  # client's Performance Summary row
    property_mix: dict[str, Fact]  # sector -> % of portfolio
    geographic_mix: dict[str, Fact]  # region -> % of portfolio
    checks: list[dict] = field(default_factory=list)  # every footing check, pass or fail
    issues: list[Issue] = field(default_factory=list)

    # Look up a fund by exact name.
    def fund(self, name: str) -> Fund:
        return next(f for f in self.funds if f.name == name)


def title_and_quarter(path: Path) -> tuple[str, Quarter | None, bool]:
    """Client title, quarter and whether the flash carries data. Used by discovery."""
    # read_only: fast; only the title rows are needed.
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[FUNDING]
    # All text in the first 10 rows.
    texts = [xlsx.text(v) for row in ws.iter_rows(max_row=10, values_only=True) for v in row if isinstance(v, str)]
    wb.close()
    # Quarter: first text holding a "Fourth Quarter 2025"-style label.
    quarter = next((q for q in map(Quarter.search, texts) if q), None)
    # Title: first text that is not the quarter line.
    title = next((t for t in texts if Quarter.search(t) is None), "")
    # Decoy flashes say "No data available".
    has_data = not any("no data available" in t.lower() for t in texts)
    return strip_portfolio(title), quarter, has_data


def _split_sleeves(rows):
    """Split a table into sleeve blocks, fund rows and other labelled rows.

    A sleeve is a label row with no numbers that reappears later with numbers (its subtotal);
    every row between the two is a fund in that sleeve.
    """
    sleeves, funds, others = [], [], {}
    i = 0
    while i < len(rows):
        lab, vals = rows[i]
        # A heading row (no numbers) may open a sleeve.
        if not xlsx.has_numbers(vals):
            # Find the same label later with numbers: the sleeve subtotal.
            j = next((k for k in range(i + 1, len(rows))
                      if rows[k][0].value == lab.value and xlsx.has_numbers(rows[k][1])), None)
            if j is not None:
                sleeves.append(lab.value)
                # Rows between heading and subtotal are the sleeve's funds.
                funds += [(rows[k][0], rows[k][1], lab.value) for k in range(i + 1, j)]
                others[lab.value] = rows[j][1]
                # Skip past the subtotal.
                i = j + 1
                continue
        # Numeric rows outside any sleeve (total, Ex-US): first occurrence wins.
        elif lab.value not in others:
            others[lab.value] = vals
        i += 1
    return sleeves, funds, others


# Find a table by its header labels and read its rows; fail loudly if absent.
def _table(ws, file, *header_labels, sub_header=False):
    r = xlsx.find_row(ws, *header_labels)
    if r is None:
        raise ValueError(f"{file}: sheet {ws.title!r} has no header row with {header_labels}")
    return xlsx.read_rows(ws, file, r, r + 1 if sub_header else None)


# Values of the row whose label is label (ignoring " Portfolio"); {} if none.
def _row_for(rows, label):
    return next((v for lab, v in rows if strip_portfolio(lab.value) == label), {})


def load(path: Path, inputs_root: Path) -> Flash:
    file = path.relative_to(inputs_root).as_posix()
    client_name, quarter, _ = title_and_quarter(path)
    # data_only: read cached formula results, not formulas.
    wb = openpyxl.load_workbook(path, data_only=True)
    issues: list[Issue] = []

    # FundingStatus holds three stacked tables; each is located by its own header labels.
    ws = wb[FUNDING]
    # Table 1: portfolio composition, a single value row under its header.
    comp_row = xlsx.find_row(ws, "Total Plan Assets")
    composition = {
        # The second "Market Value" column is the percentage.
        k.replace(" #2", " (%)"): Fact(ws.cell(comp_row + 1, c).value, file, f"{ws.title}!{ws.cell(comp_row + 1, c).coordinate}")
        for c, k in xlsx.column_keys(ws, comp_row).items()
    }
    # Table 2: performance summary, two-level header.
    perf_rows = _table(ws, file, "Performance Summary", sub_header=True)
    performance = _row_for(perf_rows, client_name)
    # Table 3: per-fund investments; defines the fund list.
    sleeves, fund_rows, funding_rows = _split_sleeves(_table(ws, file, "Investment", "Market Value ($)"))
    funds = [Fund(lab.value, sleeve, vals, name_loc=lab.loc) for lab, vals, sleeve in fund_rows]

    # Same split on the other per-fund sheets.
    cash_sleeves, cash_funds, cash_rows = _split_sleeves(_table(wb[CASH], file, "Investment", BEGIN))
    ret_sleeves, ret_funds, returns_rows = _split_sleeves(
        _table(wb[RETURNS], file, "Investment", "Market Value ($)", sub_header=True))
    # AnnReturns has no funds; keep only its labelled rows.
    _, _, annual_rows = _split_sleeves(_table(wb[ANNUAL], file, "Investment", sub_header=True))

    # Attach CashActivity and ReturnsMultiples rows to funds by name and sleeve.
    by_name = {f.name: f for f in funds}
    for sheet, rows, attr in ((CASH, cash_funds, "cash"), (RETURNS, ret_funds, "returns")):
        for lab, vals, sleeve in rows:
            f = by_name.get(lab.value)
            # Fund on this sheet but not on FundingStatus, or in a different sleeve.
            if f is None or f.sleeve != sleeve:
                issues.append(Issue("blocker", "flash_fund_mismatch",
                                    f"{lab.value!r} ({sleeve}) in {sheet} has no matching row in {FUNDING}", lab.loc))
                continue
            setattr(f, attr, vals)
        # Fund on FundingStatus but missing from this sheet.
        seen = {lab.value for lab, _, _ in rows}
        for f in funds:
            if f.name not in seen:
                issues.append(Issue("blocker", "flash_fund_missing", f"{f.name!r} has no row in {sheet}", file))
    # All sheets must list the same sleeves in the same order.
    for sheet, s in ((CASH, cash_sleeves), (RETURNS, ret_sleeves)):
        if s != sleeves:
            issues.append(Issue("blocker", "flash_sleeve_mismatch", f"{sheet} sleeves {s} differ from {FUNDING} {sleeves}", file))

    # Diversification rows for this client.
    property_mix = _row_for(_table(wb[PROPERTY], file, "Property Type Diversification (%)"), client_name)
    geographic_mix = _row_for(_table(wb[GEOGRAPHIC], file, "Geographic Diversification (%)"), client_name)
    wb.close()

    flash = Flash(file, client_name, quarter, sleeves, funds, funding_rows, cash_rows,
                  returns_rows, annual_rows, composition, performance, property_mix, geographic_mix, issues=issues)
    # Client-level rows the report needs; missing means blocker.
    for name, value in (("performance summary", performance), ("property mix", property_mix),
                        ("geographic mix", geographic_mix)):
        if not value:
            issues.append(Issue("blocker", "flash_row_missing", f"No {name} row for {client_name!r}", file))
    run_footing_checks(flash)
    return flash


# Record one check; a failure becomes a blocker.
def _check(flash: Flash, name: str, expected: float, actual: float, tol: float, locs: list[str]):
    ok = abs(expected - actual) <= tol
    flash.checks.append({"check": name, "ok": ok, "expected": round(expected, 4), "actual": round(actual, 4),
                         "tolerance": tol, "locs": locs})
    if not ok:
        flash.issues.append(Issue("blocker", "footing_failed",
                                  f"{name}: expected {expected:,.2f}, found {actual:,.2f}", locs[0] if locs else flash.file))


def run_footing_checks(flash: Flash) -> None:
    """REQ 1.8: funds roll forward, funds foot to sleeves, sleeves foot to the total."""
    # 1. Roll-forward: begin + flows + income - fees + appreciation = ending market value.
    for f in flash.funds:
        c = f.cash
        if not c:
            continue
        rolled = (num(c.get(BEGIN)) + num(c.get(CONTRIB)) - num(c.get(DISTRIB)) - num(c.get(WITHDRAW))
                  + num(c.get(INCOME)) - num(c.get(FEES)) + num(c.get(APPREC)))
        _check(flash, f"roll-forward: {f.name}", num(c.get(END)), rolled, DOLLAR_TOL, [c[END].loc])

    # 2. Funds sum to each sleeve subtotal; sleeves sum to the total.
    # Each entry: sheet, its subtotal rows, each fund's row on that sheet, columns, tolerance.
    for sheet, totals, fund_rows, cols, tol in (
        (FUNDING, flash.funding_rows, [f.funding for f in flash.funds], FUNDING_ADDITIVE, DOLLAR_TOL),
        (FUNDING, flash.funding_rows, [f.funding for f in flash.funds], [FUNDING_PCT], PCT_TOL),
        (CASH, flash.cash_rows, [f.cash for f in flash.funds], CASH_ADDITIVE, DOLLAR_TOL),
    ):
        for col in cols:
            for sleeve in flash.sleeves:
                # This sleeve's fund rows.
                members = [row for f, row in zip(flash.funds, fund_rows) if f.sleeve == sleeve]
                sub = totals.get(sleeve, {}).get(col)
                # Column not on the subtotal row: nothing to check.
                if sub is None:
                    continue
                _check(flash, f"{sheet} {col}: {sleeve} = sum of funds", num(sub),
                       sum(num(m.get(col)) for m in members), tol, [sub.loc])
            total = totals.get(TOTAL_LABEL, {}).get(col)
            if total is not None:
                _check(flash, f"{sheet} {col}: {TOTAL_LABEL} = sum of sleeves", num(total),
                       sum(num(totals.get(s, {}).get(col)) for s in flash.sleeves), tol, [total.loc])

    # 3. Each fund's market value is the same on all three sheets.
    for f in flash.funds:
        mv = f.funding.get("Market Value ($)")
        for sheet, other in ((CASH, f.cash.get(END)), (RETURNS, f.returns.get("Market Value ($)"))):
            if mv is not None and other is not None:
                _check(flash, f"market value agrees across sheets: {f.name} ({sheet})", num(mv), num(other),
                       DOLLAR_TOL, [mv.loc, other.loc])

    # 4. Composition block market value equals the investment table total.
    total_mv = flash.funding_rows.get(TOTAL_LABEL, {}).get("Market Value ($)")
    comp_mv = flash.composition.get("Market Value")
    if total_mv is not None and comp_mv is not None:
        _check(flash, "portfolio composition market value = investment total", num(total_mv), num(comp_mv),
               DOLLAR_TOL, [comp_mv.loc, total_mv.loc])
