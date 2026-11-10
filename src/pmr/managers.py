"""Manager reports and strategy one-pagers: which fund each document covers (REQ 1.5, 1.14-1.15).

A document is matched to a fund when the fund's canonical name appears, on word boundaries,
in the document's canonical text. The filename is never used. One fund name found means a
match; several, or none, is left for a reviewer.
"""

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pmr import entities
from pmr.model import Issue


@dataclass
class ManagerDoc:
    file: str  # path relative to inputs/
    pages: list[str]  # raw text per page
    fund: str | None = None  # the fund this document covers, once matched
    match: dict | None = None  # how it was matched: name, page and normalization rules


def load(path: Path, inputs_root: Path) -> ManagerDoc:
    with pymupdf.open(path) as doc:
        return ManagerDoc(path.relative_to(inputs_root).as_posix(), [p.get_text() for p in doc])


def match_funds(docs: list[ManagerDoc], funds: list[str]) -> list[Issue]:
    """Assign each document to the single fund whose name it contains."""
    issues = []
    # Canonical key and normalization rules for every candidate fund name, computed once.
    # Candidates are the flash's funds plus this quarter's approvals, so one-pagers for new commitments match too.
    keys = {f: entities.canonical(f) for f in funds}
    for d in docs:
        # Every fund whose name appears somewhere in this document, with the page it was found on.
        hits = []
        for fund, (key, rules) in keys.items():
            # Search page by page so the match can cite the page where the name appears.
            for i, page in enumerate(d.pages):
                # Pad with spaces so "fund 3" does not match inside "fund 30".
                if f" {key} " in f" {entities.canonical(page)[0]} ":
                    hits.append((fund, i + 1, rules))
                    break
        # Exactly one fund named: that is the fund the document covers.
        if len(hits) == 1:
            fund, page, rules = hits[0]
            d.fund = fund
            d.match = {"fund": fund, "page": page, "rules": rules + ["name found in document text"]}
        # Several funds named (a letter comparing funds, say): ambiguous, so it is not used until a reviewer assigns it.
        elif hits:
            issues.append(Issue("warning", "manager_doc_ambiguous",
                                f"{d.file} names several funds {[h[0] for h in hits]}; not used until a reviewer assigns it",
                                d.file))
        # No known fund named: the document is set aside, never guessed at from its filename.
        else:
            issues.append(Issue("warning", "manager_doc_unmatched",
                                f"{d.file} names no fund in the flash or this quarter's approvals; not used", d.file))
    # Two documents for one fund cannot both be the source.
    by_fund: dict[str, list[ManagerDoc]] = {}
    for d in docs:
        if d.fund:
            by_fund.setdefault(d.fund, []).append(d)
    for fund, ds in by_fund.items():
        if len(ds) > 1:
            issues.append(Issue("blocker", "manager_doc_duplicate",
                                f"{fund!r} has several documents {[x.file for x in ds]}; a reviewer must pick one",
                                ds[0].file))
    return issues
