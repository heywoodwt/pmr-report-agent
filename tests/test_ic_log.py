from datetime import date

from pmr import ic_log
from pmr.periods import Quarter

Q4 = Quarter.parse("4Q25")
FLASH_FUNDS = ["Existing Fund II"]


def row(no, d, fund, action, amount=10, client="ABC", category="Commitment"):
    rec = f"Approve a commitment of up to USD {amount} million to {fund}" if amount else f"Approve {fund}"
    return ic_log.Row(str(no), d, category, client, fund, rec, action, f"log.xlsx#Log!A{no}")


def analyze(rows, prior_text=""):
    return ic_log.analyze(rows, "ABC", "Alpha Beta Client", Q4, FLASH_FUNDS, prior_text)


# Golden test against the supplied log (REQ 1.18 allows this-quarter assertions in tests).
def test_cpers_4q25_golden(facts):
    cm = facts["commitments"]
    assert [(a["fund"], a["amount_musd"]) for a in cm["new_this_quarter"]] == [
        ("Northgate Logistics Partners", 40.0), ("Ridgeline Value Fund IV", 25.0)]
    assert [(a["fund"], a["reversal"]["action"]) for a in cm["reversed_this_quarter"]] == [
        ("Alder Creek Multifamily Fund III", "withdrawn"), ("Sentinel Logistics Income Fund", "lapsed")]
    assert cm["totals"]["total_committed_or_approved_usd"] == 391_814_526.42 + 65_000_000
    assert (cm["totals"]["funded_positions"], cm["totals"]["open_positions"]) == (12, 2)


def test_filters_client_category_quarter_and_action():
    act = analyze([
        row(1, date(2025, 11, 1), "New Fund A", "approved", 30),
        row(2, date(2025, 11, 2), "Other Client Fund", "approved", client="XYZ"),
        row(3, date(2025, 11, 3), "Diligence Fund", "approved", category="Due Diligence"),
        row(4, date(2026, 1, 5), "Next Quarter Fund", "approved"),
        row(5, date(2025, 11, 4), "Deferred Fund", "deferred"),
        row(6, date(2025, 10, 9), "New Fund B", "approved", 45, client="Alpha Beta Client"),
    ])
    assert [a.row.fund for a in act.new_commitments] == ["New Fund B", "New Fund A"]  # largest first
    assert not act.issues


def test_unsorted_reversal_still_pairs_with_its_approval():
    act = analyze([
        row(2, date(2025, 11, 18), "Gone Fund", "withdrawn"),
        row(1, date(2025, 8, 19), "Gone Fund", "approved", 25),
    ], prior_text="Gone Fund was approved")
    assert act.open_approvals == []
    assert [a.row.fund for a in act.reversed_this_quarter] == ["Gone Fund"]
    assert not act.issues


def test_reversal_after_quarter_end_leaves_approval_open():
    act = analyze([row(1, date(2025, 9, 1), "Late Fund", "approved", 20),
                   row(2, date(2026, 1, 10), "Late Fund", "lapsed")])
    assert [a.row.fund for a in act.open_approvals] == ["Late Fund"]
    assert act.new_commitments == [] and act.reversed_this_quarter == []


def test_approved_and_withdrawn_in_same_quarter_is_neither_new_nor_noted():
    act = analyze([row(1, date(2025, 10, 2), "Brief Fund", "approved"),
                   row(2, date(2025, 12, 1), "Brief Fund", "withdrawn")])
    assert act.new_commitments == [] and act.reversed_this_quarter == []


def test_earlier_year_approval_counts_as_open_but_not_new():
    act = analyze([row(1, date(2024, 12, 10), "Slow Close Fund", "approved", 15)])
    assert [a.row.fund for a in act.open_approvals] == ["Slow Close Fund"]
    assert act.new_commitments == []


def test_funded_approval_is_not_open():
    act = analyze([row(1, date(2025, 3, 1), "Existing Fund 2", "approved")])
    assert act.open_approvals == []


def test_unknown_action_is_flagged_and_excluded():
    act = analyze([row(1, date(2025, 11, 1), "Odd Fund", "tabled")])
    assert act.open_approvals == []
    assert [i.code for i in act.issues] == ["ic_action_unknown"]


def test_missing_amount_is_a_blocker():
    act = analyze([row(1, date(2025, 11, 1), "Vague Fund", "approved", amount=None)])
    assert [i.code for i in act.issues] == ["ic_amount_unparsed"]


def test_reversal_unknown_to_prior_report_is_flagged():
    act = analyze([row(1, date(2025, 8, 1), "Quiet Fund", "approved"),
                   row(2, date(2025, 11, 1), "Quiet Fund", "lapsed")], prior_text="")
    assert [i.code for i in act.issues] == ["reversal_not_in_prior"]
