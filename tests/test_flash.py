from pmr import flash as fl
from pmr.pipeline import build_facts

from perturb import NEW_FUND, perturbed_inputs


def test_flash_parses_and_foots(inputs):
    f = fl.load(inputs / "flash_4Q25.xlsx", inputs)
    assert f.client_name == "Cascadia Public Employees' Retirement System"
    assert f.quarter.code == "4Q25"
    assert f.sleeves == ["Strategic Investments", "Tactical Investments"]
    assert len(f.funds) == 12
    assert all(fund.cash and fund.returns for fund in f.funds)
    assert f.checks and all(c["ok"] for c in f.checks)
    assert not f.issues


def test_blank_flow_cells_stay_none_in_facts(inputs):
    f = fl.load(inputs / "flash_4Q25.xlsx", inputs)
    tidewater = next(x for x in f.funds if x.name.startswith("Tidewater"))
    assert tidewater.returns["Quarter NET"].value is None  # not silently 0.0


def test_locators_point_at_real_cells(inputs):
    f = fl.load(inputs / "flash_4Q25.xlsx", inputs)
    assert f.fund("Redwood Logistics Trust").cash[fl.INCOME].loc == "flash_4Q25.xlsx#CashActivity(Agg)!F11"


def test_perturbed_layout_changes_answers_not_code(inputs, tmp_path):
    facts = build_facts(perturbed_inputs(inputs, tmp_path / "inputs"), "CPERS", "4Q25")
    assert not [i for i in facts["issues"] if i["level"] == "blocker"], facts["issues"]
    assert len(facts["funds"]) == 13
    assert facts["footing"]["failed"] == []
    roles = {f["fund"]: f["roles"] for f in facts["featured"]}
    assert roles[NEW_FUND] == ["largest detractor"]
    assert roles["Meridian Opportunity Fund V"] == ["second-largest detractor"]
    assert roles["Cornerstone Core Property Fund"] == ["largest individual position"]
    assert facts["commitments"]["totals"]["total_positions"] == 15
