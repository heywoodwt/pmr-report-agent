"""The review gate: approval, finalization, reviewer corrections and the reviewer page."""

import json
import shutil

import pymupdf

from pmr import review
from pmr.llm import Claude
from pmr.pipeline import finalize, run


def _text(pdf) -> str:
    with pymupdf.open(pdf) as d:
        return " ".join(" ".join(p.get_text() for p in d).split())


def _run(inputs, out):
    # Cache only: no API calls in tests.
    return run(inputs, "CPERS", "4Q25", out, Claude(enabled=False))


def test_finalize_requires_approval_then_writes_unmarked_pdf(inputs, tmp_path):
    facts, out = _run(inputs, tmp_path)
    pdf, why = finalize(facts, inputs, out)
    assert pdf is None and "not been approved" in why
    ok, _ = review.approve(out, facts, "Test Reviewer")
    assert ok
    pdf, _ = finalize(facts, inputs, out)
    assert pdf is not None and pdf.name == "PMR_CPERS_4Q25.pdf"
    assert "DRAFT FOR REVIEW" in _text(out / facts["verification"]["pdf"])
    assert "DRAFT FOR REVIEW" not in _text(pdf)


def test_change_after_approval_blocks_finalization(inputs, tmp_path):
    facts, out = _run(inputs, tmp_path)
    review.approve(out, facts, "Test Reviewer")
    # The reviewer then removes a commentary sentence: the content changes, so approval lapses.
    sentence = facts["narrative"]["commentary"]["Redwood Logistics Trust"]["sentences"][0]["text"]
    r = review.load(out)
    r["excluded_sentences"].append(sentence)
    review.save(out, r)
    facts2, _ = _run(inputs, tmp_path)
    assert sentence not in _text(out / facts2["verification"]["pdf"])
    pdf, why = finalize(facts2, inputs, out)
    assert pdf is None and "changed after it was approved" in why


def test_changed_input_also_invalidates_approval(inputs, tmp_path):
    shutil.copytree(inputs, tmp_path / "inputs")
    facts, out = _run(tmp_path / "inputs", tmp_path / "out")
    review.approve(out, facts, "Test Reviewer")
    # Someone replaces the prior PMR's stub with nothing: the file decisions change.
    (tmp_path / "inputs" / "prior_pmr_2Q25.pdf").unlink()
    facts2, _ = _run(tmp_path / "inputs", tmp_path / "out")
    assert finalize(facts2, tmp_path / "inputs", out)[0] is None


def test_approval_is_refused_while_blockers_are_open(tmp_path):
    facts = {"issues": [{"level": "blocker", "code": "x", "message": "m", "loc": ""}]}
    ok, why = review.approve(tmp_path, facts, "Test Reviewer")
    assert not ok and "blocker" in why and not (tmp_path / "review.json").exists()


def test_chart_value_correction_replaces_the_model_reading(inputs, tmp_path):
    facts, out = _run(inputs, tmp_path)
    point = facts["narrative"]["charts"][0]["series"][0]["points"][0]
    r = review.load(out)
    r["chart_value_corrections"][point["id"]] = point["value"] + 0.1
    review.save(out, r)
    facts2, _ = _run(inputs, tmp_path)
    fixed = facts2["narrative"]["charts"][0]["series"][0]["points"][0]
    assert fixed["value"] == point["value"] + 0.1 and fixed["model_value"] == point["value"]
    assert fixed["read"] == "reviewer"


def test_review_page_shows_evidence_and_chart_images(rendered):
    facts, out = rendered
    page = (out / "review.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in page
    assert "Gateway Logistics Center" in page
    # Dropped sentences are listed exactly when the source checks dropped any.
    n = facts["narrative"]
    dropped = n["market"]["dropped"] + [d for b in [*n["commentary"].values(), *n["strategy"].values()]
                                        for d in b["dropped"]]
    assert ("Drafted but dropped" in page) == bool(dropped)
    assert json.loads((out / "review.json").read_text())["approved_hash"] is None
