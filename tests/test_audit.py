import json
import subprocess
import sys
from pathlib import Path

AUDIT = Path(__file__).parent / "audit" / "audit_report.py"


def _audit(inputs, pdf, manifest) -> subprocess.CompletedProcess:
    # -I: the audit runs isolated, so it cannot import the pmr package by accident.
    return subprocess.run([sys.executable, "-I", str(AUDIT), "--inputs", str(inputs), "--pdf", str(pdf),
                           "--manifest", str(manifest)], capture_output=True, text=True)


def test_independent_audit_passes(rendered, inputs):
    facts, out = rendered
    r = _audit(inputs, out / facts["verification"]["pdf"], out / "manifest.json")
    assert r.returncode == 0, r.stdout
    assert ", 0 failed" in r.stdout


def test_independent_audit_catches_a_bad_quote(rendered, inputs, tmp_path):
    facts, out = rendered
    entries = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    e = next(e for e in entries if e["kind"] == "narrative" and e.get("quotes"))
    e["quotes"][0] += " This clause is not in the source."
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps(entries), encoding="utf-8")
    r = _audit(inputs, out / facts["verification"]["pdf"], bad)
    assert r.returncode == 1 and "quote not found" in r.stdout
