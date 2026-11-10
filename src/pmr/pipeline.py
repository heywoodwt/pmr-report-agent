"""Run discovery and every deterministic step, and return one JSON-ready facts document."""

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import openpyxl

from pmr import analytics, deck, discovery, figures, ic_log, managers, narrative, prior_pmr, render, review, style, verify, xlsx
from pmr import flash as fl
from pmr.llm import MODEL, Claude
from pmr.model import Issue
from pmr.periods import Quarter

# The steps of a run, in order. A caller can pass `progress` to run() to hear each one as it starts.
STAGES = ("Classifying input files", "Parsing the flash and running footing checks",
          "Reading the prior PMR and its house style", "Ranking funds and checking compliance",
          "Reading IC log commitments", "Matching manager documents", "Drafting and source-checking narrative",
          "Rendering and verifying the draft PDF", "Writing review outputs")


def _quiet(stage: str) -> None:
    pass


def _allocation_check(path: Path, inputs: Path, quarter: Quarter, flash: fl.Flash) -> tuple[list[dict], list[Issue]]:
    """The history has no client column, so tie its current-quarter NAV to the flash (REQ 1.14)."""
    file = path.relative_to(inputs).as_posix() # Get the relative path of the allocation history file for reporting in issues.
    wb = openpyxl.load_workbook(path, data_only=True)
    series, issues = [], []
    # Read the quarter / target / NAV table from any sheet that has it.
    for ws in wb.worksheets:
        header = xlsx.find_row(ws, "Quarter", "Target Allocation (USD MM)", "Net Asset Value (USD MM)")
        if header is None:
            continue
        for lab, vals in xlsx.read_rows(ws, file, header):
            # The exhibit runs up to the reporting quarter; later or unreadable labels are left out.
            try:
                if Quarter.parse(lab.value) > quarter:
                    continue
            except ValueError:
                continue
            # Get the quarter, target allocation, and NAV from the row and add them to the series.
            series.append({"quarter": lab.value, "target_musd": vals["Target Allocation (USD MM)"].to_json(),
                           "nav_musd": vals["Net Asset Value (USD MM)"].to_json()})
    wb.close() # Closes the workbook to free up system resources.
    current = next((s for s in series if s["quarter"] == quarter.code), None) # Find the row in the series that matches the current quarter code.
    # Get the total market value from the flash.
    total_mv = flash.funding_rows.get(fl.TOTAL_LABEL, {}).get("Market Value ($)")
    if current is None:
        issues.append(Issue("warning", "allocation_quarter_missing", f"Allocation history has no {quarter.code} row", file))
    # NAV off by more than USD 1MM: probably another client's file.
    elif total_mv is not None and abs(current["nav_musd"]["value"] - fl.num(total_mv) / 1e6) > 1.0: # Check if the NAV in the allocation history differs from the flash's total market value by more than 1 million USD.
        issues.append(Issue("warning", "allocation_nav_mismatch",
                            f"Allocation history {quarter.code} NAV {current['nav_musd']['value']}MM differs from flash "
                            f"{fl.num(total_mv) / 1e6:.1f}MM by more than 1MM; file may belong to another client",
                            current["nav_musd"]["loc"]))
    return series, issues


def build_facts(inputs: Path, client: str, quarter_code: str, progress: Callable[[str], None] = _quiet) -> dict:
    quarter = Quarter.parse(quarter_code)
    # 1. Pick the files.
    progress(STAGES[0])
    sel = discovery.discover(inputs, client, quarter)
    issues = list(sel.issues)
    facts: dict = {
        "client": {"input": client, "code": sel.client_code, "name": sel.client_name, "code_loc": sel.code_loc},
        "quarter": {"code": quarter.code, "label": quarter.label,
                    "start": quarter.start.isoformat(), "end": quarter.end.isoformat()},
        "files": sel.decisions,
    }
    # Without a flash or prior PMR nothing else can run; return what is known.
    if sel.flash is None or sel.prior_pmr is None:
        facts["issues"] = [i.to_json() for i in issues]
        return facts

    # 2. Parse the two core inputs, and measure the house style from the prior PMR.
    progress(STAGES[1])
    flash = fl.load(sel.flash, inputs)
    progress(STAGES[2])
    prior = prior_pmr.load(sel.prior_pmr, inputs)
    issues += flash.issues
    house, s_issues = style.load(sel.prior_pmr)
    issues += s_issues

    # 3. Benchmark, featured funds, compliance.
    progress(STAGES[3])
    benchmark, b_issues = analytics.find_benchmark(flash, prior.text)
    issues += b_issues
    featured, notes = analytics.select_featured(flash)
    comp, c_issues = analytics.compliance(flash, prior.guidelines, benchmark or "")
    issues += c_issues
    if not prior.guidelines:
        issues.append(Issue("blocker", "guidelines_missing", "No Guideline/Limit/Actual/Status table in the prior PMR",
                            prior.file))

    # 4. Commitments from the IC (Investment Committee) log.
    progress(STAGES[4])
    rows = ic_log.load(sel.ic_logs, inputs)
    activity = ic_log.analyze(rows, sel.client_code or "", sel.client_name or "", quarter,
                              [f.name for f in flash.funds], prior.text)
    issues += activity.issues

    # 5. Manager documents: match each to a fund, then use only those the report needs.
    progress(STAGES[5])
    docs = [managers.load(p, inputs) for p in sel.manager_docs]
    issues += managers.match_funds(docs, [f.name for f in flash.funds] + [a.row.fund for a in activity.open_approvals])
    roles = {f.name: f.roles for f in featured}
    new_funds = {a.row.fund for a in activity.new_commitments}
    for d, path in zip(docs, sel.manager_docs):
        if d.fund in roles:
            sel.decide(path, inputs, "manager_doc", True, f"manager report for featured fund {d.fund!r}")
        elif d.fund in new_funds:
            sel.decide(path, inputs, "manager_doc", True, f"strategy one-pager for new commitment {d.fund!r}")
        elif d.fund:
            sel.decide(path, inputs, "manager_doc", False, f"covers {d.fund!r}, which has no role this quarter")
        else:
            sel.decide(path, inputs, "manager_doc", False, "names no fund in the flash or this quarter's approvals")
    covered = {d.fund for d in docs}
    for fund in [f.name for f in featured] + sorted(new_funds):
        if fund not in covered:
            # REQ 1.15: safe to draft from the flash alone, but the reviewer must know.
            issues.append(Issue("warning", "manager_doc_missing",
                                f"No manager document for {fund!r}; commentary is limited to flash figures"))
    sel.decisions.sort(key=lambda d: d["file"])
    report, r_issues = figures.build(flash, prior, house.sections, benchmark, roles, docs)
    issues += r_issues

    # One approval, with its reversal and flash match, for facts.json.
    def approval_json(a: ic_log.Approval) -> dict:
        reversal = None
        if a.reversal is not None:
            # The log states the reason after a semicolon; it is quoted, never paraphrased.
            _, _, reason = a.reversal.recommendation.partition(";")
            reversal = {"action": a.reversal.action, "meeting_date": a.reversal.meeting_date.isoformat(),
                        "reason": reason.strip() or None, "loc": a.reversal.loc}
        return {"fund": a.row.fund, "amount_musd": a.amount_musd, "meeting_date": a.row.meeting_date.isoformat(),
                "ic_no": a.row.ic_no, "loc": a.row.loc, "amount_loc": a.row.amount_loc,
                "date_loc": a.row.date_loc, "reversal": reversal,
                "flash_match": a.in_flash.to_json() if a.in_flash else None,
                "document": next((d.file for d in docs if d.fund == a.row.fund), None)}

    # 6. Assemble the facts document.
    facts.update({
        "sources": {"flash": flash.file, "prior_pmr": prior.file,
                    "ic_logs": [p.relative_to(inputs).as_posix() for p in sel.ic_logs],
                    "deck": sel.deck.relative_to(inputs).as_posix() if sel.deck else None},
        "benchmark": benchmark,
        "sleeves": flash.sleeves,
        # Per-fund headline figures; blank cells stay None.
        "funds": [{"fund": f.name, "sleeve": f.sleeve,
                   "market_value": f.funding["Market Value ($)"].to_json(),
                   "quarter_net_twr": f.returns["Quarter NET"].to_json() if "Quarter NET" in f.returns else None,
                   "ltv": f.cash[fl.LTV].to_json() if fl.LTV in f.cash else None} for f in flash.funds],
        "attribution": analytics.attribution(flash),
        "featured": [{"fund": f.name, "sleeve": f.sleeve, "roles": f.roles} for f in featured],
        "featured_notes": notes,
        "commitments": {
            "new_this_quarter": [approval_json(a) for a in activity.new_commitments], # Returns a list of new commitments made this quarter, with each commitment represented as a dictionary using the approval_json function. Uses a loop to iterate over the new_commitments list in the activity object and applies the approval_json function to each approval object to convert it into a dictionary format suitable for inclusion in the facts document.
            "open_approvals": [approval_json(a) for a in activity.open_approvals],
            "reversed_this_quarter": [approval_json(a) for a in activity.reversed_this_quarter],
            "totals": analytics.committed_totals(flash, activity),
        },
        "compliance": comp,
        # Only failures are listed in full.
        "footing": {"checks": len(flash.checks), "failed": [c for c in flash.checks if not c["ok"]]},
        "report": report,
        "style": asdict(house),
    })
    # 7. Optional sanity check against the allocation history.
    if sel.allocation_history:
        # Check the allocation history against the current flash.
        series, a_issues = _allocation_check(sel.allocation_history, inputs, quarter, flash)
        facts["allocation_history"] = series
        issues += a_issues
    facts["issues"] = [i.to_json() for i in issues]
    return facts


# Issues that stop the report from being finalized.
def blockers(facts: dict) -> list[dict]:
    # Find all issues in the facts dictionary that are marked as "blocker" and return them in a list.
    return [i for i in facts.get("issues", []) if i["level"] == "blocker"] # Returns a list of issues that are blockers, which prevent the report from being finalized.


def build_narrative(facts: dict, inputs: Path, claude: Claude, corrections: dict | None = None
                    ) -> tuple[dict, list[Issue]]:
    """Model-drafted, source-checked prose for the sections that need judgment (see narrative.py).

    `corrections` is the reviewer's file (review.py): misread chart values are replaced before the
    market update is drafted, and excluded sentences are removed after the checks.
    """
    corrections = corrections or review.BLANK
    issues: list[Issue] = []
    r = facts["report"]
    # The prior PMR's own market update is the baseline for quarter-over-quarter comparison.
    prior = prior_pmr.load(inputs / facts["sources"]["prior_pmr"], inputs)
    sections = style.from_json(facts["style"]).sections
    market_sec = style.find_section(sections, "market")
    prior_lines, prior_loc = (prior.section_lines(market_sec.title, [s.title for s in sections])
                              if market_sec else ([], ""))
    # Manager documents are re-read from disk; facts only records which file belongs to which fund.
    # Sorted so the order of model calls, and so the run, is the same every time.
    docs = {f: managers.load(inputs / f, inputs) for f in sorted(set(r["documents"].values()))}

    # The reporting year, which a sentence may name ("the first quarter of 2026"). The flash's title
    # states the period, so the year cites the flash's FundingStatus sheet.
    period = {"year": facts["quarter"]["start"][:4], "locs": [f"{facts['sources']['flash']}#{fl.FUNDING}"]}

    # 1. Commentary for each featured fund that has a manager report.
    commentary = {}
    for f in r["funds"]:
        if f["roles"] and f["manager_doc"]:
            commentary[f["name"]] = narrative.commentary(docs[f["manager_doc"]], f["name"], f["roles"], claude, period)
            issues += narrative.issues_for(f"Commentary for {f['name']}", commentary[f["name"]])

    # 2. A strategy description for each new commitment that has a one-pager.
    strategies = {}
    for a in facts["commitments"]["new_this_quarter"]:
        if a["document"]:
            strategies[a["fund"]] = narrative.strategy(docs[a["document"]], a["fund"], claude)
            issues += narrative.issues_for(f"Strategy for {a['fund']}", strategies[a["fund"]])

    # 3. The market update, from the deck's text and its chart values.
    charts, market = [], {"status": "no_source", "paragraphs": [], "dropped": [], "cited_values": []}
    if facts["sources"].get("deck"):
        deck_file = facts["sources"]["deck"]
        slides = deck.load(inputs / deck_file)
        charts, c_issues = deck.read_charts(deck_file, slides, claude)
        issues += c_issues
        # Reviewer corrections to chart values replace the model's reading before anything cites them.
        fixes = corrections["chart_value_corrections"]
        for ch in charts:
            for sr in ch["series"]:
                for pt in sr["points"]:
                    if pt["id"] not in fixes:
                        continue
                    fix = fixes[pt["id"]]
                    # review.json can be edited by hand, so a correction is checked before it is used:
                    # NaN, infinity or a non-number would print as text the report cannot stand behind.
                    if isinstance(fix, bool) or not isinstance(fix, (int, float)) or not math.isfinite(fix):
                        issues.append(Issue("blocker", "correction_invalid",
                                            f"Chart value correction for {pt['id']!r} is not a finite number "
                                            f"({fix!r}); fix or clear it in review.json", ch["loc"]))
                        continue
                    pt.update({"model_value": pt["value"], "value": fix, "read": "reviewer"})
                    lo, hi = ch["axis"]
                    margin = 0.05 * (hi - lo) if hi > lo else 0
                    if hi > lo and not lo - margin <= fix <= hi + margin:
                        issues.append(Issue("warning", "chart_value_implausible",
                                            f"Slide {ch['slide']} chart: the reviewer's correction {fix} for "
                                            f"{pt['category']} is outside the printed axis ({lo} to {hi})", ch["loc"]))
        unknown = sorted(set(fixes) - {pt["id"] for ch in charts for sr in ch["series"] for pt in sr["points"]})
        if unknown:
            issues.append(Issue("warning", "correction_unmatched", f"review.json corrects unknown chart values {unknown}"))
        # Report figures the market update may cite to relate the market to the Portfolio.
        p = r["portfolio"]
        # Property types, largest first: the top two are offered to the market update as report figures.
        mix = sorted(((k, v) for k, v in r["mix"]["property"].items() if v), key=lambda kv: -kv[1]["value"])
        report_figures = {
            # The reporting period itself, so "the fourth quarter of 2025" can be cited.
            "reporting_quarter": {"text": facts["quarter"]["label"], "locs": [r["portfolio"]["qtr_net"]["loc"]]},
            "portfolio_quarter_net_return": {"text": f"{p['qtr_net']['value']:.2f}%", "locs": [p["qtr_net"]["loc"]]},
            "portfolio_one_year_net_return": {"text": f"{p['net_1y']['value']:.2f}%", "locs": [p["net_1y"]["loc"]]},
        }
        for rank, (name, v) in zip(("largest", "second_largest"), mix[:2]):
            report_figures[f"{rank}_property_exposure"] = {"text": f"{name} {v['value']:.1f}%", "locs": [v["loc"]]}
        market = narrative.market(deck_file, slides, charts, report_figures, " ".join(prior_lines), prior_loc, claude, period)
        issues += narrative.issues_for("Market update", market)

    # Reviewer exclusions: sentences that passed the checks but the reviewer chose to leave out.
    # Exclusions match on exact sentence text, so a regenerated sentence with new wording is not silently removed.
    excluded = set(corrections["excluded_sentences"])
    # Reviewer rewordings: applied only when they add no number or name the sentence's evidence lacks.
    edits, edited = corrections["edited_sentences"], set()
    for block in [*commentary.values(), *strategies.values(), market]:
        groups = [block] if "sentences" in block else block.get("paragraphs", [])
        for g in groups:
            g["excluded"] = [x for x in g["sentences"] if x["text"] in excluded]
            g["sentences"] = [x for x in g["sentences"] if x["text"] not in excluded]
            for x in g["sentences"]:
                new = edits.get(x["text"])
                if new is None or new == x["text"]:
                    continue
                edited.add(x["text"])
                problems = narrative.edit_problems(x, new)
                if problems:
                    issues.append(Issue("warning", "reviewer_edit_rejected",
                                        f"Reviewer edit not applied ({'; '.join(problems)}): {new[:120]!r}",
                                        x["locs"][0] if x["locs"] else ""))
                    continue
                x.update({"text": new, "edited_from": x["text"]})
        if "paragraphs" in block:
            block["paragraphs"] = [g for g in block["paragraphs"] if g["sentences"]]
        # A block the reviewer emptied falls back to its placeholder.
        if block["status"] == "generated" and not (block.get("sentences") or block.get("paragraphs")):
            block["status"] = "rejected"
    stale = sorted(set(edits) - edited)
    if stale:
        issues.append(Issue("warning", "correction_unmatched",
                            f"review.json edits {len(stale)} sentence(s) this draft does not contain: "
                            f"{[t[:60] for t in stale]}"))

    # One warning per section left as a placeholder because no model answer was available.
    blocks = [*commentary.values(), *strategies.values(), market]
    if any(b["status"] == "unavailable" for b in blocks):
        issues.append(Issue("warning", "narrative_unavailable",
                            "No model access and no cached answer: commentary, strategy or market text is a "
                            "placeholder in this draft"))
    return {"commentary": commentary, "strategy": strategies, "market": market, "charts": charts,
            "llm": {"model": MODEL, "live": claude.live, "calls": claude.calls, "errors": claude.errors,
                    "usage": claude.usage, "cost_usd": claude.cost_usd, "cache_files": sorted(claude.used)}}, issues


def _render_and_verify(facts: dict, inputs: Path, pdf: Path, draft: bool):
    """Render one PDF and run both verification checks on it."""
    m, issues = render.render(facts, pdf, draft=draft)
    pdf_check, pdf_issues = verify.check_pdf(pdf, m)
    loc_check, loc_issues = verify.check_locators(m, inputs)
    return m, {"pdf": pdf.name, "numbers": pdf_check, "locators": loc_check}, issues + pdf_issues + loc_issues


def run(inputs: Path, client: str, quarter_code: str, out_root: Path,
        claude: Claude | None = None, progress: Callable[[str], None] = _quiet) -> tuple[dict, Path]:
    """Build facts and narrative, render the draft PDF, verify it, and write the review outputs.

    Writes facts.json, manifest.json, the draft PDF and review.html; creates review.json the
    first time and never overwrites it, because it holds the reviewer's corrections and approval.
    """
    facts = build_facts(inputs, client, quarter_code, progress)
    out_dir = out_root / f"{facts['client']['code'] or client}_{facts['quarter']['code']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if "report" in facts:
        # The reviewer's corrections from any earlier draft of this client and quarter.
        corrections = review.load(out_dir)
        # Narrative is drafted after every figure is fixed, so the model never influences a number.
        progress(STAGES[6])
        facts["narrative"], n_issues = build_narrative(facts, inputs, claude or Claude(), corrections)
        facts["issues"] += [i.to_json() for i in n_issues]
        pdf = out_dir / f"PMR_{facts['client']['code']}_{facts['quarter']['code']}_draft.pdf"
        progress(STAGES[7])
        m, facts["verification"], issues = _render_and_verify(facts, inputs, pdf, draft=True)
        facts["issues"] += [i.to_json() for i in issues]
        progress(STAGES[8])
        (out_dir / "manifest.json").write_text(json.dumps(m.entries, indent=2, default=str), encoding="utf-8")
        # review.json is created once and then belongs to the reviewer; later runs never overwrite it.
        if not (out_dir / "review.json").is_file():
            review.save(out_dir, corrections)
        (out_dir / "review.html").write_text(review.page(facts, inputs, out_dir), encoding="utf-8")
    (out_dir / "facts.json").write_text(json.dumps(facts, indent=2, default=str), encoding="utf-8")
    return facts, out_dir


def finalize(facts: dict, inputs: Path, out_dir: Path) -> tuple[Path | None, str]:
    """Write the final PDF if the gate allows it: no blockers, approved, unchanged since approval."""
    ok, why = review.can_finalize(out_dir, facts)
    if not ok:
        return None, why
    pdf = out_dir / f"PMR_{facts['client']['code']}_{facts['quarter']['code']}.pdf"
    _, check, issues = _render_and_verify(facts, inputs, pdf, draft=False)
    # The final PDF must pass the same checks as the draft; otherwise it is removed, not shipped.
    if any(i.level == "blocker" for i in issues):
        pdf.unlink(missing_ok=True)
        return None, "the final PDF failed verification: " + "; ".join(i.message for i in issues if i.level == "blocker")
    return pdf, why
