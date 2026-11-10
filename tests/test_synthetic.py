"""REQ 1.18: an unseen next quarter runs with no code changes.

The 1Q26 folder is generated from the 4Q25 inputs (see synthetic.py), with this program's own
4Q25 report as the prior PMR. Expected results come from the generator's arithmetic, not from
the pipeline. No model calls: the cache is an empty folder.
"""

import pytest

from synthetic import build

from pmr import pipeline
from pmr.llm import Claude


@pytest.fixture(scope="session")
def next_quarter(rendered, inputs, tmp_path_factory):
    facts, _ = rendered
    root = tmp_path_factory.mktemp("synthetic")
    # Last quarter's final report, as it would sit in the drop folder next quarter.
    prior = root / "prior.pdf"
    pipeline._render_and_verify(facts, inputs, prior, draft=False)
    expected = build(inputs, prior, root / "inputs")
    result, out_dir = pipeline.run(root / "inputs", "CPERS", "1Q26", root / "out",
                                   Claude(root / "cache", enabled=False))
    return result, expected, out_dir


def test_files_are_chosen_by_content(next_quarter):
    facts, _, _ = next_quarter
    used = sorted(d["file"] for d in facts["files"] if d["selected"])
    assert "flash_1Q26.xlsx" in used and "prior_pmr_4Q25.pdf" in used and "market_outlook_1Q26.pptx" in used
    assert {"ic_log_2025.xlsx", "ic_log_2026.xlsx"} <= set(used)
    skipped = {d["file"] for d in facts["files"] if not d["selected"]}
    # Last quarter's flash, deck and manager reports are stale decoys.
    assert {"flash_4Q25.xlsx", "market_outlook_4Q25.pptx", "prior_pmr_3Q25.pdf",
            "manager_reports/redwood_logistics_trust_4Q25.pdf"} <= skipped


def test_books_foot_and_every_number_is_sourced(next_quarter):
    facts, expected, _ = next_quarter
    assert facts["footing"]["failed"] == []
    v = facts["verification"]
    assert v["numbers"]["unsourced"] == [] and v["numbers"]["dropped"] == [] and v["locators"]["mismatched"] == []
    assert not [i for i in facts["issues"] if i["level"] == "blocker"]


def test_featured_funds_follow_the_new_rankings(next_quarter):
    facts, expected, _ = next_quarter
    featured = {f["fund"]: " ".join(f["roles"]) for f in facts["featured"]}
    assert set(featured) == set(expected["contributors"] + expected["detractors"] + expected["positions"])
    assert "largest contributor" in featured[expected["contributors"][0]]
    assert "largest detractor" in featured[expected["detractors"][0]]
    assert "largest individual position" in featured[expected["positions"][0]]
    # The featured fund with no manager report is flagged, not written up from nothing.
    assert any(expected["no_document"] in i["message"] and i["level"] == "warning" for i in facts["issues"])


def test_commitments_follow_the_new_ic_log(next_quarter):
    facts, expected, _ = next_quarter
    cm = facts["commitments"]
    assert [(a["fund"], a["amount_musd"]) for a in cm["new_this_quarter"]] == expected["new_commitments"]
    assert [a["fund"] for a in cm["reversed_this_quarter"]] == expected["reversed"]
    t = cm["totals"]
    assert t["total_positions"] == expected["positions_total"]
    assert round(t["total_committed_or_approved_usd"] / 1e6, 1) == expected["committed_musd"]


def test_compliance_names_the_new_leading_sector_and_flags_the_breach(next_quarter):
    facts, expected, _ = next_quarter
    rows = facts["compliance"]
    assert [r["actual_text"] for r in rows[:3]] == [expected["sector"], expected["ex_us"], expected["ltv"]]
    assert [r["status"] for r in rows] == expected["statuses"]
    assert any(i["code"] == "compliance_breach" for i in facts["issues"])


def test_narrative_without_a_model_is_marked_not_invented(next_quarter):
    facts, _, _ = next_quarter
    llm = facts["narrative"]["llm"]
    assert llm["calls"]["live"] == 0 and llm["calls"]["unavailable"] > 0
    assert any(i["code"] == "narrative_unavailable" for i in facts["issues"])
