"""Narrative source checks: each rule is exercised with a stubbed model answer."""

import pymupdf

from pmr import deck, narrative
from pmr.llm import Claude
from pmr.managers import ManagerDoc
from pmr.pipeline import run

# A one-page manager report used by the commentary tests.
DOC = ManagerDoc("report.pdf", ["Gateway Logistics Center (Dallas) was marked up following lease-up to 97% occupancy."])


class FakeClaude:
    """Returns a fixed answer in place of the model."""

    def __init__(self, answer):
        self.answer = answer

    def json(self, task, content, schema):
        return self.answer


def _commentary(*sentences):
    return narrative.commentary(DOC, "Redwood Logistics Trust", ["largest contributor"],
                                FakeClaude({"sentences": [{"text": t, "quote": q} for t, q in sentences]}))


def test_supported_sentence_is_kept_with_its_page():
    block = _commentary(("Gateway Logistics Center in Dallas reached 97% occupancy.",
                         "lease-up to 97% occupancy"))
    assert block["status"] == "generated" and block["sentences"][0]["locs"] == ["report.pdf#p1"]


def test_quote_not_in_document_is_dropped():
    block = _commentary(("Gateway Logistics Center was sold.", "the asset was sold"))
    assert block["status"] == "rejected" and "quote not found" in block["dropped"][0]["problems"][0]


def test_number_not_in_quote_is_dropped():
    block = _commentary(("Gateway Logistics Center reached 99% occupancy.", "lease-up to 97% occupancy"))
    assert "numbers not in the quote" in block["dropped"][0]["problems"][0]


def test_reporting_year_is_allowed_and_cited_but_other_years_are_not():
    period = {"year": "2026", "locs": ["flash.xlsx#FundingStatus(Agg)"]}
    answer = FakeClaude({"sentences": [
        {"text": "During the first quarter of 2026, Gateway Logistics Center reached 97% occupancy.",
         "quote": "lease-up to 97% occupancy"},                                   # kept: the reporting year
        {"text": "During 2025, Gateway Logistics Center reached 97% occupancy.",
         "quote": "lease-up to 97% occupancy"},                                   # dropped: not the reporting year
    ]})
    block = narrative.commentary(DOC, "Redwood Logistics Trust", ["largest contributor"], answer, period)
    assert [s["text"][:30] for s in block["sentences"]] == ["During the first quarter of 20"]
    assert block["sentences"][0]["locs"] == ["report.pdf#p1", "flash.xlsx#FundingStatus(Agg)"]
    assert "['2025']" in block["dropped"][0]["problems"][0]
    # Without a reporting period, a year the quote does not print is still dropped.
    assert narrative.commentary(DOC, "Redwood Logistics Trust", ["largest contributor"], answer)["status"] == "rejected"


def test_quarter_labels_support_the_year_they_stand_for():
    assert narrative.label_years("s9:Transaction Cap Rate:25Q4") == {"2025"}
    assert narrative.label_years("s9:Return:4Q25") == {"2025"}
    assert narrative.label_years("s6:2025:Residential") == set() and narrative.label_years("s4:10-Yr") == set()
    slides = [deck.Slide(9, "Office", [])]
    charts = [{"loc": "deck.pptx#slide9/image1", "title": "Cap Rate",
               "series": [{"name": "Transaction", "points": [{"id": "s9:Transaction:25Q4", "category": "25Q4",
                                                              "value": 7.7}]}]}]
    sentence = lambda text: {"text": text, "slide_quotes": [], "chart_values": ["s9:Transaction:25Q4"],  # noqa: E731
                             "report_figures": [], "prior_report_quote": ""}
    answer = {"paragraphs": [{"sentences": [sentence("Office cap rates were 7.7% in the fourth quarter of 2025."),
                                            sentence("Office cap rates were 7.7% in the fourth quarter of 2024.")]}]}
    block = narrative.market("deck.pptx", slides, charts, {}, "", "prior.pdf#p7", FakeClaude(answer),
                             {"year": "2026", "locs": ["flash.xlsx#FundingStatus(Agg)"]})
    assert [s["text"][-8:] for p in block["paragraphs"] for s in p["sentences"]] == ["of 2025."]
    assert "['2024']" in block["dropped"][0]["problems"][0]


def test_invented_name_is_dropped_but_sentence_openers_are_not():
    block = _commentary(("During the quarter Riverside Plaza in Austin was marked up.", "was marked up"),
                        ("Following lease-up, Gateway Logistics Center was marked up.", "was marked up following lease-up"))
    assert [s["text"][:9] for s in block["sentences"]] == ["Following"]
    assert "Riverside" in block["dropped"][0]["problems"][0]


def test_market_sentences_need_cited_evidence():
    slides = [deck.Slide(4, "Return Backdrop", ["Latest quarter returns turned positive."])]
    charts = [{"loc": "deck.pptx#slide4/image1", "title": "Fund Return",
               "series": [{"name": "Fund", "points": [{"id": "s4:Fund:Qtr", "category": "Qtr", "value": -2.4}]}]}]
    figures = {"portfolio_quarter_net_return": {"text": "1.28%", "locs": ["flash.xlsx#F!C9"]}}
    sentence = lambda text, quotes=(), values=(), figs=(), prior="": {  # noqa: E731
        "text": text, "slide_quotes": [{"slide": 4, "quote": q} for q in quotes], "chart_values": list(values),
        "report_figures": list(figs), "prior_report_quote": prior}
    answer = {"paragraphs": [{"sentences": [
        sentence("Funds returned negative 2.4% while the Portfolio returned 1.28%.", values=["s4:Fund:Qtr"],
                 figs=["portfolio_quarter_net_return"]),                         # kept: both numbers cited
        sentence("Latest quarter returns turned positive.", quotes=["returns turned positive"]),  # kept
        sentence("Funds returned 3.1%.", values=["s4:Fund:Qtr"]),               # dropped: 3.1 not cited
        sentence("Values recovered.", values=["no-such-id"]),                   # dropped: unknown value id
        sentence("Last quarter was weaker.", prior="returned +0.8%"),            # dropped: not in prior text
    ]}]}
    block = narrative.market("deck.pptx", slides, charts, figures, "Funds returned +0.9%.", "prior.pdf#p7",
                             FakeClaude(answer))
    kept = [s["text"] for p in block["paragraphs"] for s in p["sentences"]]
    assert kept == ["Funds returned negative 2.4% while the Portfolio returned 1.28%.",
                    "Latest quarter returns turned positive."]
    assert len(block["dropped"]) == 3 and block["cited_values"] == ["s4:Fund:Qtr"]


def test_market_prompt_shows_chart_ids_as_printed():
    # A chart title with a dash must reach the model as the dash itself, not as "\\u2014".
    seen = []

    class Recorder(FakeClaude):
        def json(self, task, content, schema):
            seen.extend(b["text"] for b in content)
            return None

    charts = [{"loc": "deck.pptx#slide4/image1", "title": "Fund \u2014 Return",
               "series": [{"name": "Fund \u2014 Return", "points": [{"id": "s4:Fund \u2014 Return:Qtr",
                                                                    "category": "Qtr", "value": -2.4}]}]}]
    narrative.market("deck.pptx", [], charts, {}, "", "prior.pdf#p7", Recorder(None))
    prompt = "\n".join(seen)
    assert "s4:Fund \u2014 Return:Qtr" in prompt and "\\u2014" not in prompt


def test_no_key_and_no_cache_leaves_marked_placeholders(inputs, tmp_path):
    # An empty cache directory and no live calls: the run still completes with every figure.
    facts, out = run(inputs, "CPERS", "4Q25", tmp_path / "out", Claude(cache_dir=tmp_path / "empty", enabled=False))
    assert facts["narrative"]["market"]["status"] == "unavailable"
    assert any(i["code"] == "narrative_unavailable" for i in facts["issues"])
    assert facts["verification"]["numbers"]["unsourced"] == []
    with pymupdf.open(out / facts["verification"]["pdf"]) as d:
        text = " ".join(" ".join(p.get_text() for p in d).split())
    assert "produced by the narrative step, which did not run" in text
    assert "Redwood Logistics Trust was the Portfolio's largest contributor" in text


def test_committed_cache_supplies_the_narrative(rendered):
    facts, out = rendered
    assert facts["narrative"]["llm"]["calls"]["unavailable"] == 0
    with pymupdf.open(out / facts["verification"]["pdf"]) as d:
        text = " ".join(" ".join(p.get_text() for p in d).split())
    assert "Gateway Logistics Center in Dallas" in text
    assert "are not the CPERS Custom Benchmark" in text


def test_a_reviewer_edit_may_reword_but_not_add_numbers_or_names():
    s = {"text": "Bayfront Apartments was held roughly flat at 4.5% occupancy growth.",
         "quote": "Bayfront Apartments (San Diego) was held roughly flat"}
    assert narrative.edit_problems(s, "Bayfront Apartments was held roughly flat.") == []
    assert narrative.edit_problems(s, "Bayfront Apartments in San Diego held flat at 4.5%.") == []
    assert "numbers" in narrative.edit_problems(s, "Bayfront Apartments rose 6.1%.")[0]
    assert "names" in narrative.edit_problems(s, "Harbor Tower was held flat.")[0]
    assert narrative.edit_problems(s, "  ")
    # Markup would print literally in the PDF, so it is refused however it is written.
    for marked in ("<i>Bayfront Apartments</i> was held flat.", "Bayfront Apartments was held flat.<svg onload=x()>",
                   "Bayfront Apartments was held flat &amp; steady.", "Bayfront <!-- x --> Apartments was flat."):
        assert "markup" in narrative.edit_problems(s, marked)[0], marked
    # A comparison sign is not markup.
    assert narrative.edit_problems(s, "Bayfront Apartments was held roughly flat, growth < 4.5%.") == []
