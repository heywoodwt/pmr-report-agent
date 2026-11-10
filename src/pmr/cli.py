"""Command line: the only required inputs are client and quarter (REQ 1.20)."""

import argparse
import sys
import tempfile
from pathlib import Path

from pmr import archive, review
from pmr.llm import DEFAULT_CACHE, Claude
from pmr.pipeline import blockers, finalize, run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pmr", description="Build the quarterly PMR draft for one client.")
    ap.add_argument("--client", required=True, help="client short code, as defined in the prior PMR, or full name")
    ap.add_argument("--quarter", required=True, help="reporting quarter, e.g. 4Q25")
    ap.add_argument("--inputs", default="inputs", help="input drop folder (default: ./inputs)")
    ap.add_argument("--out", default="output", help="output folder (default: ./output)")
    # Review steps (optional): approve the current draft, then write the final PDF.
    ap.add_argument("--approve", metavar="REVIEWER", help="approve the current draft under this reviewer's name")
    ap.add_argument("--finalize", action="store_true", help="write the final PDF if the approved draft is unchanged")
    # Cache-only mode: replay stored model answers but never call the API.
    ap.add_argument("--no-llm", action="store_true", help="use cached model answers only; make no API calls")
    # A different cache folder, e.g. an empty one for a cold run that leaves the committed answers untouched.
    ap.add_argument("--cache", default=str(DEFAULT_CACHE), help="model answer cache folder (default: ./llm_cache)")
    args = ap.parse_args(argv)

    inputs = Path(args.inputs).resolve()
    if not any(p.suffix.lower() == ".zip" for p in inputs.rglob("*") if p.is_file()):
        return _build(args, inputs)
    # Zips are unpacked into a staged copy of the drop folder, so the folder itself is never changed.
    with tempfile.TemporaryDirectory(prefix="pmr-inputs-") as tmp:
        unpacked, skipped = archive.stage(inputs, Path(tmp))
        print(f"Unpacked {unpacked} zip archive(s) into a staged copy of {inputs}")
        for name in skipped:
            print(f"  [skip] {name}: a nested zip, or an archive that could not be unpacked")
        return _build(args, Path(tmp))


def _build(args: argparse.Namespace, inputs: Path) -> int:
    # Build facts and narrative, render the draft PDF, verify it, and write everything to <out>/<CODE>_<QUARTER>/.
    facts, out_dir = run(inputs, args.client, args.quarter, Path(args.out), Claude(Path(args.cache), enabled=not args.no_llm))
    out = out_dir / "facts.json"

    # Readable summary for the terminal.
    c = facts["client"]
    print(f"Client:  {c['name']} ({c['code']})")
    print(f"Quarter: {facts['quarter']['label']}")
    print("\nFiles:")
    # Look through the files in the facts dict and print whether each was used or skipped, along with the reason.
    for d in facts["files"]:
        print(f"  [{'use' if d['selected'] else 'skip'}] {d['file']}: {d['reason']}")
    # Analysis sections exist only if a flash and prior PMR were found. This continues from the loop above, printing the analysis results if they exist.

    if "featured" in facts:
        print("\nFeatured funds:")
        for f in facts["featured"]:
            print(f"  {f['fund']}: {', '.join(f['roles'])}")
        for note in facts["featured_notes"]:
            print(f"  note: {note}")
        cm = facts["commitments"]
        # Print the new commitments this quarter, including any reversals, and the total committed or approved amount across all positions.
        print("\nNew commitments this quarter:")
        for a in cm["new_this_quarter"] or [{"fund": "none", "amount_musd": 0}]:
            print(f"  {a['fund']}: USD {a['amount_musd']}M")
        for a in cm["reversed_this_quarter"]:
            print(f"  previously approved, {a['reversal']['action']} this quarter: {a['fund']} (USD {a['amount_musd']}M)")
        t = cm["totals"]
        print(f"  committed or approved: ${t['total_committed_or_approved_usd'] / 1e6:,.1f}M across "
              f"{t['total_positions']} positions ({t['funded_positions']} funded + {t['open_positions']} open)")
        print("\nCompliance:")
        for r in facts["compliance"]:
            print(f"  {r['guideline']}: {r['actual_text']} vs {r['limit']} -> {r['status']}")
        print(f"\nFooting: {facts['footing']['checks']} checks, {len(facts['footing']['failed'])} failed")
    if "narrative" in facts:
        # How the narrative was obtained: from the committed cache, live calls, or not at all.
        llm = facts["narrative"]["llm"]
        print(f"\nNarrative ({llm['model']}): {llm['calls']['cached']} cached, {llm['calls']['live']} live, "
              f"{llm['calls']['unavailable']} unavailable, {llm['calls']['failed']} failed")
        # Live calls only: what this run spent. A fully cached run spends nothing.
        u = llm["usage"]
        print(f"  live usage: {u['input_tokens']:,} input + {u['output_tokens']:,} output tokens, "
              f"{u['seconds']:.0f}s of API time, about USD {llm['cost_usd']:.2f}")
    if "verification" in facts:
        v = facts["verification"]
        print(f"\nReport: {out_dir / v['pdf']} ({v['numbers']['pages']} pages)")
        print(f"  Review page: {out_dir / 'review.html'}")
        print(f"  {v['numbers']['numbers_checked']} printed numbers checked, {len(v['numbers']['unsourced'])} without a source")
        print(f"  {v['locators']['checked']} workbook citations re-read, {len(v['locators']['mismatched'])} mismatched")
    print("\nIssues:")
    # Look through the issues in the facts dict and print each one, or a default message if there are no issues.
    for i in facts["issues"] or [{"level": "none", "code": "", "message": "no issues"}]:
        print(f"  {i['level'].upper()}: {i['message']}")
    print(f"\nWrote {out}")
    # If there are any blockers, print a message indicating that the draft cannot be finalized until a reviewer resolves them. Blockers mean that
    if blockers(facts):
        print(f"{len(blockers(facts))} blocker(s): the draft cannot be finalized until a reviewer resolves them.") # Count the number of blockers and print.

    # Approval: refused while blockers are open; records a hash of exactly what was reviewed.
    if args.approve:
        ok, msg = review.approve(out_dir, facts, args.approve)
        print(f"\nApproval: {msg}")
        if not ok:
            return 1
    # Finalization: refused unless approved and unchanged since; writes the final PDF.
    if args.finalize:
        pdf, msg = finalize(facts, inputs, out_dir)
        print(f"\nFinalize: {'wrote ' + str(pdf) + ' (' + msg + ')' if pdf else 'refused: ' + msg}")
        if pdf is None:
            return 1
    # A draft run always exits 0: blockers are reported, not raised, so a draft is still produced.
    return 0


if __name__ == "__main__":
    sys.exit(main())
