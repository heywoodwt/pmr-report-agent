"""Investment Committee log: this client's commitments for the quarter (REQ 1.6-1.7)."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from pmr import entities, xlsx
from pmr.model import Fact, Issue
from pmr.periods import Quarter

# Headers the log must have.
COLUMNS = ["IC #", "Meeting Date", "Recommendation Category", "Client", "Fund", "Recommendation", "Committee Action"]
# Only this category counts as a commitment.
COMMITMENT = "commitment"
# Committee Action values, lowercased.
APPROVED = "approved"
REVERSALS = {"withdrawn", "lapsed"}  # undo an earlier approval
NO_DECISION = {"deferred"}  # known action, no effect
# Dollar amount in the recommendation text, e.g. "USD 25 million" or "USD 25MM".
_AMOUNT = re.compile(r"USD\s*([\d,]+(?:\.\d+)?)\s*(million|mm|m)\b", re.IGNORECASE)


# One IC log row, cleaned.
@dataclass
class Row:
    ic_no: str
    meeting_date: date | None
    category: str
    client: str
    fund: str
    recommendation: str
    action: str  # lowercased
    loc: str  # "file#Sheet!A12": the row, cited by its IC # cell
    amount_loc: str = ""  # the Recommendation cell the amount is parsed from
    date_loc: str = ""  # the Meeting Date cell

    # Amount in USD millions, or None if the text has none.
    def amount_musd(self) -> float | None:
        m = _AMOUNT.search(self.recommendation)
        return float(m.group(1).replace(",", "")) if m else None


# An approval and what later happened to it.
@dataclass
class Approval:
    row: Row
    amount_musd: float | None
    reversal: Row | None = None  # the withdrawn/lapsed row that undid it, if dated by quarter-end
    in_flash: entities.Match | None = None  # match against flash funds (funded already?)

    @property
    def is_open(self) -> bool:
        """REQ 1.6: approved by quarter-end, not funded, not withdrawn or lapsed."""
        # Ambiguous matches stay open; a reviewer confirms them.
        return self.reversal is None and (self.in_flash is None or self.in_flash.status in ("none", "ambiguous"))


# Result of analyzing the log for one client and quarter.
@dataclass
class Activity:
    new_commitments: list[Approval] = field(default_factory=list)  # approved this quarter, still open
    open_approvals: list[Approval] = field(default_factory=list)  # every open approval to date
    reversed_this_quarter: list[Approval] = field(default_factory=list)  # earlier approvals undone this quarter
    issues: list[Issue] = field(default_factory=list)


# A sheet is an IC log if it has these two headers.
def is_ic_log(ws) -> bool:
    return xlsx.find_row(ws, "Committee Action", "Recommendation Category") is not None


# Read every IC log sheet from every file into Rows.
def load(paths: list[Path], inputs_root: Path) -> list[Row]:
    rows = []
    for path in paths:
        file = path.relative_to(inputs_root).as_posix()
        wb = openpyxl.load_workbook(path, data_only=True)
        for ws in wb.worksheets:
            header = xlsx.find_row(ws, "Committee Action", "Recommendation Category")
            if header is None:
                continue
            # Header text -> column index.
            cols = {xlsx.text(c.value): c.column for c in ws[header] if c.value is not None}
            missing = [c for c in COLUMNS if c not in cols]
            if missing:
                raise ValueError(f"{file}: IC log sheet {ws.title!r} lacks columns {missing}")
            for r in range(header + 1, ws.max_row + 1):
                # Cell value in row r under the named column.
                get = lambda name: ws.cell(r, cols[name]).value  # noqa: E731
                # Blank category: not a log entry.
                if get("Recommendation Category") is None:
                    continue
                d = get("Meeting Date")
                rows.append(Row(
                    ic_no=xlsx.text(get("IC #")),  # mixed int/str in the log
                    # datetime -> date; non-dates -> None.
                    meeting_date=d.date() if isinstance(d, datetime) else d if isinstance(d, date) else None,
                    category=xlsx.text(get("Recommendation Category")),
                    client=xlsx.text(get("Client")),
                    fund=xlsx.text(get("Fund")),
                    recommendation=xlsx.text(get("Recommendation")),
                    action=xlsx.text(get("Committee Action")).lower(),
                    loc=f"{file}#{ws.title.strip()}!A{r}",
                    # The amount lives in the Recommendation text; the IC # is an identifier, never a value.
                    amount_loc=f"{file}#{ws.title.strip()}!{get_column_letter(cols['Recommendation'])}{r}",
                    date_loc=f"{file}#{ws.title.strip()}!{get_column_letter(cols['Meeting Date'])}{r}",
                ))
        wb.close()
    return rows


def analyze(rows: list[Row], client_code: str, client_name: str, quarter: Quarter,
            flash_funds: list[str], prior_text: str = "") -> Activity:
    act = Activity()
    # The log names clients by code or full name.
    clients = {client_code.lower(), client_name.lower()}
    # The log is not in date order (REQ 1.7); order by date, then IC number, for determinism.
    mine = sorted((r for r in rows if r.category.lower() == COMMITMENT and r.client.lower() in clients),
                  key=lambda x: (x.meeting_date or date.max, x.ic_no))

    # Replay the log in date order.
    approvals: list[Approval] = []
    for r in mine:
        if r.meeting_date is None:
            act.issues.append(Issue("blocker", "ic_date_missing", f"IC #{r.ic_no} {r.fund!r} has no meeting date", r.loc))
            continue
        if r.meeting_date > quarter.end:
            continue  # decided after quarter-end: not part of this report
        if r.action == APPROVED:
            amount = r.amount_musd()
            if amount is None:
                act.issues.append(Issue("blocker", "ic_amount_unparsed",
                                        f"IC #{r.ic_no}: no USD amount found in {r.recommendation!r}", r.loc))
            approvals.append(Approval(r, amount))
        elif r.action in REVERSALS:
            # Pair with the most recent unreversed approval of the same fund.
            key = entities.canonical(r.fund)[0]
            target = next((a for a in reversed(approvals)
                           if a.reversal is None and entities.canonical(a.row.fund)[0] == key), None)
            if target is None:
                act.issues.append(Issue("warning", "ic_reversal_unmatched",
                                        f"IC #{r.ic_no} {r.action} {r.fund!r} matches no earlier approval", r.loc))
            else:
                target.reversal = r
        elif r.action not in NO_DECISION:
            act.issues.append(Issue("warning", "ic_action_unknown",
                                    f"IC #{r.ic_no} {r.fund!r}: Committee Action {r.action!r} is not recognized; "
                                    "excluded from totals pending review", r.loc))

    # Is each approved fund already in the flash (funded)?
    for a in approvals:
        a.in_flash = entities.resolve(a.row.fund, flash_funds)
        if a.in_flash.status == "ambiguous":
            act.issues.append(Issue("blocker", "entity_ambiguous",
                                    f"IC #{a.row.ic_no} {a.row.fund!r} may be flash fund(s) {a.in_flash.candidates}; "
                                    "a reviewer must confirm before the committed totals are final", a.row.loc))

    act.open_approvals = [a for a in approvals if a.is_open]
    # Lead with the largest new commitment (REQ 1.6).
    act.new_commitments = sorted((a for a in act.open_approvals if quarter.contains(a.row.meeting_date)),
                                 key=lambda a: (-(a.amount_musd or 0), a.row.fund))
    # Approved before this quarter, reversed during it: the report must mention it.
    for a in approvals:
        if a.reversal is not None and quarter.contains(a.reversal.meeting_date) and a.row.meeting_date < quarter.start:
            act.reversed_this_quarter.append(a)
            # Client may never have been told of the approval.
            if a.row.fund.lower() not in prior_text.lower():
                act.issues.append(Issue("warning", "reversal_not_in_prior",
                                        f"{a.row.fund!r} was {a.reversal.action} this quarter but the prior PMR "
                                        "does not mention it; confirm whether the client was told of the approval",
                                        a.reversal.loc))
    # Approved and funded in the same quarter: not a new commitment.
    for a in approvals:
        if quarter.contains(a.row.meeting_date) and a.in_flash and a.in_flash.status in ("exact", "normalized"):
            act.issues.append(Issue("warning", "approval_already_funded",
                                    f"{a.row.fund!r} was approved this quarter but already appears in the flash as "
                                    f"{a.in_flash.target!r}; treated as funded, not as a new commitment", a.row.loc))
    return act
