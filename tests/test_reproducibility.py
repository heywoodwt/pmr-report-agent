"""REQ 1.18: same inputs give the same facts, and the source carries no input-specific values."""

import json
import re
from pathlib import Path

from pmr.pipeline import build_facts

SRC = Path(__file__).resolve().parent.parent / "src"


def test_two_runs_agree(inputs):
    a = json.dumps(build_facts(inputs, "CPERS", "4Q25"), sort_keys=True, default=str)
    b = json.dumps(build_facts(inputs, "CPERS", "4Q25"), sort_keys=True, default=str)
    assert a == b


def test_source_has_no_input_specific_values(facts):
    """No fund name, client name or code, benchmark, input filename or guideline limit in src/."""
    source = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    banned = [f["fund"] for f in facts["funds"]] + [c["fund"] for c in facts["commitments"]["open_approvals"]]
    banned += [facts["client"]["code"], facts["client"]["name"], facts["benchmark"]]
    banned += [d["file"].rsplit("/", 1)[-1] for d in facts["files"]]
    hits = [b for b in banned if b and b in source]
    assert hits == []
    # Guideline limits (40, 50, 75, 35) must come from the prior PMR, not literals in comparisons.
    assert not re.search(r"[<>]=?\s*(40|50|75|35)\b", source)


def test_pdf_layout_analysis_is_off_on_every_install():
    """pymupdf.layout turns itself on wherever numpy is installed; pmr switches it off (see pmr/__init__.py).

    The hook pmr clears must still be the one pymupdf.layout sets, or a PyMuPDF upgrade would quietly turn
    the slow analysis back on. The layout package's source is read rather than imported, because importing
    it needs numpy, which a minimal install may lack.
    """
    import importlib.util

    import pymupdf

    import pmr  # noqa: F401

    assert hasattr(pymupdf, "_get_layout") and pymupdf._get_layout is None
    spec = importlib.util.find_spec("pymupdf.layout")
    if spec is not None:
        assert "pymupdf._get_layout" in Path(spec.origin).read_text(encoding="utf-8")
