"""Every figure the report prints, each with its source locator (REQ 1.8, 1.17).

A value read from a document is {"value", "loc"}. A value computed in code is
{"value", "formula", "locs"}, so a reviewer can re-derive it from the cells it names.
The renderer reads only this structure; it never opens an input file itself.
"""

from pmr import flash as fl
from pmr import style as st
from pmr.managers import ManagerDoc
from pmr.model import Fact, Issue
from pmr.prior_pmr import PriorPMR


# A Fact as JSON, or None when the cell was not found.
def fj(f: Fact | None) -> dict | None:
    return f.to_json() if f is not None else None


# A value computed in code, with the formula and the locators of its inputs.
def derived(value: float, formula: str, *facts: Fact | None) -> dict:
    return {"value": value, "formula": formula, "locs": [f.loc for f in facts if f is not None]}


def _ratio(num: Fact | None, den: Fact | None, formula: str) -> dict | None:
    # Percentage num/den, or None when either input is missing or the denominator is zero.
    if num is None or den is None or not fl.num(den):
        return None
    return derived(100 * fl.num(num) / fl.num(den), formula, num, den)


def build(flash: fl.Flash, prior: PriorPMR, sections: list[st.Section], benchmark: str | None,
          roles: dict[str, list[str]], docs: list[ManagerDoc]) -> tuple[dict, list[Issue]]:
    issues: list[Issue] = []
    comp, perf = flash.composition, flash.performance
    # The portfolio total row ("Corvane Initiated Investments") in each of the three fund sheets.
    # An empty dict stands in for a missing row, so a missing total shows up as a missing figure, not a crash.
    total_f = flash.funding_rows.get(fl.TOTAL_LABEL, {})
    total_c = flash.cash_rows.get(fl.TOTAL_LABEL, {})
    total_r = flash.returns_rows.get(fl.TOTAL_LABEL, {})
    # The client's own row in AnnReturns is labelled with the client name (plus " Portfolio").
    ann = next((v for k, v in flash.annual_rows.items() if fl.strip_portfolio(k) == flash.client_name), {})
    # The benchmark's rows: annualized returns (AnnReturns) and the quarter's return (ReturnsMultiples).
    bench_a = flash.annual_rows.get(benchmark or "", {})
    bench_r = flash.returns_rows.get(benchmark or "", {})

    # Portfolio-level figures for the overview, statistics table, TWR chart and performance update.
    # Composition values are read from the Portfolio Composition block; totals from the total rows.
    portfolio = {
        "total_plan_assets": fj(comp.get("Total Plan Assets")),
        "allocation": fj(comp.get("Allocation")),
        "allocation_pct": fj(comp.get("Allocation (%)")),  # a fraction, e.g. 0.1
        "nav": fj(comp.get("Market Value")),
        "nav_pct_plan": fj(comp.get("Market Value (%)")),  # a fraction
        # Not in the flash: computed here, so it carries its formula and both input cells.
        "nav_pct_target": _ratio(comp.get("Market Value"), comp.get("Allocation"),
                                 "100 * Market Value / Allocation (Portfolio Composition)"),
        "total_mv": fj(total_f.get("Market Value ($)")),
        "committed_funded": fj(total_f.get("Commitment Amount")),
        "qtr_gross": fj(perf.get("Quarter (%) TGRS")),
        "qtr_net": fj(perf.get("Quarter (%) TNET")),
        "net_1y": fj(perf.get("1 Year (%) TNET")),
        # The one-, three- and five-year horizons the prior PMR's TWR exhibit shows.
        "ann_net": {f"{n}": fj(ann.get(f"{n} Year Net")) for n in (1, 3, 5)},
        "income": fj(total_c.get(fl.INCOME)),
        "appreciation": fj(total_c.get(fl.APPREC)),
        "fees": fj(total_c.get(fl.FEES)),
        # Since-inception IRR and multiple for the funded investments (see DECISIONS.md for the row choice).
        "irr": fj(total_r.get("NET IRR")),
        "multiple": fj(total_r.get("Net Multiple")),
    }
    # The client's benchmark: the label found in the flash and named by the prior PMR, and its returns.
    bench = {
        "label": benchmark,
        "qtr_net": fj(bench_r.get("Quarter NET")),
        "ann_net": {f"{n}": fj(bench_a.get(f"{n} Year Net")) for n in (1, 3, 5)},
    }
    # One entry per sleeve, in sheet order: share of market value, quarterly return, value-weighted LTV.
    sleeves = [{
        "name": s,
        "mv": fj(flash.funding_rows.get(s, {}).get("Market Value ($)")),
        "mv_pct": fj(flash.funding_rows.get(s, {}).get(fl.FUNDING_PCT)),
        "qtr_net": fj(flash.returns_rows.get(s, {}).get("Quarter NET")),
        "ltv": fj(flash.cash_rows.get(s, {}).get(fl.LTV)),
    } for s in flash.sleeves]

    # Which manager document (if any) covers each fund, from managers.match_funds.
    doc_for = {d.fund: d for d in docs if d.fund}
    funds = []
    # Every fund in flash order, with the figures the sleeve sections and attribution table print.
    for f in flash.funds:
        c = f.cash
        funds.append({
            "name": f.name,
            "sleeve": f.sleeve,
            "vintage": fj(f.funding.get("Vintage")),
            "mv": fj(f.funding.get("Market Value ($)")),
            "mv_pct": fj(f.funding.get(fl.FUNDING_PCT)),
            "qtr_net": fj(f.returns.get("Quarter NET")),
            "income": fj(c.get(fl.INCOME)),
            "appreciation": fj(c.get(fl.APPREC)),
            "fees": fj(c.get(fl.FEES)),
            # REQ 1.3's dollar contribution: computed, so it carries its formula and the three cells it adds up.
            "contribution": derived(f.contribution(), "Gross Income + Appreciation - Manager Fees",
                                    c.get(fl.INCOME), c.get(fl.APPREC), c.get(fl.FEES)),
            "ltv": fj(c.get(fl.LTV)),
            # Roles from analytics.select_featured; an empty list means the fund gets a one-line mention.
            "roles": roles.get(f.name, []),
            "manager_doc": doc_for[f.name].file if f.name in doc_for else None,
            "manager_match": doc_for[f.name].match if f.name in doc_for else None,
        })

    # Diversification exhibits, plus the two Ex-US measures REQ 1.9 distinguishes: the fund-level
    # classification (used for compliance) and the look-through property share (used for the exhibit).
    mix = {
        "property": {k: fj(v) for k, v in flash.property_mix.items()},
        "geographic": {k: fj(v) for k, v in flash.geographic_mix.items()},
        "ex_us_fund_level": fj(flash.funding_rows.get(fl.EX_US_LABEL, {}).get(fl.FUNDING_PCT)),
        "ex_us_look_through": fj(flash.geographic_mix.get(fl.GEO_EX_US)),
    }

    # Facts only the prior PMR states (history, policy range, managers, disclosures).
    carried, c_issues = _carried(prior, sections, benchmark)
    issues += c_issues
    # A headline figure the flash does not supply is a blocker: the report cannot be finalized without it.
    for name, value in (("portfolio", portfolio), ("benchmark", bench)):
        missing = [k for k, v in value.items() if v is None]
        if missing:
            issues.append(Issue("blocker", "figure_missing", f"{name} figures not found in the flash: {missing}",
                                flash.file))
    return {"portfolio": portfolio, "benchmark": bench, "sleeves": sleeves, "funds": funds, "mix": mix,
            "carried": carried, "documents": {d.fund: d.file for d in docs if d.fund},
            "appendix": appendix(flash, benchmark)}, issues


def _block_rows(flash: fl.Flash, attr: str, cols: list[str]) -> list[dict]:
    """Fund rows sleeve by sleeve, each sleeve followed by its subtotal, then the portfolio total."""
    # attr names the sheet: "funding", "cash" or "returns"; its subtotal rows live in flash.<attr>_rows.
    totals = getattr(flash, attr + "_rows")
    rows = []
    for s in flash.sleeves:
        for f in (f for f in flash.funds if f.sleeve == s):
            vals = getattr(f, attr)
            rows.append({"label": f.name, "kind": "fund", "cells": [fj(vals.get(c)) for c in cols]})
        rows.append({"label": s, "kind": "subtotal", "cells": [fj(totals.get(s, {}).get(c)) for c in cols]})
    rows.append({"label": fl.TOTAL_LABEL, "kind": "total",
                 "cells": [fj(totals.get(fl.TOTAL_LABEL, {}).get(c)) for c in cols]})
    return rows


def appendix(flash: fl.Flash, benchmark: str | None) -> list[dict]:
    """The quarterly flash, re-tabled in house style as the prior PMR's appendix does (REQ 1.11).

    Column formats: usd = "$1,234", acct = "1,234" / "(1,234)", pct = "1.23%", pct1 = "1.2%",
    frac = a fraction shown as a percentage, text = as is.
    """
    comp, perf = flash.composition, flash.performance
    # The client's own row in AnnReturns, found by name rather than position.
    me = fl.strip_portfolio(flash.client_name)
    ann_me = next((v for k, v in flash.annual_rows.items() if fl.strip_portfolio(k) == me), {})
    cash_cols = [fl.BEGIN, fl.CONTRIB, fl.DISTRIB, fl.WITHDRAW, fl.INCOME, fl.FEES, fl.APPREC, fl.END, fl.LTV]
    ret_cols = ["Quarter INC", "Quarter APP", "Quarter GRS", "Quarter NET", "Market Value ($)"]
    # The benchmark's quarterly net return closes the returns table, as in the prior PMR's appendix.
    returns = _block_rows(flash, "returns", ret_cols)
    if benchmark:
        b = flash.returns_rows.get(benchmark, {})
        returns.append({"label": benchmark, "kind": "benchmark", "cells": [None, None, None, fj(b.get("Quarter NET")), None]})
    return [
        {"title": "Portfolio Composition", "level": 2, "new_page": False,
         "columns": ["Total Plan Assets", "Target Allocation", "Market Value", "Unfunded", "Remaining"],
         "formats": ["usd"] * 5,
         "rows": [{"label": None, "kind": "fund", "cells": [fj(comp.get(k)) for k in (
             "Total Plan Assets", "Allocation", "Market Value", "Unfunded Commitments", "Remaining Allocation")]}]},
        {"title": "Performance Summary (%)", "level": 2, "new_page": False,
         "columns": ["", "Quarter GRS", "Quarter NET", "1 Yr GRS", "1 Yr NET", "3 Yr GRS", "3 Yr NET", "5 Yr GRS"],
         "formats": ["text"] + ["pct"] * 7,
         "rows": [{"label": flash.client_name, "kind": "fund", "cells": [fj(perf.get(k)) for k in (
             "Quarter (%) TGRS", "Quarter (%) TNET", "1 Year (%) TGRS", "1 Year (%) TNET",
             "3 Year (%) TGRS", "3 Year (%) TNET", "5 Year (%) TGRS")]}]},
        {"title": "Investment Schedule", "level": 2, "new_page": False,
         "columns": ["Investment", "Vintage", "Commitment", "Funded", "Unfunded", "Returned", "Market Value", "% NAV"],
         "formats": ["text", "year", "usd", "usd", "usd", "usd", "usd", "pct"],
         "rows": [dict(r, cells=r["cells"] if r["kind"] == "fund" else [None] + r["cells"][1:])
                  for r in _block_rows(flash, "funding", ["Vintage"] + fl.FUNDING_ADDITIVE + [fl.FUNDING_PCT])]},
        {"title": "Cash Activity", "level": 1, "new_page": True,
         "columns": ["Investment", "Beginning MV", "Contrib.", "Distrib.", "Withdrawals", "Income", "Fees",
                     "Apprec.", "Ending MV", "LTV"],
         "formats": ["text", "usd", "usd", "usd", "usd", "acct", "acct", "acct", "usd", "pct1"],
         "rows": _block_rows(flash, "cash", cash_cols)},
        {"title": "Returns and Multiples (%)", "level": 2, "new_page": False,
         "columns": ["Investment", "Qtr INC", "Qtr APP", "Qtr GRS", "Qtr NET", "Market Value"],
         "formats": ["text", "pct", "pct", "pct", "pct", "usd"],
         "rows": returns},
        {"title": "Diversification and Annual Returns", "level": 1, "new_page": True, "columns": [], "rows": []},
        {"title": "Property Type Diversification (%)", "level": 2, "new_page": False,
         "columns": ["Property Type"] + list(flash.property_mix), "formats": ["text"] + ["pct"] * len(flash.property_mix),
         "rows": [{"label": "Total Portfolio", "kind": "fund", "cells": [fj(v) for v in flash.property_mix.values()]}]},
        {"title": "Geographic Diversification (%)", "level": 2, "new_page": False,
         "columns": ["Region"] + list(flash.geographic_mix),
         "formats": ["text"] + ["pct"] * len(flash.geographic_mix),
         "rows": [{"label": "Total Portfolio", "kind": "fund", "cells": [fj(v) for v in flash.geographic_mix.values()]}]},
        {"title": "Annualized Net Time-Weighted Return (%)", "level": 2, "new_page": False,
         "columns": ["Investment", "1 Year", "3 Year", "5 Year"], "formats": ["text", "pct", "pct", "pct"],
         "rows": [{"label": lab, "kind": "fund", "cells": [fj(row.get(f"{n} Year Net")) for n in (1, 3, 5)]}
                  for lab, row in ((benchmark, flash.annual_rows.get(benchmark or "", {})),
                                   (flash.client_name, ann_me)) if lab]},
    ]


def _carried(prior: PriorPMR, sections: list[st.Section], benchmark: str | None) -> tuple[dict, list[Issue]]:
    """Client facts that only the prior PMR states: history, policy range, managers, disclosures."""
    issues = []
    # Every section title: the text under one heading stops where the next heading starts.
    heads = [s.title for s in sections]
    overview = st.find_section(sections, "overview")
    managers = st.find_section(sections, "managers")
    disclosure = st.find_section(sections, "disclosure")

    # Run one regex over one section of the prior PMR; a miss is a warning, and that clause is left out of the report.
    def one(pattern, title, what):
        found = prior.find(pattern, title.title, heads) if title else []
        if not found:
            issues.append(Issue("warning", "carried_missing", f"Prior PMR does not state {what}; it is omitted",
                                prior.file))
        return [fj(f) for f in found]

    carried = {
        # The mandate's history (who retained whom, and when) is quoted verbatim from the prior overview.
        "history": (one(r"^(.*?discretion[^.]*\.)", overview, "the mandate history") or [None])[0],
        "allowable_range": one(r"allowable range of ([\d.]+)\s*[\u2013-]\s*([\d.]+)%", overview, "the allowable allocation range"),
        "benchmark_description": None,
        "managers": [],
        "disclosure": None,
    }
    # The benchmark's description ("NCREIF ODCE, net of fees") is only carried forward if the prior PMR
    # describes the same benchmark the flash names; otherwise it would describe the wrong series.
    desc = one(r"in excess of the (.+?) \(([^)]+)\)", overview, "the benchmark description")
    if desc and desc[0]["value"] == benchmark:
        carried["benchmark_description"] = desc[1]
    elif desc:
        issues.append(Issue("warning", "benchmark_description_mismatch",
                            f"Prior PMR describes {desc[0]['value']!r}, not {benchmark!r}; description omitted", prior.file))
    if managers:
        lines, loc = prior.section_lines(managers.title, heads)
        # Manager lines are the ones carrying an email address; heading and stray layout text are skipped.
        carried["managers"] = [{"value": x, "loc": loc} for x in lines if "@" in x]
    if disclosure:
        lines, loc = prior.section_lines(disclosure.title, heads)
        if lines:
            # The disclosure paragraph is rejoined from its wrapped lines and reproduced verbatim.
            carried["disclosure"] = {"value": " ".join(lines), "loc": loc}
    for key in ("managers", "disclosure"):
        if not carried[key]:
            issues.append(Issue("warning", "carried_missing", f"Prior PMR has no {key} text; section left empty",
                                prior.file))
    return carried, issues
