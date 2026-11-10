"""
Find this client's and quarter's files by reading them, never by filename (REQ 1.20).

Every file in the inputs folder gets a recorded decision, selected or skipped with a reason,
so a reviewer can see why the report used what it used.
"""

from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from pmr import flash as fl
from pmr import deck, ic_log, prior_pmr, xlsx
from pmr.model import Issue
from pmr.periods import Quarter

# Headings on the first page of a flash rendered as PDF.
FLASH_PDF_HEADINGS = ("Portfolio Composition", "Performance Summary")


# Comparable client name: normalized quotes/spaces, no " Portfolio", lowercase.
def client_key(name: str) -> str:
    return fl.strip_portfolio(prior_pmr.squash(name)).lower()


# The files chosen for one run, plus why every file was used or skipped.
@dataclass
class Selection:
    client_code: str | None = None  # short code, e.g. "ABC"
    client_name: str | None = None  # full name from the flash title
    code_loc: str = ""  # where the code was defined
    quarter: Quarter | None = None
    flash: Path | None = None
    prior_pmr: Path | None = None
    ic_logs: list[Path] = field(default_factory=list)
    allocation_history: Path | None = None
    manager_docs: list[Path] = field(default_factory=list)  # this quarter's fund documents; matched to funds later
    deck: Path | None = None  # the house-view market outlook deck for this quarter
    decisions: list[dict] = field(default_factory=list)  # one per input file
    issues: list[Issue] = field(default_factory=list)

    # Record a used/skipped decision for one file.
    def decide(self, path: Path, root: Path, kind: str, selected: bool, reason: str):
        self.decisions.append({"file": path.relative_to(root).as_posix(), "kind": kind,
                               "selected": selected, "reason": reason})


# Identify a workbook by its sheets and headers.
def _classify_xlsx(path: Path) -> str:
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        if fl.FUNDING in wb.sheetnames:
            return "flash"
        for ws in wb.worksheets:
            if ic_log.is_ic_log(ws):
                return "ic_log"
            if xlsx.find_row(ws, "Quarter", "Target Allocation (USD MM)", "Net Asset Value (USD MM)"):
                return "allocation_history"
        return "unknown"
    finally:
        wb.close()


def discover(inputs: Path, client: str, quarter: Quarter) -> Selection:
    sel = Selection(quarter=quarter)
    # Every file under inputs/, skipping hidden files and Excel lock files.
    files = sorted(p for p in inputs.rglob("*") if p.is_file() and not p.name.startswith((".", "~$")))

    # Flashes and PMRs need the client resolved first, so collect them; decide the rest now.
    flashes, pmrs, other = [], [], []
    for p in files:
        suffix = p.suffix.lower()
        if suffix in (".xlsx", ".xlsm"):
            kind = _classify_xlsx(p)
            if kind == "flash":
                # (path, title, quarter, has_data)
                flashes.append((p, *fl.title_and_quarter(p)))
            elif kind == "ic_log":
                # IC logs are firm-wide: always used.
                sel.ic_logs.append(p)
                sel.decide(p, inputs, kind, True, "firm-wide IC log; filtered to this client and period later")
            elif kind == "allocation_history":
                # Keep the first; warn on extras.
                if sel.allocation_history is None:
                    sel.allocation_history = p
                    sel.decide(p, inputs, kind, True, "target allocation vs NAV history")
                else:
                    sel.decide(p, inputs, kind, False, "a second allocation history; first one kept")
                    sel.issues.append(Issue("warning", "duplicate_allocation_history",
                                            f"More than one allocation history found; using {sel.allocation_history.name}"))
            else:
                sel.decide(p, inputs, "unknown", False, "workbook matches no known input layout")
        elif suffix == ".pdf":
            # A PDF is a PMR if its cover carries the report title.
            text, pages = prior_pmr.cover(p)
            if prior_pmr.TITLE.lower() in text.lower():
                pmrs.append((p, text, pages))
            elif all(h.lower() in text.lower() for h in FLASH_PDF_HEADINGS):
                # The appendix is rebuilt from the workbook in house style, so the PDF rendering is not needed.
                sel.decide(p, inputs, "flash_pdf", False, "PDF rendering of a flash; the appendix is rebuilt from the workbook")
            elif Quarter.search(text) == quarter:
                # Any other PDF dated this quarter may be a manager report or one-pager.
                sel.manager_docs.append(p)
            else:
                pq = Quarter.search(text)
                sel.decide(p, inputs, "manager_doc", False, f"document for {pq.code if pq else 'an unknown quarter'}, not {quarter.code}")
        elif suffix == ".pptx":
            # A slide deck is the market outlook when its first slide names this quarter.
            dq = deck.quarter_of(p)
            if dq == quarter and sel.deck is None:
                sel.deck = p
                sel.decide(p, inputs, "outlook_deck", True, f"house-view market outlook for {quarter.code}")
            elif dq == quarter:
                # Two decks for the same quarter: keep the first, tell the reviewer.
                sel.decide(p, inputs, "outlook_deck", False, "a second outlook deck; first one kept")
                sel.issues.append(Issue("warning", "duplicate_deck",
                                        f"More than one outlook deck for {quarter.code}; using {sel.deck.name}"))
            else:
                sel.decide(p, inputs, "outlook_deck", False,
                           f"deck for {dq.code if dq else 'an unknown quarter'}, not {quarter.code}")
        else:
            other.append(p)

    _pick_client(sel, inputs, client, flashes, pmrs)
    # Anything else (images, text files, other formats) matches no known input.
    for p in other:
        sel.decide(p, inputs, "unknown", False, "file type matches no known input")
    if sel.deck is None:
        # REQ 1.15: the market update can still be drafted as a placeholder; the reviewer must know.
        sel.issues.append(Issue("warning", "deck_missing",
                                f"No market outlook deck for {quarter.code}. The Market Update section will show "
                                f"placeholder text. Upload the {quarter.code} outlook deck, or confirm the placeholder "
                                "is fine for this draft."))
    if not sel.ic_logs:
        sel.issues.append(Issue("blocker", "ic_log_missing", "No IC log found; new commitments cannot be determined"))
    if sel.allocation_history is None:
        sel.issues.append(Issue("warning", "allocation_history_missing", "No allocation history workbook found"))
    # Stable order for reproducible output.
    sel.decisions.sort(key=lambda d: d["file"])
    return sel


def inventory(inputs: Path) -> list[dict]:
    """The clients and quarters the drop folder can report on: one entry per client with a filled-in flash.

    Each entry is {"name", "code", "quarters"}, quarters newest first. The code comes from a prior PMR's
    definition, as in discover; it is None when no PMR defines one. Used to tell a preparer which
    client and quarter to pick before a run is queued.
    """
    files = sorted(p for p in inputs.rglob("*") if p.is_file() and not p.name.startswith((".", "~$")))
    by_name: dict[str, set[Quarter]] = {}
    for p in files:
        if p.suffix.lower() in (".xlsx", ".xlsm") and _classify_xlsx(p) == "flash":
            title, fq, has_data = fl.title_and_quarter(p)
            if title and fq and has_data:
                by_name.setdefault(title, set()).add(fq)
    pmrs = []
    for p in files:
        if p.suffix.lower() == ".pdf":
            text, pages = prior_pmr.cover(p)
            if pages > 1 and prior_pmr.TITLE.lower() in text.lower():
                pmrs.append(prior_pmr.load(p, inputs))
    out = []
    for name in sorted(by_name):
        code = next((c.value for doc in pmrs if (c := doc.client_code(name))), None)
        quarters = sorted(by_name[name], key=lambda q: (q.year, q.q), reverse=True)
        out.append({"name": name, "code": code, "quarters": [q.code for q in quarters]})
    return out


def _pick_client(sel: Selection, inputs: Path, client: str, flashes, pmrs) -> None:
    q = sel.quarter
    # The short code is defined in a prior PMR as 'Full Name ("CODE")'; resolve it from there.
    # Full-load only multi-page PMRs; stubs carry no definition.
    loaded = {p: prior_pmr.load(p, inputs) for p, _, pages in pmrs if pages > 1}
    # Client names known from flash titles.
    names = sorted({title for _, title, _, _ in flashes if title})
    for name in names:
        # --client given as a full name.
        if client_key(client) == client_key(name):
            sel.client_name = name
        # --client given as a short code some PMR defines for this name.
        for doc in loaded.values():
            code = doc.client_code(name)
            if code and code.value.lower() == client.strip().lower():
                sel.client_name, sel.client_code, sel.code_loc = name, code.value, code.loc
    # Matched by name: still look up the code.
    if sel.client_name and sel.client_code is None:
        for doc in loaded.values():
            code = doc.client_code(sel.client_name)
            if code:
                sel.client_code, sel.code_loc = code.value, code.loc
    if sel.client_name is None:
        sel.issues.append(Issue("blocker", "client_unresolved",
                                f"{client!r} is neither a client name in any flash nor a short code defined in a prior PMR"))

    # Flash: exactly one for this client and quarter, with data.
    for p, title, fq, has_data in flashes:
        if sel.client_name is None or client_key(title) != client_key(sel.client_name):
            sel.decide(p, inputs, "flash", False, f"flash for another client ({title!r})")
        elif fq != q:
            sel.decide(p, inputs, "flash", False, f"flash for {fq.code if fq else 'unknown quarter'}, not {q.code}")
        elif not has_data:
            sel.decide(p, inputs, "flash", False, "flash states no data is available for this period/client")
        elif sel.flash is not None:
            # Two valid matches: cannot choose safely.
            sel.decide(p, inputs, "flash", False, "a second matching flash; first one kept")
            sel.issues.append(Issue("blocker", "flash_ambiguous",
                                    f"Two flashes match {sel.client_name} {q.code}: {sel.flash.name}, {p.name}"))
        else:
            sel.flash = p
            sel.decide(p, inputs, "flash", True, f"flash for {title!r}, {q.code}")
    if sel.flash is None and sel.client_name:
        sel.issues.append(Issue("blocker", "flash_missing",
                                f"Missing {q.code} flash report. None of the uploaded files is a filled-in flash "
                                f"report for {sel.client_name} for {q.code}, so performance figures cannot be built "
                                "or checked. Upload the flash report, or check the Inputs tab to see whether it was "
                                "skipped because of its period or content."))

    # Prior PMR: the latest populated report for this client from an earlier quarter.
    candidates = []
    for p, text, pages in pmrs:
        pq = Quarter.search(text)
        # Client name appears on the cover.
        mine = sel.client_name is not None and client_key(sel.client_name) in client_key(text)
        if not mine:
            sel.decide(p, inputs, "prior_pmr", False, "PMR for another client")
        elif pq is None or pq >= q:
            sel.decide(p, inputs, "prior_pmr", False, f"PMR for {pq.code if pq else 'unknown quarter'}, not before {q.code}")
        elif pages <= 1:
            sel.decide(p, inputs, "prior_pmr", False, f"{pq.code} PMR is a one-page stub with no report content")
        else:
            candidates.append((pq, p))
    # Oldest first; the last one wins.
    candidates.sort()
    for pq, p in candidates[:-1]:
        sel.decide(p, inputs, "prior_pmr", False, f"older than the {candidates[-1][0].code} PMR")
    if candidates:
        pq, p = candidates[-1]
        sel.prior_pmr = p
        sel.decide(p, inputs, "prior_pmr", True, f"latest populated PMR before {q.code} ({pq.code})")
        # A gap quarter is allowed but flagged.
        if pq != q.previous():
            gap = (q.previous().year * 4 + q.previous().q) - (pq.year * 4 + pq.q)
            behind = "a year" if gap == 4 else f"{gap} quarter{'s' if gap != 1 else ''}"
            sel.issues.append(Issue("warning", "prior_pmr_not_previous_quarter",
                                    f"Prior-quarter report is out of date. The latest Performance Measurement Report "
                                    f"(PMR) on file is {pq.code}, but {q.previous().code} is expected. "
                                    f"Quarter-over-quarter comparisons use {pq.code}, {behind} earlier than they "
                                    f"should. Upload the {q.previous().code} PMR before you rely on these "
                                    "comparisons."))
    elif sel.client_name:
        sel.issues.append(Issue("blocker", "prior_pmr_missing",
                                "No populated prior PMR; guideline limits and the format baseline are unavailable"))
