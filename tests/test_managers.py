import shutil

from pmr import managers
from pmr.discovery import discover
from pmr.periods import Quarter

FUNDS = ["Ironwood Value-Add Fund III", "Kestrel U.S. Real Estate Fund", "Redwood Logistics Trust"]


def test_documents_match_funds_by_content(facts):
    by_fund = {f["name"]: f["manager_match"] for f in facts["report"]["funds"] if f["manager_match"]}
    # Ironwood's report names its fund only as "Ironwood Value-Add Fund 3, L.P." in the footer.
    assert "roman numeral 'iii' -> 3" in by_fund["Ironwood Value-Add Fund III"]["rules"]
    assert facts["report"]["documents"]["Northgate Logistics Partners"].endswith("northgate_logistics_partners_4Q25.pdf")
    assert any(i["code"] == "manager_doc_missing" and "Meridian" in i["message"] for i in facts["issues"])


def test_filenames_are_irrelevant(inputs, tmp_path):
    shutil.copytree(inputs, tmp_path / "inputs")
    src = tmp_path / "inputs" / "manager_reports"
    for i, p in enumerate(sorted(src.glob("*.pdf"))):
        p.rename(src / f"document_{i}.pdf")
    sel = discover(tmp_path / "inputs", "CPERS", Quarter.parse("4Q25"))
    docs = [managers.load(p, tmp_path / "inputs") for p in sel.manager_docs]
    managers.match_funds(docs, FUNDS)
    assert {d.fund for d in docs if d.fund} == set(FUNDS)


def test_unmatched_document_is_flagged_not_guessed():
    doc = managers.ManagerDoc("x.pdf", ["Quarterly letter for Some Other Fund"])
    issues = managers.match_funds([doc], FUNDS)
    assert doc.fund is None and [i.code for i in issues] == ["manager_doc_unmatched"]


def test_document_naming_two_funds_is_ambiguous():
    doc = managers.ManagerDoc("x.pdf", ["Redwood Logistics Trust and Kestrel US Real Estate Fund"])
    issues = managers.match_funds([doc], FUNDS)
    assert doc.fund is None and [i.code for i in issues] == ["manager_doc_ambiguous"]
