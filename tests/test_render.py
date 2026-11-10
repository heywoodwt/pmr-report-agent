import json
import re

import pymupdf

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import KeepInFrame, PageBreak, Paragraph, SimpleDocTemplate

from pmr import verify
from pmr.render import FIT_LIMIT, fit_pages
from pmr.llm import Claude
from pmr.manifest import Manifest
from pmr.pipeline import run

from perturb import NEW_FUND, perturbed_inputs


def _text(pdf) -> str:
    with pymupdf.open(pdf) as d:
        return "\n".join(p.get_text() for p in d)


def test_every_printed_number_has_a_source(rendered):
    facts, _ = rendered
    v = facts["verification"]
    assert v["numbers"]["numbers_checked"] > 500
    assert v["numbers"]["unsourced"] == [] and v["numbers"]["dropped"] == []
    assert v["locators"]["checked"] > 400 and v["locators"]["mismatched"] == []
    assert not [i for i in facts["issues"] if i["level"] == "blocker"]


def test_sections_render_in_prior_pmr_order(rendered):
    facts, out = rendered
    text = _text(out / facts["verification"]["pdf"])
    titles = [s["title"] for s in facts["style"]["sections"]]
    # Each title appears in the body after the contents page, in order.
    body = text.split(titles[-1], 1)[1]
    positions = [body.find(t) for t in titles[:-1]]
    assert all(p >= 0 for p in positions) and positions == sorted(positions)


def test_report_states_this_quarters_facts(rendered):
    facts, out = rendered
    text = " ".join(_text(out / facts["verification"]["pdf"]).split())
    assert "Fourth Quarter 2025" in text
    assert "Redwood Logistics Trust was the Portfolio's largest contributor" in text
    assert "Northgate Logistics Partners: $40.0M, approved October 2025" in text
    assert "No manager report was supplied for this Fund" in text  # Meridian
    assert "36.2% (Industrial)" in text


def test_client_pdf_matches_prior_pmr_style(rendered):
    facts, out = rendered
    text = " ".join(_text(out / facts["verification"]["pdf"]).split())
    # No citation markers and no Sources page, as in the prior PMR; sources live in the manifest.
    assert not re.search(r"\[\d+\]", text) and "Sources" not in text
    # Fund dollars print exactly, to the dollar.
    assert "adding $1,367,036" in text
    with pymupdf.open(out / facts["verification"]["pdf"]) as d:
        spans = [s for p in d for b in p.get_text("dict")["blocks"] for line in b.get("lines", [])
                 for s in line["spans"]]
    fonts = {s["text"].strip(): s["font"] for s in spans}
    assert "Bold" in fonts.get("Marcus Ellison", "")  # manager names are bold
    assert any("Oblique" in s["font"] and s["text"].startswith("No manager report") for s in spans)


def test_computed_figures_carry_formula_and_inputs(rendered):
    _, out = rendered
    entries = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    # The committed total says it is a sum, and cites the Recommendation cells its amounts are read
    # from, not the IC # cells, which are identifiers.
    total = next(e for e in entries if (e["formula"] or "").startswith("Sum: flash committed total"))
    locs = " ".join(total["locs"])
    assert "Log!G9" in locs and "Log!G10" in locs and "Log!A9" not in locs


def test_manifest_written_and_traceable(rendered):
    _, out = rendered
    entries = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    figures = [e for e in entries if e["kind"] == "figure"]
    assert figures and all(e["locs"] for e in figures)


def test_unsourced_number_is_caught(rendered):
    facts, out = rendered
    m = Manifest()
    m.entries = [e for e in json.loads((out / "manifest.json").read_text(encoding="utf-8")) if "1.28%" not in e["text"]]
    result, issues = verify.check_pdf(out / facts["verification"]["pdf"], m)
    assert {"1.28"} <= {u["number"] for u in result["unsourced"]}
    assert issues and issues[0].level == "blocker"


def test_wrong_locator_is_caught(rendered, inputs):
    _, out = rendered
    m = Manifest()
    m.entries = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    target = next(e for e in m.entries if e["kind"] == "figure" and e["formula"] is None
                  and e["locs"] and ".xlsx#" in e["locs"][0] and isinstance(e["value"], float))
    target["value"] += 1
    result, _ = verify.check_locators(m, inputs)
    assert [x["loc"] for x in result["mismatched"]] == target["locs"]


def test_two_runs_give_identical_pdfs(inputs, tmp_path):
    f1, d1 = run(inputs, "CPERS", "4Q25", tmp_path / "a", Claude(enabled=False))
    f2, d2 = run(inputs, "CPERS", "4Q25", tmp_path / "b", Claude(enabled=False))
    pdf = f1["verification"]["pdf"]
    assert (d1 / pdf).read_bytes() == (d2 / pdf).read_bytes()


def test_perturbed_inputs_render_and_verify(inputs, tmp_path):
    facts, out = run(perturbed_inputs(inputs, tmp_path / "inputs"), "CPERS", "4Q25", tmp_path / "out",
                     Claude(enabled=False))
    v = facts["verification"]
    assert v["numbers"]["unsourced"] == [] and v["locators"]["mismatched"] == []
    text = " ".join(_text(out / v["pdf"]).split())
    assert f"{NEW_FUND} was the Portfolio's largest detractor" in text


def test_slight_overflow_is_fitted_to_its_page(tmp_path):
    style = ParagraphStyle("b", fontSize=10, leading=12)
    doc = SimpleDocTemplate(str(tmp_path / "fit.pdf"))
    line = Paragraph("x", style)
    per_page = int(doc.height // line.wrap(doc.width, doc.height)[1])
    # A run 10% over one page is shrunk onto that page; a run twice the limit flows on.
    short = [Paragraph(f"line {i}", style) for i in range(int(per_page * 1.1))]
    long = [Paragraph(f"line {i}", style) for i in range(int(per_page * 2 * FIT_LIMIT))]
    fitted = fit_pages(short + [PageBreak()] + long, doc.width, doc.height)
    assert isinstance(fitted[0], KeepInFrame) and isinstance(fitted[1], PageBreak)
    assert not any(isinstance(f, KeepInFrame) for f in fitted[2:])
    doc.build(fit_pages([Paragraph(f"line {i}", style) for i in range(int(per_page * 1.1))], doc.width, doc.height))
    with pymupdf.open(tmp_path / "fit.pdf") as d:
        assert d.page_count == 1
