import pytest

from pmr import analytics
from pmr import flash as fl


def test_featured_funds_and_roles_golden(facts):
    assert [(f["fund"], f["roles"]) for f in facts["featured"]] == [
        ("Redwood Logistics Trust", ["largest contributor", "second-largest individual position"]),
        ("Ironwood Value-Add Fund III", ["second-largest contributor"]),
        ("Meridian Opportunity Fund V", ["largest detractor"]),
        ("Cornerstone Core Property Fund", ["second-largest detractor", "largest individual position"]),
    ]
    assert facts["featured_notes"] == []


def test_attribution_uses_income_appreciation_fees_only(facts):
    redwood = next(r for r in facts["attribution"] if r["fund"] == "Redwood Logistics Trust")
    assert redwood["contribution"] == pytest.approx(346_623.46 + 1_081_768.36 - 61_356.27)
    assert len(redwood["locs"]) == 3


def test_compliance_golden(facts):
    assert [(r["actual_text"], r["status"]) for r in facts["compliance"]] == [
        ("36.2% (Industrial)", "In compliance"),
        ("9.6% Ex-US", "In compliance"),
        ("34.0% strategic / 37.7% tactical", "In compliance"),
        ("5.46% vs 2.66% (1-Yr)", "In compliance"),
    ]
    assert facts["benchmark"] == "CPERS Custom Benchmark"


def test_fewer_than_two_detractors_is_stated(inputs):
    f = fl.load(inputs / "flash_4Q25.xlsx", inputs)
    for fund in f.funds:
        if fund.contribution() < 0 and fund.name != "Meridian Opportunity Fund V":
            fund.cash[fl.APPREC] = fl.Fact(1_000_000.0, "test", "x")
    featured, notes = analytics.select_featured(f)
    detractors = [x.name for x in featured if any("detractor" in r for r in x.roles)]
    assert detractors == ["Meridian Opportunity Fund V"]
    assert notes and "every other fund contributed positively" in notes[0]
