"""The outlook deck: slide text, discovery, and chart-value checks. The model is stubbed out."""

from pmr import deck
from pmr.discovery import discover
from pmr.periods import Quarter


class FakeClaude:
    """Stands in for llm.Claude: returns a fixed chart reading, or None for 'no access'."""

    def __init__(self, answer):
        self.answer = answer

    def json(self, task, content, schema):
        return self.answer


def test_deck_is_discovered_by_its_quarter(inputs):
    sel = discover(inputs, "CPERS", Quarter.parse("4Q25"))
    assert sel.deck.name == "market_outlook_4Q25.pptx"
    # The same deck is skipped for another quarter, with the reason.
    other = discover(inputs, "CPERS", Quarter.parse("1Q26"))
    assert other.deck is None
    assert any(d["file"].endswith(".pptx") and "not 1Q26" in d["reason"] for d in other.decisions)


def test_slides_keep_titles_text_and_images(inputs):
    slides = deck.load(inputs / "market_outlook_4Q25.pptx")
    assert len(slides) == 13
    assert slides[3].title.startswith("Return Backdrop") and len(slides[3].images) == 1
    assert sum(len(s.images) for s in slides) == 7
    assert any("industrial" in line.lower() for line in slides[1].lines)


def test_chart_values_get_ids_locators_and_checks(inputs):
    slides = [deck.Slide(4, "Returns", ["caption"], [(b"png", "image/png")])]
    reading = {"title": "Fund Return", "unit": "%", "axis_min": -3, "axis_max": 4,
               "series": [{"name": "Fund", "points": [{"category": "1-Yr", "value": 3.3, "read": "label"}]}]}
    charts, issues = deck.read_charts("deck.pptx", slides, FakeClaude(reading))
    point = charts[0]["series"][0]["points"][0]
    assert charts[0]["loc"] == "deck.pptx#slide4/image1"
    assert point["id"] == "slide4-image1:Fund:1-Yr"
    assert all(c["ok"] for c in charts[0]["checks"]) and not issues


def test_value_outside_axis_and_missing_category_are_flagged():
    chart = {"axis": [0, 10], "series": [
        {"name": "A", "points": [{"category": "x", "value": 25}, {"category": "y", "value": 1}]},
        {"name": "B", "points": [{"category": "x", "value": 2}]}]}
    failed = {c["check"] for c in deck.check_chart(chart) if not c["ok"]}
    assert failed == {"values within axis range", "series share categories"}


def test_unread_chart_is_a_warning_not_a_crash():
    slides = [deck.Slide(5, "Cap rates", [], [(b"png", "image/png")])]
    charts, issues = deck.read_charts("deck.pptx", slides, FakeClaude(None))
    assert charts[0]["status"] == "unavailable"
    assert [(i.level, i.code) for i in issues] == [("warning", "chart_unread")]
