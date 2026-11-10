"""Render the PMR PDF from facts.json alone (REQ 1.1, 1.19).

Section order, levels and page breaks come from the prior PMR's contents page (style.sections).
Each section title is mapped to a builder by keyword; a title with no builder still gets its
heading plus a visible placeholder, and a blocker is raised. Every number goes through the
manifest, so the finished PDF can be checked number by number (verify.py). The PDF carries no
citation markers, as the prior PMR has none; each figure's source is in manifest.json and on the
reviewer page.
"""

import re
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (BaseDocTemplate, Frame, KeepInFrame, NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.flowables import HRFlowable

from pmr import charts
from pmr import style as stl
from pmr.manifest import Manifest, numbers_in, pct
from pmr.model import Issue
from pmr.periods import Quarter

# What a reader sees where narrative needs the language-model step and it did not run.
PENDING = "[{what} is produced by the narrative step, which did not run for this draft.]"
# What a reader sees where the model drafted text but no sentence survived the source checks.
REJECTED = "[{what} was drafted but did not pass the source checks; the reviewer page lists why.]"
NO_MANAGER_REPORT = ("No manager report was supplied for this Fund for the Quarter. Commentary is therefore limited "
                     "to the flash-derived role, market value, return and dollar contribution above; no asset-level "
                     "drivers are reported for the Fund this Quarter.")
# A page's content may run this far past one page and still be scaled down to fit it, rather than
# spill a few lines onto a mostly blank page. At 1.25 the smallest body text is 80% of its size.
FIT_LIMIT = 1.25
_WORDS = {"1": "one", "3": "three", "5": "five", "10": "ten"}
# A percentage in the market update, with any sign or "~" before it: "+0.8%", "~4.6%", "-2.4%".
MARKET_FIGURE = re.compile(r"[~+\-−]?\d[\d,]*(?:\.\d+)?%")
# How each computed overview figure is derived (REQ 1.6), printed in its Sources note.
TOTAL_FORMULA = "Sum: flash committed total + amounts of open IC approvals (Recommendation text)"
POSITIONS_FORMULA = "Count: fund rows in the flash + open IC approvals (rows by IC #)"
FUNDED_FORMULA = "Count: fund rows in the flash"
AMOUNT_FORMULA = "Amount read from the IC log Recommendation text"


class Ctx:
    """Everything a section builder needs: facts, style, manifest and paragraph styles."""

    def __init__(self, facts: dict, st: stl.Style, m: Manifest):
        self.facts, self.st, self.m = facts, st, m
        self.r = facts["report"]
        # Usable text width: the page minus the measured left and right margins.
        self.width = st.page_width - 2 * st.margin_x
        self.issues: list[Issue] = []
        b, h1, h2, n, t = st.body, st.h1, st.h2, st.note, st.table
        # Paragraph styles built from the measured house style: body, headings, notes, table cells.
        # Body text is justified, as in the prior PMR.
        self.p_body = ParagraphStyle("body", fontName=b.font, fontSize=b.size, leading=st.body_leading,
                                     textColor=HexColor(b.color), spaceAfter=st.body_leading * 0.4,
                                     alignment=TA_JUSTIFY)
        self.p_h1 = ParagraphStyle("h1", fontName=h1.font, fontSize=h1.size, leading=h1.size * 1.25,
                                   textColor=HexColor(h1.color), spaceBefore=6)
        self.p_h2 = ParagraphStyle("h2", fontName=h2.font, fontSize=h2.size, leading=h2.size * 1.3,
                                   textColor=HexColor(h2.color), spaceBefore=8, spaceAfter=5)
        self.p_note = ParagraphStyle("note", fontName=n.font, fontSize=n.size, leading=n.size * 1.3,
                                     textColor=HexColor(n.color), spaceBefore=4, spaceAfter=6)
        self.p_cell = ParagraphStyle("cell", fontName=t.font, fontSize=t.size, leading=t.size * 1.2)
        self.p_head = ParagraphStyle("head", parent=self.p_cell, fontName=_bold(t.font), textColor=white)

    def para(self, html: str, style=None) -> Paragraph:
        return Paragraph(html, style or self.p_body)

    def h1(self, title: str) -> list:
        # A page heading is the title plus the primary-colored rule beneath it, as in the prior PMR.
        return [self.para(escape(title), self.p_h1),
                HRFlowable(width="100%", thickness=self.st.rule_width, color=HexColor(self.st.primary),
                           spaceBefore=2, spaceAfter=7)]

    def h2(self, title: str) -> list:
        return [self.para(escape(title), self.p_h2)]

    def table(self, header: list[str], rows: list[list], widths: list[float] | None = None, bold_rows: tuple = (),
              wrap: tuple = (0,), size: float | None = None) -> Table:
        """A table in the house style: primary header, striped rows, thin grid, numbers right-aligned.

        Columns in `wrap` hold text and may wrap; every other column is a plain string that never
        wraps, so a number can never be split across lines. Without `widths`, each non-wrapping
        column is sized to its widest entry and the wrapping columns share what is left; the font
        shrinks in small steps if even that does not fit.
        """
        st, size = self.st, size or self.st.table.size
        font, bold_font = st.table.font, _bold(st.table.font)
        # Cell padding is 4pt a side; the extra 2pt keeps a measured word from wrapping on rounding.
        pad = 10

        # A number column's width: its widest entry, and its header on one line (or, if that does
        # not fit, its longest header word, so a word is never split).
        def need(i: int, by_word: bool) -> float:
            head = header[i].split() if by_word else [header[i]]
            return max([stringWidth(str(r[i]), bold_font, size) for r in rows] +
                       [stringWidth(w, bold_font, size) for w in head]) + pad

        # Auto-size: measure each number column; if the text columns would be too narrow (under
        # 70pt each), wrap headers by word, then shrink the font by 0.3pt and measure again.
        auto = widths is None
        while widths is None:
            for by_word in (False, True):
                fixed = {i: need(i, by_word) for i in range(len(header)) if i not in wrap}
                left = self.width - sum(fixed.values())
                if left / max(len(wrap), 1) >= 70:
                    break
            if left / max(len(wrap), 1) >= 70 or size <= 5.5:
                # A wrapping column takes no more than its widest entry needs.
                natural = {i: max(stringWidth(str(r[i]), bold_font, size) for r in rows) + pad for i in wrap}
                share = left / max(len(wrap), 1)
                widths = [fixed.get(i, min(share, natural.get(i, share))) for i in range(len(header))]
            else:
                size = round(size - 0.3, 1)
        numeric = [i for i in range(len(header)) if i not in wrap]
        if auto and numeric:
            # Spread any width left over across the number columns, so the table spans the text
            # width as the prior PMR's tables do.
            spare = max(0.0, self.width - sum(widths)) / len(numeric)
            widths = [w + spare if i in numeric else w for i, w in enumerate(widths)]
        cell = ParagraphStyle("c", parent=self.p_cell, fontSize=size, leading=size * 1.2)
        head = ParagraphStyle("h", parent=self.p_head, fontSize=size, leading=size * 1.2)
        right_head = ParagraphStyle("rh", parent=head, alignment=2)
        # Header row: white bold text, right-aligned over number columns.
        data = [[Paragraph(escape(h), head if i in wrap else right_head) for i, h in enumerate(header)]]
        for r, row in enumerate(rows, start=1):
            b = r in bold_rows
            # Text columns become Paragraphs (they wrap); number columns stay plain strings (they never wrap).
            data.append([Paragraph(f"<b>{escape(str(v))}</b>" if b else escape(str(v)), cell) if i in wrap else str(v)
                         for i, v in enumerate(row)])
        # repeatRows=1 repeats the header row when a long table breaks across pages.
        t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        bold_cmds = [("FONTNAME", (0, r), (-1, r), bold_font) for r in bold_rows]
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 1), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), size),
            ("ALIGN", (0, 1), (-1, -1), "RIGHT"),
            *[("ALIGN", (i, 1), (i, -1), "LEFT") for i in wrap],
            *bold_cmds,
            ("BACKGROUND", (0, 0), (-1, 0), HexColor(st.primary)),
            # Alternate white and stripe-colored rows below the header, as in the prior PMR.
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, HexColor(st.stripe)]),
            ("GRID", (0, 0), (-1, -1), st.grid_width, HexColor(st.grid)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        return t


def _bold(font: str) -> str:
    return font if "Bold" in font else f"{font}-Bold"


def _e(s) -> str:
    return escape(str(s))


# "2025-10-14" -> "October 2025", the way the prior PMR dates approvals.
def _month(iso: str) -> str:
    return f"{date.fromisoformat(iso):%B %Y}"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _src(value, formula: str, locs: list[str]) -> dict:
    # A derived figure assembled at render time (e.g. counts and totals from the commitments block).
    return {"value": value, "formula": formula, "locs": locs}


# ["a", "b", "c"] -> "a, b and c".
def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------- section builders

def overview(c: Ctx, sec) -> list:
    r, m, f = c.r, c.m, c.facts
    p, b, car = r["portfolio"], r["benchmark"], r["carried"]
    t = f["commitments"]["totals"]
    flash_locs = [p["committed_funded"]["loc"]] if p["committed_funded"] else []
    # Counts for the overview sentence; the flash's committed total is a source for each of them.
    new = len(f["commitments"]["new_this_quarter"])
    # Open approvals from earlier quarters (approved, not yet closed, not withdrawn) are stated separately.
    earlier = t["open_positions"] - new
    total = m.fig(_src(t["total_committed_or_approved_usd"], TOTAL_FORMULA,
                       flash_locs + t["open_approval_amount_locs"]), lambda v: f"${v / 1e9:,.2f} billion")
    n_all = m.fig(_src(t["total_positions"], POSITIONS_FORMULA,
                       t["funded_position_locs"] + t["open_approval_locs"]), "count")
    n_funded = m.fig(_src(t["funded_positions"], FUNDED_FORMULA, t["funded_position_locs"]), "count")
    split = [f"{m.fig(_src(new, 'Count: IC approvals this quarter, still open (rows by IC #)', t['open_approval_locs']), 'count')} "
             "approved during the Quarter"] if new else []
    if earlier:
        split.append(f"{m.fig(_src(earlier, 'Count: IC approvals in earlier quarters, still open (rows by IC #)', t['open_approval_locs']), 'count')} "
                     "approved in earlier quarters")
    # REQ 1.6: state the funded/approved split rather than leaving it implicit.
    clause = (f": {n_funded} funded and carried in the flash, plus {_join(split)} that had not closed by Quarter-end"
              if split else f", all {n_funded} of them funded and carried in the flash")
    # The mandate history is quoted from the prior PMR; it is omitted (with a warning) if not found.
    history = f"{_e(m.text(car['history']))} " if car.get("history") else ""
    out = [c.para(
        f"{history}Since inception, approximately {total} has been committed or approved for commitment across "
        f"{n_all} individual investment positions{clause}. The funded investments have generated a since-inception "
        f"net IRR of {m.fig(p['irr'], 'pct1')} and a {m.fig(p['multiple'], 'mult')} net equity multiple.")]

    code = _e(f["client"]["code"] or f["client"]["name"])
    rng = car.get("allowable_range") or []
    rng_txt = f", with an allowable range of {m.text(rng[0])}-{m.text(rng[1])}%" if len(rng) == 2 else ""
    desc = f" ({_e(m.text(car['benchmark_description']))})" if car.get("benchmark_description") else ""
    out.append(c.para(
        f"{code} has allocated {m.fig(p['allocation_pct'], 'frac')} of total plan assets to real estate{rng_txt}. "
        f"At Quarter-end the Portfolio's net asset value was {m.fig(p['nav'], lambda v: f'${v / 1e9:,.2f} billion')}, "
        # The two allocation measures are bold, as in the prior PMR.
        f"which is <b>{m.fig(p['nav_pct_plan'], 'frac')} of the "
        f"{m.fig(p['total_plan_assets'], lambda v: f'${v / 1e9:,.2f} billion')} total plan</b> and "
        f"<b>{m.fig(p['nav_pct_target'], 'pct1')} of the {m.fig(p['allocation'], lambda v: f'${v / 1e9:,.2f} billion')} "
        f"target real estate allocation</b>. The Portfolio seeks long-term net nominal returns in excess of the "
        f"{_e(b['label'])}{desc}, and is diversified across property type, geography and strategy."))
    out.append(c.para(
        f"The Portfolio returned {m.fig(p['qtr_net'], 'pct')} net of all fees during the Quarter and "
        f"{m.fig(p['net_1y'], 'pct')} net over the trailing one-year period, against "
        f"{m.fig(b['qtr_net'], 'pct')} and {m.fig(b['ann_net']['1'], 'pct')} respectively for the benchmark."))
    return out


def guidelines(c: Ctx, sec) -> list:
    r, m, f = c.r, c.m, c.facts
    p, t = r["portfolio"], f["commitments"]["totals"]
    flash_locs = [p["committed_funded"]["loc"]] if p["committed_funded"] else []
    split = " / ".join(m.fig(s["mv_pct"], "pct1") for s in r["sleeves"])
    # "Strategic / Tactical split", built from the sleeve names the flash uses.
    split_label = " / ".join(s["name"].split()[0] for s in r["sleeves"]) + " split"
    rows = [
        ["Funded investments", m.fig(_src(t["funded_positions"], FUNDED_FORMULA, t["funded_position_locs"]), "count")],
        ["Approved, not yet closed", m.fig(_src(t["open_positions"], "Count: open IC approvals (rows by IC #)",
                                                t["open_approval_locs"]), "count")],
        ["Total committed or approved", m.fig(_src(t["total_committed_or_approved_usd"], TOTAL_FORMULA,
                                                   flash_locs + t["open_approval_amount_locs"]), "usd_m")],
        ["Of which funded", m.fig(p["committed_funded"], "usd_m")],
        ["Market value (NAV)", m.fig(p["total_mv"], "usd_m")],
        ["Real estate as % of total plan assets", m.fig(p["nav_pct_plan"], "frac")],
        ["NAV as % of target allocation", m.fig(p["nav_pct_target"], "pct1")],
        [split_label, split],
        ["Since-inception net IRR", m.fig(p["irr"], "pct1")],
        ["Since-inception equity multiple", m.fig(p["multiple"], "mult")],
    ]
    return [c.table(["Statistic", "Value"], rows, [0.52 * c.width, 0.2 * c.width])]


def managers(c: Ctx, sec) -> list:
    out = []
    for x in c.r["carried"]["managers"]:
        # "Name, Title • email": the name before the first comma is bold, as in the prior PMR.
        name, sep, rest = c.m.text(x).partition(",")
        out.append(c.para(f"<b>{_e(name)}</b>{_e(sep + rest)}"))
    return out


def twr(c: Ctx, sec) -> list:
    r, m = c.r, c.m
    p, b = r["portfolio"]["ann_net"], r["benchmark"]["ann_net"]
    # Horizons with both a portfolio and a benchmark figure; a missing one is left off the chart.
    horizons = [n for n in ("1", "3", "5") if p.get(n) and b.get(n)]
    chart = [charts.grouped_bars(c.st, m, "Annualized Time-Weighted Return (net, %)", [f"{n}-Yr" for n in horizons],
                                        [("Portfolio", [p[n] for n in horizons]),
                                         (r["benchmark"]["label"], [b[n] for n in horizons])], c.width)]
    # Which horizons beat the benchmark is decided by code, and the sentence is chosen to match.
    ahead = [n for n in horizons if p[n]["value"] > b[n]["value"]]
    behind = [n for n in horizons if n not in ahead]

    # ["1", "3", "5"] -> "one-, three- and five-year horizons".
    def span(ns):
        words = [_WORDS.get(n, n) for n in ns]
        return _join([w + "-" for w in words[:-1]] + [words[-1] + "-year"]) + (" horizons" if len(ns) > 1 else " horizon")

    if not behind:
        lead = f"The Portfolio outperformed the {_e(r['benchmark']['label'])} across the {span(ahead)} shown above"
    elif not ahead:
        lead = f"The Portfolio trailed the {_e(r['benchmark']['label'])} across the {span(behind)} shown above"
    else:
        lead = (f"The Portfolio outperformed the {_e(r['benchmark']['label'])} over the {span(ahead)} and trailed it "
                f"over the {span(behind)} shown above")
    text = (f"{lead}, returning {m.fig(r['portfolio']['net_1y'], 'pct')} net over the trailing year against "
            f"{m.fig(b['1'], 'pct')} for the benchmark.")
    return [*chart, Spacer(1, 6), c.para(text)]


def performance(c: Ctx, sec) -> list:
    r, m, f = c.r, c.m, c.facts
    p = r["portfolio"]
    # Negative appreciation is described as depreciation, printed as a positive amount.
    app = p["appreciation"]["value"]
    out = [c.para(
        f"The Portfolio generated approximately {m.fig(p['income'], 'usd_m')} of gross income and "
        f"{m.fig(_src(abs(app), 'absolute value of Appreciation', [p['appreciation']['loc']]), 'usd_m')} of "
        f"{'appreciation' if app >= 0 else 'depreciation'} during the Quarter, resulting in a total gross return of "
        f"{m.fig(p['qtr_gross'], 'pct')} ({m.fig(p['qtr_net'], 'pct')} net of all fees). The NAV of the Portfolio was "
        f"{m.fig(p['total_mv'], lambda v: f'${v / 1e9:,.2f} billion')} at Quarter-end.")]

    funds = {x["name"]: x for x in r["funds"]}
    featured = [funds[x["fund"]] for x in f["featured"]]
    # The attribution table lists contributors and detractors; a fund featured only as a large
    # position is described in the paragraph below the table, as the prior PMR does.
    ranked = [x for x in featured if any("contributor" in ro or "detractor" in ro for ro in x["roles"])]
    out += c.h2("Attribution: Quarterly Dollar Contribution")
    rows = [[x["name"], _cap(_join(x["roles"])), m.fig(x["income"], "acct"), m.fig(x["appreciation"], "acct"),
             m.fig(x["fees"], "acct"), m.fig(x["contribution"], "acct")] for x in ranked]
    w = c.width
    out.append(c.table(["Fund", "Role", "Income", "Apprec.", "Fees", "Contribution"], rows,
                       [0.24 * w, 0.3 * w, 0.1 * w, 0.11 * w, 0.09 * w, 0.12 * w], wrap=(0, 1)))
    out.append(Spacer(1, 8))

    sentences = []
    for x in featured:
        # Split each fund's roles: performance roles first, then position roles ("It is also...").
        con = [ro for ro in x["roles"] if "contributor" in ro or "detractor" in ro]
        pos = [ro for ro in x["roles"] if "position" in ro]
        name = f"<b>{_e(x['name'])}</b>"
        mv = m.fig(x["mv"], "usd_m")
        for ro in con:
            verb = "adding" if "contributor" in ro else "at"
            tail = " to performance during the Quarter" if "contributor" in ro else ""
            sentences.append(f"{name} was the Portfolio's {ro}{tail}, {verb} {m.fig(x['contribution'], 'usd')}.")
        for ro in pos:
            sentences.append(f"{'It' if con else name} {'is also' if con else 'is'} the Portfolio's {ro} at {mv}.")
    sentences += [_e(n) for n in f["featured_notes"]]
    out.append(c.para(" ".join(sentences)))
    return out


def sleeve(c: Ctx, sec, name: str) -> list:
    r, m = c.r, c.m
    # This section's sleeve: its share of market value and its quarterly return open the section.
    s = next(x for x in r["sleeves"] if x["name"] == name)
    out = [c.para(f"The {_e(sec.title)} represented approximately {m.fig(s['mv_pct'], 'pct1')} of the Portfolio's "
                  f"market value at Quarter-end and generated a {m.fig(s['qtr_net'], 'pct')} net return during the Quarter.")]
    # Every fund in the sleeve, in flash order, as the prior PMR walks its holdings.
    for x in (x for x in r["funds"] if x["sleeve"] == name):
        # A blank return cell is reported as such, not printed as "n/a".
        has_ret = x["qtr_net"] is not None and x["qtr_net"].get("value") is not None
        ret = (f"returned {m.fig(x['qtr_net'], 'pct')} net during the Quarter" if has_ret
               else "reported no quarterly return in the flash")
        # A fund with no role gets one line: return, market value and LTV.
        if not x["roles"]:
            out.append(c.para(f"{_e(x['name'])} {ret}, with a market value of {m.fig(x['mv'], 'usd_m')} and "
                              f"{m.fig(x['ltv'], 'pct1')} LTV."))
            continue
        # A featured fund gets its role, its figures, then commentary (or the no-report sentence).
        app = x["appreciation"]["value"] if x["appreciation"] else 0
        out.append(c.para(
            f"<b>{_e(x['name'])}</b> was the Portfolio's {_join(x['roles'])}. The Fund {ret} on "
            f"{m.fig(x['income'], 'usd')} of income and {m.fig(x['appreciation'], 'usd')} of "
            f"{'appreciation' if app >= 0 else 'depreciation'}, contributing {m.fig(x['contribution'], 'usd')}. "
            f"Its market value was {m.fig(x['mv'], 'usd_m')} at Quarter-end and it carried "
            f"{m.fig(x['ltv'], 'pct1')} LTV."))
        if x["manager_doc"]:
            # Checked commentary from the manager report, or a marked placeholder.
            out.append(_narrative_para(c, c.facts.get("narrative", {}).get("commentary", {}).get(x["name"]),
                                       f"Manager commentary drawn from {x['manager_doc']}", f"commentary: {x['name']}"))
        else:
            # REQ 1.15: no report, so no drivers are invented; the prior PMR's own wording is used.
            out.append(c.para(f"<i>{NO_MANAGER_REPORT}</i>"))
    return out


def activity(c: Ctx, sec) -> list:
    m, f = c.m, c.facts
    cm = f["commitments"]
    new = cm["new_this_quarter"]
    code = _e(f["client"]["code"] or f["client"]["name"])
    if not new:
        out = [c.para(f"The Investment Committee approved no new commitments for {code} during the Quarter.")]
    else:
        n = m.fig(_src(len(new), "Count: IC approvals this quarter, still open (rows by IC #)", [a["loc"] for a in new]), "count")
        # Wording that agrees with the number of commitments (one, two, or more).
        rest = {1: "It had not closed or funded by Quarter-end; accordingly it is",
                2: "Neither had closed or funded by Quarter-end; accordingly they are"}.get(
            len(new), "None had closed or funded by Quarter-end; accordingly they are")
        out = [c.para(f"During the Quarter the Investment Committee approved {n} new "
                      f"commitment{'s' if len(new) != 1 else ''} for {code}. {rest} reflected here as new investment "
                      "activity rather than in funded holdings or performance.")]
    for a in new:
        amount = m.fig(_src(a["amount_musd"] * 1e6, AMOUNT_FORMULA, [a["amount_loc"]]), "usd_m")
        when = m.text({"value": _month(a["meeting_date"]), "loc": a["date_loc"]})
        out += c.h2(f"{a['fund']}: {amount}, approved {when}")
        if a["document"]:
            # Checked strategy description from the one-pager, or a marked placeholder.
            out.append(_narrative_para(c, c.facts.get("narrative", {}).get("strategy", {}).get(a["fund"]),
                                       f"The strategy description drawn from {a['document']}", f"strategy: {a['fund']}"))
        else:
            out.append(c.para("<i>No strategy document was supplied for this commitment.</i>"))
    for a in cm["reversed_this_quarter"]:
        rv = a["reversal"]
        amount = m.fig(_src(a["amount_musd"], AMOUNT_FORMULA, [a["amount_loc"]]), lambda v: f"USD {v:g} million")
        when = m.text({"value": _month(a["meeting_date"]), "loc": a["date_loc"]})
        # REQ 1.6: one sentence per previously reported approval that was withdrawn or lapsed.
        verb = {"withdrawn": "was withdrawn", "lapsed": "lapsed"}.get(rv["action"], rv["action"])
        reason = f"; {_e(m.text({'value': rv['reason'], 'loc': rv['loc']}))}" if rv.get("reason") else ""
        out.append(c.para(f"The {amount} commitment to {_e(a['fund'])} approved in {when} {verb} during the "
                          f"Quarter{reason}."))
    return out


def _narrative_para(c: Ctx, block: dict | None, what: str, label: str) -> Paragraph:
    """One paragraph of checked sentences; each is recorded in the manifest with its evidence."""
    if block and block["status"] == "generated":
        return c.para(" ".join(_e(c.m.narrative(s, label)) for s in block["sentences"]))
    # No usable sentences: say why (checks failed, or no model answer) instead of leaving a gap.
    template = REJECTED if block and block["status"] == "rejected" else PENDING
    return c.para(_e(template.format(what=what)))


def market(c: Ctx, sec) -> list:
    block = c.facts.get("narrative", {}).get("market")
    what = "The market update condensed from the house-view outlook deck"
    if not block or block["status"] == "no_source":
        return [c.para("No market outlook deck was supplied for this Quarter; the market update is omitted.")]
    if block["status"] != "generated":
        return [_narrative_para(c, block, what, "market update")]
    # Market figures are bold and the Portfolio's own figures are not, as in the prior PMR.
    p, mix = c.r["portfolio"], c.r["mix"]["property"]
    own = {pct(p["qtr_net"]["value"]), pct(p["net_1y"]["value"])} | {pct(v["value"], 1) for v in mix.values() if v}

    def bold(sentence: str) -> str:
        return MARKET_FIGURE.sub(lambda mt: mt[0] if mt[0].lstrip("~+-−") in own else f"<b>{mt[0]}</b>", sentence)

    # Each paragraph is a list of checked sentences.
    out = [c.para(" ".join(bold(_e(c.m.narrative(s, "market update"))) for s in par["sentences"]))
           for par in block["paragraphs"]]
    # REQ 1.10: market indices and the client's benchmark are different series; say so.
    out.append(c.para(f"Market figures above are drawn from the house-view deck's exhibits and describe broad market "
                      f"indices. They are not the {_e(c.r['benchmark']['label'])}, which is a distinct client-specific "
                      "series and is the basis for the return-objective test; the two are related but not "
                      "interchangeable.", c.p_note))
    return out


def allocation(c: Ctx, sec) -> list:
    r, m, f = c.r, c.m, c.facts
    hist = f.get("allocation_history") or []
    if not hist:
        c.issues.append(Issue("warning", "allocation_exhibit_missing", "No allocation history; exhibit omitted"))
        return [c.para("No allocation history was supplied for this period.")]
    p = r["portfolio"]
    # The series' first and last points frame the sentence; the chart shows every quarter.
    first, last = hist[0], hist[-1]
    mm = lambda v: f"${v:,.0f}M"  # noqa: E731
    out = [c.para(
        f"Over the period shown the Portfolio's net asset value moved from {m.fig(first['nav_musd'], mm)} in "
        f"{m.fixed(first['quarter'])} to {m.fig(last['nav_musd'], mm)} in {m.fixed(last['quarter'])}, while the target "
        f"allocation moved with total plan assets. At Quarter-end the net asset value of "
        f"{m.fig(p['total_mv'], 'usd_m')} compared with a target allocation of {m.fig(p['allocation'], 'usd_m')}.")]
    out.append(charts.lines(c.st, m, "Target Allocation vs Net Asset Value ($MM)", [h["quarter"] for h in hist],
                                [("Target allocation", [h["target_musd"] for h in hist]),
                                 ("NAV", [h["nav_musd"] for h in hist])], c.width))
    return out


def diversification(c: Ctx, sec) -> list:
    r, m = c.r, c.m
    mix = r["mix"]
    out = [charts.pie(c.st, m, "Property Type Diversification", list(mix["property"].items()), c.width),
           Spacer(1, 10),
           charts.hbars(c.st, m, "Geographic Diversification (% of market value)",
                        list(mix["geographic"].items()), c.width)]
    if mix["ex_us_look_through"] and mix["ex_us_fund_level"]:
        out.append(c.para(
            f"Look-through Ex-US property exposure was {m.fig(mix['ex_us_look_through'], 'pct1')}. The compliance test "
            f"below uses the separate fund-level Ex-US classification, {m.fig(mix['ex_us_fund_level'], 'pct1')}; the two "
            "measure different things: one looks through to where the underlying assets sit, the other classifies "
            "the vehicle.", c.p_note))
    return out


def compliance(c: Ctx, sec) -> list:
    m = c.m
    # Limit text comes from the prior PMR; the actual figure and status come from analytics.compliance.
    rows = [[x["guideline"], m.text({"value": x["limit"], "loc": x["limit_loc"]}),
             m.text({"value": x["actual_text"], "locs": x["actual_locs"]}), x["status"] or "Not evaluated"]
            for x in c.facts["compliance"]]
    w = c.width
    return [c.table(["Guideline", "Limit", "Actual", "Status"], rows, [0.28 * w, 0.2 * w, 0.27 * w, 0.17 * w],
                    wrap=(0, 1, 2, 3)),
            c.para("Leverage is reported using the value-weighted loan-to-value subtotals for each sleeve, not an "
                   "average of individual fund ratios.", c.p_note)]


def disclosure(c: Ctx, sec) -> list:
    d = c.r["carried"].get("disclosure")
    return [c.para(_e(c.m.text(d)))] if d else []


def appendix(c: Ctx, sec) -> list:
    m = c.m
    out = [c.para(f"The exhibits below are the {_e(m.fixed(c.facts['quarter']['label']))} quarterly flash from which "
                  "every figure in this report is derived.")]
    for t in c.r["appendix"]:
        if t["new_page"]:
            out += [PageBreak()] + c.h1(t["title"])
        elif t["level"] == 1:
            out += c.h1(t["title"])
        else:
            out += c.h2(t["title"])
        if not t["columns"]:
            continue
        # A table whose first column is text has row labels; Portfolio Composition has none.
        has_label = t["formats"][0] == "text"
        fmts = t["formats"][1:] if has_label else t["formats"]
        rows, bold = [], []
        # Each cell is labelled with its column too, so a blank cell can be told apart from its row neighbours.
        heads = [" ".join(str(h).split()) for h in (t["columns"][1:] if has_label else t["columns"])]
        for i, row in enumerate(t["rows"], start=1):
            # Blank flash cells print blank (missing=""), never as zero.
            cells = [m.fig(v, fmt, f"{t['title']}: {row['label']}, {h}", missing="")
                     for v, fmt, h in zip(row["cells"], fmts, heads)]
            rows.append(([row["label"]] if has_label else []) + cells)
            if row["kind"] in ("subtotal", "total"):
                bold.append(i)
        # Label column wraps; tables without one (portfolio composition) split the width evenly.
        n = len(t["columns"])
        widths = None if has_label else [c.width / n] * n
        out.append(c.table(t["columns"], rows, widths, tuple(bold), wrap=(0,) if has_label else (),
                           size=c.st.appendix_table.size))
    return out


def _builder(sec: stl.Section, facts: dict):
    """Pick the builder for a contents title by keyword; sleeves by their first word."""
    t = sec.title.lower()
    # A sleeve section is recognized by the sleeve's first word in its title ("Strategic Portfolio",
    # "Tactical and Special Situations Portfolio"), so the titles can change without code changes.
    for s in facts["report"]["sleeves"]:
        if s["name"].split()[0].lower() in t and "portfolio" in t and "overview" not in t:
            return lambda c, sec, name=s["name"]: sleeve(c, sec, name)
    # Keyword -> builder. Order matters: the first keyword found in the title wins.
    table = [("overview", overview), ("guideline", guidelines), ("manager", managers),
             ("time-weighted", twr), ("performance update", performance), ("investment activity", activity),
             ("market", market), ("allocation", allocation), ("diversification", diversification),
             ("compliance", compliance), ("disclosure", disclosure), ("appendix", appendix)]
    return next((fn for key, fn in table if key in t), None)


# ---------------------------------------------------------------- document

def fit_pages(story: list, width: float, height: float) -> list:
    """Scale down each run of content between page breaks that slightly overflows one page.

    The page breaks are the prior PMR's, so a section that runs a little long would otherwise put
    its last lines on a page of their own. A run up to FIT_LIMIT pages tall is shrunk to fit one
    page; a run that fits is left alone, and a longer run flows across pages as normal.
    """
    out, run = [], []

    def flush():
        tall = sum(f.wrap(width, height)[1] + f.getSpaceBefore() + f.getSpaceAfter() for f in run)
        if height < tall <= FIT_LIMIT * height:
            out.append(KeepInFrame(width, height, run, mode="shrink"))
        else:
            out.extend(run)

    for f in story:
        if isinstance(f, PageBreak):
            flush()
            out.append(f)
            run = []
        else:
            run.append(f)
    flush()
    return out


def render(facts: dict, path, draft: bool = True) -> tuple[Manifest, list[Issue]]:
    """Render the report. A draft carries a "Draft for review" mark on every page; the final does not."""
    st = stl.from_json(facts["style"])
    m = Manifest()
    c = Ctx(facts, st, m)
    # The reporting quarter, used for the cover and the document title.
    quarter = Quarter.parse(facts["quarter"]["code"])

    # Drawn on every page: the firm's footer text, the page number, and the draft mark if a draft.
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(st.footer.font, st.footer.size)
        canvas.setFillColor(HexColor(st.footer.color))
        y = st.page_height - st.footer_y
        canvas.drawString(st.footer_x, y, st.footer_text)
        canvas.drawRightString(st.page_width - st.footer_x, y, m.fixed(f"Page {doc.page}", "page number"))
        if draft:
            # Centered in the footer band, in the accent color, so a draft can never pass for a final.
            canvas.setFillColor(HexColor(st.accent))
            canvas.drawCentredString(st.page_width / 2, y, "DRAFT FOR REVIEW")
        canvas.restoreState()

    # Drawn on page one only, then the footer on top.
    def cover(canvas, doc):
        # The prior cover, span for span, with the quarter label replaced by this quarter's.
        canvas.saveState()
        for text, ts, x, y in st.cover:
            # Only the quarter label changes; the client name lines and firm lines stay as the prior PMR has them.
            if Quarter.search(text):
                text = text.replace(Quarter.search(text).label, quarter.label)
            canvas.setFont(ts.font, ts.size)
            canvas.setFillColor(HexColor(ts.color))
            canvas.drawString(x, st.page_height - y, m.fixed(text, "cover") if numbers_in(text) else text)
        canvas.restoreState()
        footer(canvas, doc)

    # The text area: measured margins, top at the heading line, bottom clear of the footer.
    frame_h = st.page_height - st.margin_top - 60 + 4
    frame = Frame(st.margin_x, 60, c.width, frame_h, 0, 0, 0, 0)
    doc = BaseDocTemplate(str(path), pagesize=(st.page_width, st.page_height),
                          title=f"{facts['client']['name']} Performance Measurement Report, {quarter.label}",
                          author=st.footer_text.split("|")[0].strip(),
                          invariant=1)  # no timestamps or random IDs: identical inputs give identical bytes
    doc.addPageTemplates([PageTemplate("cover", [frame], onPage=cover), PageTemplate("body", [frame], onPage=footer)])

    # Page one is the cover (drawn by the template); every later page uses the body template.
    story = [NextPageTemplate("body"), PageBreak()]
    m.section = "Contents"
    story += c.h1("Contents")
    # Contents page: the prior PMR's section list, numbered as it numbers them.
    for i, sec in enumerate(st.sections, start=1):
        story.append(c.para(f"{m.fixed(f'{i}.', 'contents numbering')} {_e(sec.title)}",
                            ParagraphStyle("toc", parent=c.p_body, alignment=0, spaceAfter=c.st.body_leading * 0.4)))

    # Then each section in the prior PMR's order, starting a new page where the prior PMR did.
    for sec in st.sections:
        m.section = sec.title
        # Which builder writes this section; None means the title matched no keyword.
        fn = _builder(sec, facts)
        story += [PageBreak()] if sec.new_page else []
        story += c.h1(sec.title) if sec.level == 1 else c.h2(sec.title)
        if fn is None:
            c.issues.append(Issue("blocker", "section_unbuilt",
                                  f"No builder for prior-PMR section {sec.title!r}; a placeholder was rendered"))
            story.append(c.para(_e(f"[No content generator exists for the section '{sec.title}'.]")))
            continue
        story += fn(c, sec)

    # Lay out every page; the footer and cover callbacks record page numbers in the manifest as they run.
    doc.build(fit_pages(story, c.width, frame_h))
    return m, c.issues
