"""Build a synthetic next-quarter input folder (CPERS 1Q26) from the 4Q25 package.

The folder stands in for an unseen future period. Every change below is a deliberate test:

- The flash rolls every fund forward one quarter with new cash flows, so the rankings change.
- One fund winds down (its rows are deleted) and the open 4Q25 commitment Northgate funds
  (a new row), so cell addresses, fund counts and sleeve sizes all move.
- Apartment becomes the largest sector at over 40%, a compliance breach in a sector the
  4Q25 report never named.
- A new IC log adds two commitments (one under the client's full name), withdraws a
  previously reported approval, and carries traps: another client's approval of the same fund,
  an approval dated after quarter-end, a deferred item and a due-diligence item.
- Synthetic manager reports cover some featured funds and leave one out; one names its fund
  with a legal suffix. The 4Q25 reports and deck stay in the folder as stale decoys.
- The prior PMR is this program's own 4Q25 report, as it would be in production.

Every expected result is computed here with plain arithmetic, independently of the pipeline,
and returned so the test can compare. Run as a script to write the folder for a demo:

    python tests/synthetic.py <prior_pmr_4Q25.pdf> <out_dir>
"""

import random
import shutil
import sys
from datetime import datetime
from pathlib import Path

import openpyxl
import pymupdf
from pptx import Presentation

QUARTER_OLD, QUARTER_NEW = "Fourth Quarter 2025", "First Quarter 2026"
CLIENT = "Cascadia Public Employees' Retirement System"
WOUND_DOWN = "Tidewater Distressed Realty Fund"
FUNDED = "Northgate Logistics Partners"  # approved in 4Q25, closed and funded in 1Q26
FUNDED_CONTRIB, FUNDED_INCOME, FUNDED_FEES = 12_000_000.00, 60_000.00, 20_000.00

# Designed quarterly contribution (income + appreciation - fees, USD) for the funds that should rank.
DESIGNED = {
    "Summit Multifamily Partners": 1_900_000.00,        # largest contributor
    "Blackford Special Situations Fund II": 1_200_000.00,  # second contributor
    "Harborview Industrial Fund": -1_500_000.00,          # largest detractor
    "Kestrel U.S. Real Estate Fund": -700_000.00,          # second detractor, no manager report
}
# Every other fund gets a contribution in this band, so the designed four rank where intended.
OTHER_BAND = (-300_000.00, 600_000.00)

PROPERTY_MIX = {"Apartment": 41.2, "Office": 7.5, "Industrial": 33.0, "Retail": 8.0, "Hotel": 1.1, "Other": 9.2}
LTV = {"Strategic Investments": 35.1, "Tactical Investments": 39.0, "Corvane Initiated Investments": 36.0}
EX_US_SHARE = 10.4  # % of market value in non-US funds
BENCHMARK = {"quarter": 0.91, "1y": 2.93}  # benchmark net returns, %
PLAN_ASSETS = 5_050_000_000.00
IRR, MULTIPLE = 9.4, 1.46

# New IC log rows: (IC #, date, committee, category, client, fund, recommendation, action).
IC_ROWS = [
    ("7901", datetime(2026, 1, 13), "IC", "Other", None, None, "Approve 1Q26 IC procedures", "approved"),
    ("7902", datetime(2026, 2, 10), "IC", "Commitment", "CPERS", "Larkspur Residential Partners",
     "Approve a commitment of up to USD35 million to Larkspur Residential Partners", "approved"),
    ("7903", datetime(2026, 2, 24), "PM", "Commitment", CLIENT, "Ashgrove Core Income Fund",
     "Approve a commitment of up to USD 15 million to Ashgrove Core Income Fund", "approved"),
    ("7904", datetime(2026, 3, 3), "IC", "Commitment", "CPERS", "Osprey Hotel Partners",
     "Approve a commitment of up to USD10 million to Osprey Hotel Partners", "deferred"),
    ("7905", datetime(2026, 3, 10), "PM", "Commitment", "CPERS", "Ridgeline Value Fund IV",
     "Withdraw the previously approved USD 25 million commitment to Ridgeline Value Fund IV; "
     "the manager postponed its final close", "withdrawn"),
    ("7906", datetime(2026, 3, 17), "IC", "Commitment", "Northwind Pension Trust", "Larkspur Residential Partners",
     "Approve a commitment of up to USD20 million to Larkspur Residential Partners", "approved"),
    ("7907", datetime(2026, 3, 24), "AC", "Due Diligence", "CPERS", "Fairmount Net Lease Fund",
     "Approve Stage 3 due diligence for Fairmount Net Lease Fund", "approved"),
    ("7908", datetime(2026, 4, 7), "IC", "Commitment", "CPERS", "Juniper Data Center Fund",
     "Approve a commitment of up to USD20 million to Juniper Data Center Fund", "approved"),
]

# Synthetic manager documents: (file, fund name as the document prints it, body lines).
DOCS = [
    ("summit_1Q26.pdf", "Summit Multifamily Partners, L.P.", [
        "Quarterly Report to Limited Partners, First Quarter 2026",
        "The Fund's garden-style communities in Raleigh and Nashville reached 96% leased.",
        "Rent growth on renewals averaged 4.1% during the quarter.",
        "Appraised values rose following lower cap rates on two Sun Belt assets."]),
    ("blackford_1Q26.pdf", "Blackford Special Situations Fund II", [
        "Investor Update, First Quarter 2026",
        "The Fund sold the Lakeside Medical Office portfolio at a premium to its prior mark.",
        "Proceeds of USD 9 million were retained to fund a structured preferred investment."]),
    ("harborview_1Q26.pdf", "Harborview Industrial Fund", [
        "Quarterly Letter, First Quarter 2026",
        "Two tenants at the Inland Empire distribution park vacated at lease expiry.",
        "Occupancy fell to 81% and the appraisal reflected a longer lease-up assumption."]),
    ("cornerstone_1Q26.pdf", "Cornerstone Core Property Fund", [
        "Quarterly Report, First Quarter 2026",
        "The Fund's office exposure declined to 18% after the sale of 400 Market Street.",
        "Same-store net operating income grew 2.3% year over year."]),
    ("whitfield_1Q26.pdf", "Whitfield Diversified Property Fund", [
        "Quarterly Report, First Quarter 2026", "The Fund completed a refinancing of its retail centers."]),
    ("larkspur_onepager.pdf", "Larkspur Residential Partners", [
        "Strategy Overview, First Quarter 2026",
        "Larkspur Residential Partners is a value-add strategy acquiring workforce apartment communities",
        "in high-growth Sun Belt metropolitan areas, targeting renovation-driven rent growth."]),
]


def _replace_text(wb, old: str, new: str) -> None:
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value == old:
                    c.value = new


def _header(ws, first: str, second: str) -> tuple[int, dict[str, int]]:
    """Row of the header containing `first` and `second`, and column by header text (first wins)."""
    for r in range(1, ws.max_row + 1):
        texts = {ws.cell(r, c).value for c in range(1, ws.max_column + 1)}
        if first in texts and second in texts:
            cols = {}
            for c in range(1, ws.max_column + 1):
                v = ws.cell(r, c).value
                if isinstance(v, str) and v not in cols:
                    cols[v] = c
            return r, cols
    raise ValueError(f"{ws.title}: no header with {first!r} and {second!r}")


def _rows(ws, label: str) -> list[int]:
    return [r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == label]


def _block(ws, sleeve: str) -> list[int]:
    """Fund rows between a sleeve's heading and its subtotal."""
    head, sub = _rows(ws, sleeve)[0], _rows(ws, sleeve)[-1]
    return list(range(head + 1, sub))


def _restructure(ws, sleeve_for_new: str) -> None:
    """Delete the wound-down fund's row and insert a row for the newly funded fund."""
    for rng in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(rng))
    ws.delete_rows(_rows(ws, WOUND_DOWN)[0])
    sub = _rows(ws, sleeve_for_new)[-1]
    ws.insert_rows(sub)
    ws.cell(sub, 1, FUNDED)


def build(src: Path, prior_pdf: Path, dst: Path) -> dict:
    """Write the 1Q26 folder to dst and return the results the pipeline should produce."""
    rng = random.Random(42)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("~$*"))
    shutil.copy(prior_pdf, dst / "prior_pmr_4Q25.pdf")

    wb = openpyxl.load_workbook(src / "flash_4Q25.xlsx")
    _replace_text(wb, QUARTER_OLD, QUARTER_NEW)
    cash, fund_ws, ret, ann = wb["CashActivity(Agg)"], wb["FundingStatus(Agg)"], wb["ReturnsMultiples(Agg)"], wb["AnnReturns(Agg)"]
    sleeves = ["Strategic Investments", "Tactical Investments"]
    for ws in (cash, fund_ws, ret):
        _restructure(ws, sleeves[0])

    # 1. CashActivity: begin = last quarter's end; new flows; end rolls forward exactly.
    _, cc = _header(cash, "Investment", "Beginning Market Value ($)")
    flows = {}
    for sleeve in sleeves:
        for r in _block(cash, sleeve):
            name = cash.cell(r, 1).value
            if name == FUNDED:
                f = {"begin": 0.0, "contrib": FUNDED_CONTRIB, "distrib": 0.0, "withdraw": 0.0,
                     "income": FUNDED_INCOME, "fees": FUNDED_FEES, "apprec": 0.0}
            else:
                begin = float(cash.cell(r, cc["Market Value ($)"]).value)
                income = round(begin * rng.uniform(0.008, 0.012), 2)
                fees = round(begin * 0.002, 2)
                target = DESIGNED.get(name, round(rng.uniform(*OTHER_BAND), 2))
                f = {"begin": begin, "contrib": 0.0, "distrib": round(begin * 0.009, 2), "withdraw": 0.0,
                     "income": income, "fees": fees, "apprec": round(target - income + fees, 2)}
            f["end"] = round(f["begin"] + f["contrib"] - f["distrib"] - f["withdraw"] + f["income"] - f["fees"]
                             + f["apprec"], 2)
            f["sleeve"] = sleeve
            flows[name] = f
            for key, col in (("begin", "Beginning Market Value ($)"), ("contrib", "Contributions"),
                             ("distrib", "Distributions"), ("withdraw", "Withdrawals"), ("income", "Gross Income"),
                             ("fees", "Manager Fees"), ("apprec", "Appreciation"), ("end", "Market Value ($)")):
                cash.cell(r, cc[col], f[key])
            if name == FUNDED:
                cash.cell(r, cc["LTV"], 30.0)

    def sums(names):
        return {k: round(sum(flows[n][k] for n in names), 2) for k in ("begin", "contrib", "distrib", "withdraw",
                                                                      "income", "fees", "apprec", "end")}
    by_sleeve = {s: [n for n, f in flows.items() if f["sleeve"] == s] for s in sleeves}
    agg = {s: sums(by_sleeve[s]) for s in sleeves}
    agg["Corvane Initiated Investments"] = sums(list(flows))
    agg[CLIENT + " Portfolio"] = agg["Corvane Initiated Investments"]
    keymap = {"begin": "Beginning Market Value ($)", "contrib": "Contributions", "distrib": "Distributions",
              "withdraw": "Withdrawals", "income": "Gross Income", "fees": "Manager Fees", "apprec": "Appreciation",
              "end": "Market Value ($)"}
    for label, a in agg.items():
        r = _rows(cash, label)[-1]
        for k, col in keymap.items():
            cash.cell(r, cc[col], a[k])
        cash.cell(r, cc["LTV"], LTV.get(label, LTV["Corvane Initiated Investments"]))
    total_mv = agg["Corvane Initiated Investments"]["end"]

    # 2. FundingStatus: funded and returned capital accumulate; market value matches CashActivity.
    _, fc = _header(fund_ws, "Investment", "Market Value ($)")
    pct = {}
    for sleeve in sleeves:
        for r in _block(fund_ws, sleeve):
            name = fund_ws.cell(r, 1).value
            f = flows[name]
            if name == FUNDED:
                fund_ws.cell(r, fc["Vintage"], 2026)
                fund_ws.cell(r, fc["Commitment Amount"], 40_000_000.00)
                fund_ws.cell(r, fc["Funded Amount"], FUNDED_CONTRIB)
                fund_ws.cell(r, fc["Unfunded Commitments"], 40_000_000.00 - FUNDED_CONTRIB)
                fund_ws.cell(r, fc["Capital Returned"], 0.0)
            else:
                fund_ws.cell(r, fc["Funded Amount"], round(fund_ws.cell(r, fc["Funded Amount"]).value + f["contrib"], 2))
                fund_ws.cell(r, fc["Capital Returned"],
                             round(fund_ws.cell(r, fc["Capital Returned"]).value + f["distrib"] + f["withdraw"], 2))
            fund_ws.cell(r, fc["Market Value ($)"], f["end"])
            pct[name] = round(f["end"] / total_mv * 100, 3)
            fund_ws.cell(r, fc["Market Value (%)"], pct[name])
    additive = ["Commitment Amount", "Funded Amount", "Unfunded Commitments", "Capital Returned", "Market Value ($)"]
    fund_rows = {fund_ws.cell(r, 1).value: r for s in sleeves for r in _block(fund_ws, s)}
    totals = {}
    for label, names in [*by_sleeve.items(), ("Corvane Initiated Investments", list(flows)),
                         (CLIENT + " Portfolio", list(flows))]:
        r = _rows(fund_ws, label)[-1]
        totals[label] = {col: round(sum(fund_ws.cell(fund_rows[n], fc[col]).value for n in names), 2) for col in additive}
        for col in additive:
            fund_ws.cell(r, fc[col], totals[label][col])
        fund_ws.cell(r, fc["Market Value (%)"], 100 if len(names) == len(flows) else round(sum(pct[n] for n in names), 3))
    # US / Ex-US split by design.
    ex_us_mv = round(total_mv * EX_US_SHARE / 100, 2)
    for label, mv in (("US Portfolio", total_mv - ex_us_mv), ("Ex-US Portfolio", ex_us_mv)):
        r = _rows(fund_ws, label)[-1]
        fund_ws.cell(r, fc["Market Value ($)"], round(mv, 2))
        fund_ws.cell(r, fc["Market Value (%)"], mv / total_mv * 100)
    # Composition block: plan assets, allocation, market value, unfunded, remaining.
    comp = _rows(fund_ws, "Total Plan Assets")[0] + 1
    unfunded = totals["Corvane Initiated Investments"]["Unfunded Commitments"]
    allocation = round(PLAN_ASSETS * 0.10)
    for col, v in zip("ABCDEFGH", [PLAN_ASSETS, allocation, 0.10, round(total_mv), round(total_mv / PLAN_ASSETS, 4),
                                   round(unfunded), round(unfunded / PLAN_ASSETS, 4),
                                   allocation - round(total_mv) - round(unfunded)]):
        fund_ws[f"{col}{comp}"] = v

    # 3. Returns: quarter returns from this quarter's flows; 1-year rolls the new quarter in.
    hr, _ = _header(ret, "Investment", "Market Value ($)")
    qtr = {}

    def quarter_returns(f):
        if not f["begin"]:
            return None
        inc, app = f["income"] / f["begin"] * 100, f["apprec"] / f["begin"] * 100
        return {"INC": inc, "APP": app, "GRS": inc + app, "NET": (f["income"] + f["apprec"] - f["fees"]) / f["begin"] * 100}
    for label, f in [*flows.items(), *agg.items()]:
        qtr[label] = quarter_returns(f)
    # Columns: B market value; C-F quarter INC/APP/GRS/NET; G-J one year INC/APP/GRS/NET.
    for r in range(hr + 2, ret.max_row + 1):
        label = ret.cell(r, 1).value
        # A sleeve label appears twice; only its second row (the subtotal) carries numbers.
        if label in agg and r != _rows(ret, label)[-1]:
            continue
        if label in flows or label in agg:
            ret.cell(r, 2, (flows.get(label) or agg[label])["end"])
            q = qtr[label]
            for i, k in enumerate(("INC", "APP", "GRS", "NET")):
                old_q = ret.cell(r, 3 + i).value
                old_y = ret.cell(r, 7 + i).value
                ret.cell(r, 3 + i, None if q is None else q[k])
                # One year: drop last year's quarter (approximated by 4Q25's) and add this one.
                if q is not None and isinstance(old_y, (int, float)) and isinstance(old_q, (int, float)):
                    ret.cell(r, 7 + i, ((1 + old_y / 100) * (1 + q[k] / 100) / (1 + old_q / 100) - 1) * 100)
            if label == FUNDED:
                ret.cell(r, 21, "1Q 26")
            if label in ("Corvane Initiated Investments", CLIENT + " Portfolio"):
                ret.cell(r, 22, IRR)
                ret.cell(r, 23, MULTIPLE)
        elif label and "Benchmark" in str(label):
            ret.cell(r, 6, BENCHMARK["quarter"])
            ret.cell(r, 10, BENCHMARK["1y"])
    client_row = _rows(ret, CLIENT + " Portfolio")[-1]
    one_year = {k: ret.cell(client_row, 7 + i).value for i, k in enumerate(("INC", "APP", "GRS", "NET"))}
    # FundingStatus performance summary: quarter and 1-year gross and net.
    perf = _rows(fund_ws, CLIENT + " Portfolio")[0]
    for col, v in zip("BCDE", [qtr[CLIENT + " Portfolio"]["GRS"], qtr[CLIENT + " Portfolio"]["NET"],
                               one_year["GRS"], one_year["NET"]]):
        fund_ws[f"{col}{perf}"] = v
    # AnnReturns: benchmark and client 1-year.
    for r in range(1, ann.max_row + 1):
        label = ann.cell(r, 1).value
        if label and "Benchmark" in str(label):
            ann.cell(r, 5, BENCHMARK["1y"])
        elif label == CLIENT + " Portfolio":
            for i, k in enumerate(("INC", "APP", "GRS", "NET")):
                ann.cell(r, 2 + i, one_year[k])

    # 4. Diversification exhibits.
    ws = wb["All Property"]
    hr, pc = _header(ws, "Property Type Diversification (%)", "Apartment")
    r = _rows(ws, CLIENT + " Portfolio")[-1]
    for k, v in PROPERTY_MIX.items():
        ws.cell(r, pc[k], v)
    ws = wb["All Geographic"]
    r = _rows(ws, CLIENT + " Portfolio")[-1]
    ws.cell(r, 2, ws.cell(r, 2).value + 1.5)   # Northeast up,
    ws.cell(r, 9, ws.cell(r, 9).value - 1.5)   # Pacific down: still sums to 100
    wb.save(dst / "flash_1Q26.xlsx")
    wb.close()

    # 5. Allocation history: one more quarter.
    wb = openpyxl.load_workbook(dst / "allocation_history.xlsx")
    ws = wb.worksheets[0]
    ws.append(["1Q26", round(allocation / 1e6), round(total_mv / 1e6)])
    wb.save(dst / "allocation_history.xlsx")

    # 6. New IC log in the same layout as the 2025 log.
    src_log = openpyxl.load_workbook(src / "ic_log_2025.xlsx").worksheets[0]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = src_log.title
    ws.append([c.value for c in src_log[1]])
    for no, d, committee, cat, client, fund, rec, action in IC_ROWS:
        ws.append([no, d, committee, cat, client, fund, rec, None, "A. Reyes", action, None])
    wb.save(dst / "ic_log_2026.xlsx")

    # 7. Manager documents, plus a deck retitled for the new quarter.
    for file, fund, lines in DOCS:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(60, 60, 540, 760), "\n\n".join([fund] + lines), fontsize=11)
        doc.save(dst / "manager_reports" / file)
        doc.close()
    prs = Presentation(str(src / "market_outlook_4Q25.pptx"))
    for shape in prs.slides[0].shapes:
        if shape.has_text_frame:
            for p in shape.text_frame.paragraphs:
                for run in p.runs:
                    run.text = run.text.replace(QUARTER_OLD, QUARTER_NEW)
    prs.save(str(dst / "market_outlook_1Q26.pptx"))

    # Expected results, from the numbers above.
    contrib = {n: f["income"] + f["apprec"] - f["fees"] for n, f in flows.items()}
    ranked = sorted(contrib, key=contrib.get, reverse=True)
    by_mv = sorted(flows, key=lambda n: flows[n]["end"], reverse=True)
    commitment_total = totals["Corvane Initiated Investments"]["Commitment Amount"] + 35e6 + 15e6
    sector, share = max(PROPERTY_MIX.items(), key=lambda kv: kv[1])
    return {
        "funds": len(flows),
        "contributors": ranked[:2], "detractors": ranked[::-1][:2], "positions": by_mv[:2],
        "new_commitments": [("Larkspur Residential Partners", 35.0), ("Ashgrove Core Income Fund", 15.0)],
        "reversed": ["Ridgeline Value Fund IV"],
        "positions_total": len(flows) + 2,
        "committed_musd": round(commitment_total / 1e6, 1),
        "sector": f"{share}% ({sector})",
        "ex_us": f"{EX_US_SHARE}% Ex-US",
        "ltv": f"{LTV['Strategic Investments']}% strategic / {LTV['Tactical Investments']}% tactical",
        "statuses": ["Not in compliance", "In compliance", "In compliance",
                     "In compliance" if one_year["NET"] > BENCHMARK["1y"] else "Not in compliance"],
        "no_document": "Kestrel U.S. Real Estate Fund",
    }


if __name__ == "__main__":
    print(build(Path(__file__).resolve().parent.parent / "inputs", Path(sys.argv[1]), Path(sys.argv[2])))
