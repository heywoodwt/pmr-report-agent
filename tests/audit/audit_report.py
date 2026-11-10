"""Independent audit of a finished PMR PDF against the raw inputs.

This script does not import the pmr package. It re-reads the flash workbook with openpyxl, the
manager reports and prior PMR with PyMuPDF and the outlook deck with python-pptx, then checks
what the PDF prints:

1. Footing: fund rows sum to their sleeve subtotals and the sleeves sum to the portfolio row.
2. Roles: the featured funds and their roles, re-ranked from CashActivity(Agg) (REQ 1.3 to 1.5).
3. Fund figures: each fund's quarterly net return, market value and LTV, and for a featured fund
   its income, appreciation and contribution to the dollar.
4. Portfolio figures: the overview's totals, allocation shares and returns, recomputed from cells.
5. Quotes: each narrative sentence prints in the PDF, and each of its evidence quotes appears word
   for word on the page or slide it cites.

It reads manifest.json only for the list of narrative sentences and their quotes; every value it
compares against comes from the inputs. It does not check IC-log commitments, compliance, charts
or chart values read from deck images.

Usage:
    python tests/audit/audit_report.py --inputs inputs --pdf examples/PMR_CPERS_4Q25.pdf \
        --manifest output/CPERS_4Q25/manifest.json
Exits 1 if any check fails.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import openpyxl
import pymupdf
from pptx import Presentation

ORDINALS = ["largest", "second-largest"]


def norm(s: str) -> str:
    """Whitespace, quote and dash variants made uniform so a quote compares word for word."""
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("−", "-"), (" ", " ")):
        s = s.replace(a, b)
    return " ".join(s.split())


def usd(v: float) -> str:
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


# ---------------------------------------------------------------- flash, read by labels

def flash_rows(ws, header_label: str = "Investment") -> tuple[list, list[tuple]]:
    """The header row and every labelled row below it, as (label, values)."""
    rows = list(ws.iter_rows(values_only=True))
    h = next(i for i, r in enumerate(rows) if r[0] == header_label)
    return rows[h], [(r[0], r[1:]) for r in rows[h + 1:] if isinstance(r[0], str)]


def sleeves_and_funds(rows: list[tuple]) -> tuple[dict, list, tuple]:
    """Sleeve -> fund rows, sleeve subtotals, and the row after the last subtotal (the total).

    A sleeve opens with a label row that holds no numbers and closes where that label repeats
    with numbers.
    """
    has_num = lambda v: any(isinstance(x, (int, float)) for x in v)  # noqa: E731
    sleeves, subtotals, open_sleeve, total = {}, [], None, None
    for i, (label, vals) in enumerate(rows):
        if not has_num(vals):
            open_sleeve = label
            sleeves[label] = []
        elif label == open_sleeve:
            subtotals.append((label, vals))
            open_sleeve = None
            total = rows[i + 1]
        elif open_sleeve:
            sleeves[open_sleeve].append((label, vals))
    return sleeves, subtotals, total


class Audit:
    def __init__(self):
        self.passed, self.failed = 0, []

    def check(self, ok: bool, what: str):
        if ok:
            self.passed += 1
        else:
            self.failed.append(what)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inputs", type=Path, required=True)
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    args = ap.parse_args()
    a = Audit()

    with pymupdf.open(args.pdf) as d:
        text = norm(" ".join(p.get_text() for p in d))
    # The flash the report used is the one its manifest cites.
    entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    cited = {loc.split("#")[0] for e in entries for loc in e["locs"]}
    flash = next(args.inputs / f for f in sorted(cited) if f.endswith(".xlsx")
                 and "CashActivity(Agg)" in openpyxl.load_workbook(args.inputs / f, read_only=True).sheetnames)
    wb = openpyxl.load_workbook(flash, data_only=True)

    # 1. Footing of CashActivity(Agg): every money column, within USD 1.00.
    head, rows = flash_rows(wb["CashActivity(Agg)"])
    cols = {name: i - 1 for i, name in enumerate(head) if name}
    sleeves, subtotals, total = sleeves_and_funds(rows)
    money = [c for c in cols if c not in ("Investment", "LTV")]
    for sleeve, sub in subtotals:
        for c in money:
            s = sum(v[cols[c]] or 0 for _, v in sleeves[sleeve])
            a.check(abs(s - (sub[cols[c]] or 0)) <= 1.0, f"footing: {sleeve} {c} funds sum {s:,.2f} != {sub[cols[c]]}")
    for c in money:
        s = sum(sub[cols[c]] or 0 for _, sub in subtotals)
        a.check(abs(s - (total[1][cols[c]] or 0)) <= 1.0, f"footing: {total[0]} {c} sleeves sum {s:,.2f}")

    # 2. Roles, re-ranked from the cash activity cells.
    funds = {name: v for fs in sleeves.values() for name, v in fs}
    contrib = {n: (v[cols["Gross Income"]] or 0) + (v[cols["Appreciation"]] or 0) - (v[cols["Manager Fees"]] or 0)
               for n, v in funds.items()}
    mv = {n: v[cols["Market Value ($)"]] or 0 for n, v in funds.items()}
    expected: dict[str, set] = {}
    up = sorted((n for n in contrib if contrib[n] > 0), key=lambda n: -contrib[n])[:2]
    down = sorted((n for n in contrib if contrib[n] < 0), key=lambda n: contrib[n])[:2]
    big = sorted(mv, key=lambda n: -mv[n])[:2]
    for names, role in ((up, "contributor"), (down, "detractor"), (big, "individual position")):
        for k, n in enumerate(names):
            expected.setdefault(n, set()).add(f"{ORDINALS[k]} {role}")

    # Quarterly net return per fund from ReturnsMultiples(Agg): the NET column under "Quarter".
    rm = list(wb["ReturnsMultiples(Agg)"].iter_rows(values_only=True))
    h = next(i for i, r in enumerate(rm) if r[0] == "Investment")
    q = rm[h].index("Quarter")
    net_col = next(j for j in range(q, len(rm[h + 1])) if rm[h + 1][j] == "NET")
    qnet = {r[0]: r[net_col] for r in rm[h + 2:] if isinstance(r[0], str)}

    def ret(n: str) -> str | None:
        # The printed return, or None where the cell is blank and the report says so.
        return None if qnet.get(n) is None else f"{qnet[n]:.2f}"

    # 3. Each fund's sentence in its sleeve section.
    # A fund whose return cell is blank "reported no quarterly return in the flash".
    ret_pat = r"(?:returned (-?[\d.]+)% net during the Quarter|reported no quarterly return in the flash)"
    featured_pat = (r" was the Portfolio's ([^.]+)\. The Fund " + ret_pat + r" on "
                    r"(-?\$[\d,]+) of income and (-?\$[\d,]+) of (?:appreciation|depreciation), contributing "
                    r"(-?\$[\d,]+)\. Its market value was \$([\d.]+)M at Quarter-end and it carried ([\d.]+)% LTV")
    plain_pat = " " + ret_pat + r", with a market value of \$([\d.]+)M and ([\d.]+)% LTV"
    for n, v in funds.items():
        ltv = v[cols["LTV"]] or 0
        m = re.search(re.escape(n) + featured_pat, text)
        if n in expected:
            if not m:
                a.check(False, f"roles: {n} should be featured as {sorted(expected[n])} but has no featured paragraph")
                continue
            roles = set(m[1].split(" and "))
            a.check(roles == expected[n], f"roles: {n} printed {sorted(roles)}, expected {sorted(expected[n])}")
            inc, app = v[cols["Gross Income"]] or 0, v[cols["Appreciation"]] or 0
            for printed, want, what in ((m[2], ret(n), "quarter net"), (m[3], usd(inc), "income"),
                                        (m[4], usd(app), "appreciation"), (m[5], usd(contrib[n]), "contribution"),
                                        (m[6], f"{mv[n] / 1e6:.1f}", "market value"), (m[7], f"{ltv:.1f}", "LTV")):
                a.check(printed == want, f"{n} {what}: printed {printed}, inputs give {want}")
        else:
            a.check(m is None, f"roles: {n} is written up as featured but ranks in no role")
            p = re.search(re.escape(n) + plain_pat, text)
            if not p:
                a.check(False, f"{n}: no sleeve sentence found")
                continue
            for printed, want, what in ((p[1], ret(n), "quarter net"),
                                        (p[2], f"{mv[n] / 1e6:.1f}", "market value"), (p[3], f"{ltv:.1f}", "LTV")):
                a.check(printed == want, f"{n} {what}: printed {printed}, inputs give {want}")

    # 4. Overview figures from FundingStatus(Agg).
    fs = list(wb["FundingStatus(Agg)"].iter_rows(values_only=True))
    i = next(k for k, r in enumerate(fs) if r[0] == "Total Plan Assets")
    labels, vals = fs[i], fs[i + 1]
    tpa, alloc = vals[labels.index("Total Plan Assets")], vals[labels.index("Allocation")]
    alloc_pct, nav = vals[labels.index("Allocation") + 1], vals[labels.index("Market Value")]
    i = next(k for k, r in enumerate(fs) if r[0] == "Performance Summary")
    perf = next(r for r in fs[i + 2:] if isinstance(r[0], str))
    q_net, y_net = perf[fs[i].index("Quarter (%)") + 1], perf[fs[i].index("1 Year (%)") + 1]
    for want in (f"allocated {alloc_pct * 100:.1f}% of total plan assets",
                 f"net asset value was ${nav / 1e9:.2f} billion",
                 f"{nav / tpa * 100:.1f}% of the ${tpa / 1e9:.2f} billion total plan",
                 f"{nav / alloc * 100:.1f}% of the ${alloc / 1e9:.2f} billion target real estate allocation",
                 f"Portfolio returned {q_net:.2f}% net of all fees during the Quarter",
                 f"{y_net:.2f}% net over the trailing one-year period"):
        a.check(want in text, f"overview: {want!r} not printed")

    # 5. Narrative sentences and their quotes, against independently extracted source text.
    pages: dict[str, str] = {}

    def source(loc: str) -> str | None:
        file, _, where = loc.partition("#")
        if loc not in pages:
            path = args.inputs / file
            if file.endswith(".pdf") and where.startswith("p"):
                with pymupdf.open(path) as d:
                    pages[loc] = norm(d[int(where[1:]) - 1].get_text())
            elif file.endswith(".pptx") and where.startswith("slide") and "/" not in where:
                slide = Presentation(path).slides[int(where[5:]) - 1]
                bits = [s.text_frame.text for s in slide.shapes if s.has_text_frame]
                if slide.has_notes_slide:
                    bits.append(slide.notes_slide.notes_text_frame.text)
                pages[loc] = norm(" ".join(bits))
            else:
                return None
        return pages[loc]

    for e in (e for e in entries if e["kind"] == "narrative"):
        a.check(norm(e["text"]) in text, f"narrative not in PDF: {e['text'][:80]!r}")
        texts = [t for t in (source(loc) for loc in e["locs"]) if t is not None]
        for quote in filter(None, e.get("quotes") or []):
            a.check(any(norm(quote) in t for t in texts), f"quote not found in {e['locs']}: {quote[:80]!r}")

    n_quotes = sum(len([x for x in e.get("quotes") or [] if x]) for e in entries if e["kind"] == "narrative")
    print(f"Audit of {args.pdf} against {flash.name} and the cited sources")
    print(f"  featured funds re-ranked: {sorted(expected)}")
    print(f"  narrative sentences: {sum(e['kind'] == 'narrative' for e in entries)}, evidence quotes: {n_quotes}")
    print(f"  {a.passed} checks passed, {len(a.failed)} failed")
    for f in a.failed:
        print(f"  FAIL {f}")
    return 1 if a.failed else 0


if __name__ == "__main__":
    sys.exit(main())
