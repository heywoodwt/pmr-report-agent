from pmr.discovery import discover
from pmr.periods import Quarter


def _decision(sel, name):
    return next(d for d in sel.decisions if d["file"] == name)


def test_selects_the_right_files_by_content(inputs):
    sel = discover(inputs, "CPERS", Quarter.parse("4Q25"))
    assert sel.client_name == "Cascadia Public Employees' Retirement System"
    assert sel.flash.name == "flash_4Q25.xlsx"
    assert sel.prior_pmr.name == "prior_pmr_3Q25.pdf"
    assert [p.name for p in sel.ic_logs] == ["ic_log_2025.xlsx"]
    assert sel.allocation_history.name == "allocation_history.xlsx"
    assert not [i for i in sel.issues if i.level == "blocker"]


def test_decoys_are_skipped_with_reasons(inputs):
    sel = discover(inputs, "CPERS", Quarter.parse("4Q25"))
    assert "another client" in _decision(sel, "flash_4Q25_NWPT.xlsx")["reason"]
    assert "3Q25" in _decision(sel, "flash_3Q25.xlsx")["reason"]
    assert "stub" in _decision(sel, "prior_pmr_2Q25.pdf")["reason"]
    assert all(d["reason"] for d in sel.decisions)


def test_full_name_is_accepted(inputs):
    sel = discover(inputs, "Cascadia Public Employees' Retirement System", Quarter.parse("4Q25"))
    assert sel.client_code == "CPERS" and sel.flash.name == "flash_4Q25.xlsx"


def test_unknown_client_is_a_blocker_not_a_crash(inputs):
    sel = discover(inputs, "NOPE", Quarter.parse("4Q25"))
    assert sel.flash is None
    assert any(i.code == "client_unresolved" for i in sel.issues)


def test_client_without_populated_flash_is_a_blocker(inputs):
    sel = discover(inputs, "Northwind Pension Trust", Quarter.parse("4Q25"))
    assert sel.flash is None
    assert any(i.code == "flash_missing" for i in sel.issues)


def test_an_xlsm_workbook_is_read_like_an_xlsx(inputs, tmp_path):
    import shutil

    shutil.copytree(inputs, tmp_path / "in")
    (tmp_path / "in" / "flash_4Q25.xlsx").rename(tmp_path / "in" / "flash_4Q25.xlsm")
    sel = discover(tmp_path / "in", "CPERS", Quarter.parse("4Q25"))
    assert sel.flash.name == "flash_4Q25.xlsm"
