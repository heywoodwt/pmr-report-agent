"""Human review: the reviewer page, reviewer corrections, approval and the finalization gate (REQ 1.15).

The workflow, all with client and quarter as the only required inputs:
  1. draft     python -m pmr --client X --quarter Q
               writes the draft PDF, facts.json, manifest.json, review.html and (once) review.json
  2. correct   edit review.json: correct a chart value the model misread, exclude a sentence or
               reword one (the rewording may not add a number or name its evidence lacks);
               the next draft applies the corrections and records them
  3. approve   python -m pmr --client X --quarter Q --approve "Reviewer Name"
               refused while any blocker is open; records a hash of exactly what was reviewed
  4. finalize  python -m pmr --client X --quarter Q --finalize
               refused unless there are no blockers and the content still matches the approved hash;
               writes the final PDF without draft marking

Approval is tied to content, not to a moment: if any input, figure or sentence changes after
approval, the hash no longer matches and the report must be reviewed again.
"""

import base64
import copy
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path

from pmr import deck

# A fresh review.json: no corrections, not approved.
BLANK = {
    "reviewer": "",
    "approved_hash": None,
    "approved_at": None,
    # {"<chart value id>": <corrected number>} for values the model misread from a chart image.
    "chart_value_corrections": {},
    # Exact sentence texts to leave out of the report even though they passed the source checks.
    "excluded_sentences": [],
    # {"<drafted sentence text>": "<reviewer's wording>"}: a rewording that keeps the sentence's evidence.
    "edited_sentences": {},
    "notes": "",
}


def load(out_dir: Path) -> dict:
    """The reviewer's file for this client and quarter, or a blank one."""
    path = out_dir / "review.json"
    if path.is_file():
        # Missing keys (an older or hand-written file) fall back to the blank defaults.
        return {**BLANK, **json.loads(path.read_text(encoding="utf-8"))}
    return copy.deepcopy(BLANK)


def save(out_dir: Path, review: dict) -> None:
    (out_dir / "review.json").write_text(json.dumps(review, indent=2), encoding="utf-8")


def content_hash(facts: dict) -> str:
    """A fingerprint of everything the report says.

    Run statistics (how many model answers came from cache versus live calls) are left out,
    because they describe how the run went, not what the report contains.
    """
    # A copy, so removing the run statistics does not change the facts being reported.
    content = copy.deepcopy(facts)
    content.get("narrative", {}).pop("llm", None)
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def blockers(facts: dict) -> list[dict]:
    return [i for i in facts.get("issues", []) if i["level"] == "blocker"]


def approve(out_dir: Path, facts: dict, reviewer: str) -> tuple[bool, str]:
    """Record approval of the current content. Refused while any blocker is open."""
    if blockers(facts):
        return False, f"{len(blockers(facts))} blocker(s) are open; resolve them before approving"
    review = load(out_dir)
    # The approval is a reviewer name, a time and a fingerprint of exactly what they saw.
    review.update({"reviewer": reviewer, "approved_hash": content_hash(facts),
                   "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    save(out_dir, review)
    return True, f"approved by {reviewer}; content hash {review['approved_hash'][:12]}"


def can_finalize(out_dir: Path, facts: dict) -> tuple[bool, str]:
    """The finalization gate: no blockers, and an approval that matches the current content."""
    if blockers(facts):
        return False, f"{len(blockers(facts))} blocker(s) are open"
    review = load(out_dir)
    if not review["approved_hash"]:
        return False, "the draft has not been approved (run with --approve \"Reviewer Name\")"
    # Any change since approval (an input, a figure, a sentence, a correction) invalidates it.
    if review["approved_hash"] != content_hash(facts):
        return False, "the report changed after it was approved; review the new draft and approve again"
    return True, f"approved by {review['reviewer']} at {review['approved_at']}"


# ---------------------------------------------------------------- reviewer page

CSS = """
body{font:14px/1.45 Helvetica,Arial,sans-serif;color:#1d1d1f;background:#fff;margin:0 auto;max-width:1100px;padding:24px 16px}
h1{color:#1b2a4a;margin:0 0 4px}h2{color:#1b2a4a;border-bottom:1px solid #1b2a4a;padding-bottom:4px;margin-top:32px}
h3{color:#1b2a4a;margin:20px 0 6px}table{border-collapse:collapse;width:100%;margin:8px 0}
th{background:#1b2a4a;color:#fff;text-align:left;padding:5px 8px;font-weight:600}
td{border-bottom:1px solid #c9d0dc;padding:5px 8px;vertical-align:top}tr:nth-child(even) td{background:#eef1f6}
.blocker{color:#9b1c1c;font-weight:600}.warning{color:#8a5a00}.ok{color:#1f6b3a;font-weight:600}
.quote{color:#555;font-style:italic}.muted{color:#666;font-size:12px}code{font-size:12px}
.status{padding:10px 12px;border-left:4px solid #1b2a4a;background:#eef1f6;margin:12px 0}
img{max-width:100%;border:1px solid #c9d0dc}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:800px){.grid{grid-template-columns:1fr}}
"""


def _e(x) -> str:
    return html.escape(str(x))


def _table(header: list[str], rows: list[list[str]]) -> str:
    # Cells are pre-escaped HTML strings so callers can add markup such as status classes.
    head = "".join(f"<th>{_e(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def page(facts: dict, inputs: Path, out_dir: Path) -> str:
    """The reviewer page: everything a person must inspect before approving, on one page."""
    review = load(out_dir)
    c, q = facts["client"], facts["quarter"]
    issues = facts.get("issues", [])
    n_block = sum(i["level"] == "blocker" for i in issues)
    # Approved means approved for this exact content, not just approved at some point.
    approved = review["approved_hash"] == content_hash(facts) if review["approved_hash"] else False
    parts = [f"<h1>Review: {_e(c['name'])} ({_e(c['code'])}), {_e(q['label'])}</h1>"]

    # 1. Where things stand.
    state = ("<span class='ok'>Approved</span> by " + _e(review["reviewer"]) if approved
             else "<span class='blocker'>Blocked</span>: resolve blockers before approving" if n_block
             else "Ready for review: inspect each section below, then approve")
    v = facts.get("verification", {})
    parts.append(f"<div class='status'>{state}<br><span class='muted'>"
                 f"{n_block} blocker(s), {len(issues) - n_block} warning(s). "
                 f"{v.get('numbers', {}).get('numbers_checked', 0)} printed numbers checked against the manifest, "
                 f"{len(v.get('numbers', {}).get('unsourced', []))} without a source; "
                 f"{v.get('locators', {}).get('checked', 0)} workbook citations re-read, "
                 f"{len(v.get('locators', {}).get('mismatched', []))} mismatched. "
                 f"Content hash <code>{content_hash(facts)[:12]}</code>.</span></div>")

    # 2. Issues: blockers stop finalization; warnings are safe to draft past but must be read.
    parts.append("<h2>Issues</h2>")
    parts.append(_table(["Level", "What", "Where"], [
        [f"<span class='{_e(i['level'])}'>{_e(i['level'])}</span>", _e(i["message"]), f"<code>{_e(i['loc'])}</code>"]
        for i in sorted(issues, key=lambda i: i["level"] != "blocker")]) if issues else "<p>None.</p>")

    # 3. The figures that decide the report's story.
    if "featured" in facts:
        parts.append("<h2>Featured funds and compliance</h2>")
        parts.append(_table(["Fund", "Roles"], [[_e(f["fund"]), _e(", ".join(f["roles"]))] for f in facts["featured"]]))
        parts.append(_table(["Guideline", "Limit", "Actual", "Status"], [
            [_e(r["guideline"]), _e(r["limit"]), _e(r["actual_text"]), _e(r["status"])] for r in facts["compliance"]]))

    # 4. Chart values: read from images, so a person must confirm them (REQ 1.16).
    n = facts.get("narrative", {})
    cited = set(n.get("market", {}).get("cited_values", []))
    corrections = review["chart_value_corrections"]
    if n.get("charts"):
        parts.append("<h2>Chart values read from the deck</h2>")
        parts.append("<p class='muted'>Each value was read from a chart image by the model. Compare it with the "
                     "image. Values marked <b>cited</b> appear in the market update. To correct one, add "
                     "<code>\"&lt;id&gt;\": &lt;value&gt;</code> under <code>chart_value_corrections</code> in "
                     "review.json and run the draft again.</p>")
        # The chart images are re-read from the deck so the reviewer sees each one beside its values.
        slides = {s.number: s for s in deck.load(inputs / facts["sources"]["deck"])}
        for ch in n["charts"]:
            img = ""
            s = slides.get(ch["slide"])
            # "...#slide4/image1" -> picture index 0 on that slide.
            k = int(ch["loc"].rsplit("image", 1)[1]) - 1
            if s and k < len(s.images):
                blob, media = s.images[k]
                img = f"<img alt='Chart from slide {ch['slide']}' src='data:{media};base64,{base64.b64encode(blob).decode()}'>"
            rows = [[_e(sr["name"]), _e(p["category"]), _e(p["value"]) +
                     (f" <span class='warning'>(reviewer: {_e(corrections[p['id']])})</span>" if p["id"] in corrections else ""),
                     _e(p["read"]), "cited" if p["id"] in cited else "", f"<code>{_e(p['id'])}</code>"]
                    for sr in ch["series"] for p in sr["points"]]
            failed = [x for x in ch.get("checks", []) if not x["ok"]]
            checks = "".join(f"<p class='warning'>Check failed: {_e(x['check'])} ({_e(x['detail'])})</p>" for x in failed)
            parts.append(f"<h3>{_e(ch.get('title') or ch['slide_title'])} <span class='muted'>{_e(ch['loc'])}</span></h3>"
                         f"<div class='grid'><div>{img}</div><div>{checks}"
                         f"{_table(['Series', 'Category', 'Value', 'Read as', 'Used', 'Id'], rows)}</div></div>")

    # 5. Narrative: every printed sentence beside its evidence; every dropped sentence with the reason.
    # One narrative block: printed sentences with their evidence, then any dropped sentences with the reason.
    def block_html(title: str, block: dict) -> str:
        sentences = block.get("sentences") or [s for p in block.get("paragraphs", []) for s in p["sentences"]]
        rows = [[_e(s["text"]) + ("<br><span class='muted'>Edited by the reviewer; drafted as: "
                                  + _e(s["edited_from"]) + "</span>" if s.get("edited_from") else ""),
                 "<span class='quote'>" + "<br>".join(_e(x) for x in (s.get("quotes") or [s.get("quote", "")])) +
                 "</span>", "<code>" + "<br>".join(_e(x) for x in s["locs"]) + "</code>"] for s in sentences]
        dropped = [[_e(d["text"]), _e("; ".join(d["problems"])), ""] for d in block.get("dropped", [])]
        out = f"<h3>{_e(title)} <span class='muted'>({_e(block['status'])})</span></h3>"
        out += _table(["Printed sentence", "Evidence", "Source"], rows) if rows else "<p>No sentence printed.</p>"
        if dropped:
            out += "<p class='warning'>Drafted but dropped by the source checks:</p>" + \
                   _table(["Drafted sentence", "Why it was dropped", ""], dropped)
        return out

    if n:
        parts.append("<h2>Narrative and its evidence</h2>")
        parts.append("<p class='muted'>To remove a printed sentence, copy its exact text into "
                     "<code>excluded_sentences</code> in review.json and run the draft again. To reword one, "
                     "add <code>\"&lt;drafted text&gt;\": \"&lt;new text&gt;\"</code> under "
                     "<code>edited_sentences</code>.</p>")
        for fund, b in n.get("commentary", {}).items():
            parts.append(block_html(f"Commentary: {fund}", b))
        for fund, b in n.get("strategy", {}).items():
            parts.append(block_html(f"Strategy: {fund}", b))
        if n.get("market", {}).get("status") != "no_source":
            parts.append(block_html("Market update", n["market"]))

    # 6. Sources: the PDF prints no citation markers, so each printed figure's source is listed here.
    manifest = out_dir / "manifest.json"
    if manifest.is_file():
        entries = [e for e in json.loads(manifest.read_text(encoding="utf-8"))
                   if e["kind"] in ("figure", "quote", "missing")]
        parts.append("<h2>Sources</h2>")
        parts.append(f"<p class='muted'>Every figure and quoted passage the report prints ({len(entries)}), with the "
                     "cells, pages or slides it comes from. A computed figure states its formula first. "
                     "Narrative sentences are listed with their evidence above.</p>")
        rows = [[_e(e["section"]), _e(e["label"]), _e(e["text"]),
                 (f"{_e(e['formula'])}<br>" if e["formula"] else "") + "<code>" + "<br>".join(_e(x) for x in e["locs"])
                 + "</code>"] for e in entries]
        parts.append("<details><summary>Show all sources</summary>"
                     + _table(["Section", "Item", "Printed", "Source"], rows) + "</details>")

    # 7. Which files were used, and why the rest were not.
    parts.append("<h2>Input files</h2>")
    parts.append(_table(["File", "Used", "Reason"], [[f"<code>{_e(d['file'])}</code>", "yes" if d["selected"] else "no",
                                                    _e(d["reason"])] for d in facts["files"]]))

    # 8. How to finish.
    parts.append("<h2>Approve and finalize</h2><p>When every section above has been checked: "
                 f"<code>python -m pmr --client {_e(c['code'])} --quarter {_e(q['code'])} --approve \"Your Name\"</code>, "
                 f"then <code>python -m pmr --client {_e(c['code'])} --quarter {_e(q['code'])} --finalize</code>. "
                 "Finalization is refused while blockers are open or if anything changed after approval.</p>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>PMR Review</title><style>{CSS}</style></head>"
            f"<body>{''.join(parts)}</body></html>")
