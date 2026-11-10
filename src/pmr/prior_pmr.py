"""Read the prior PMR: client short code, guideline limits and text for QoQ checks (REQ 1.12, 1.18)."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pmr.model import Fact
from pmr.periods import Quarter

# Cover text that marks a PDF as a PMR.
TITLE = "Performance Measurement Report"
# Header row of the compliance table, lowercased.
_GUIDELINE_HEADER = ["guideline", "limit", "actual", "status"]
# Endnote markers such as "[7]" that earlier versions of this program printed after each sourced value.
# A report it generated becomes next quarter's prior PMR, so any markers are removed on reading.
_ENDNOTE = re.compile(r"\[\d+\]")


def page_text(page, **kw) -> str:
    """A PDF page's text without endnote markers."""
    return _ENDNOTE.sub("", page.get_text("text", **kw))


# Straighten curly quotes and collapse whitespace so text matches reliably.
def squash(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("“", '"').replace("”", '"').split())


# One row of the prior PMR's compliance table.
@dataclass
class Guideline:
    name: Fact  # guideline name
    limit: Fact  # limit as written, e.g. "<=NN% / NN%"
    actual: Fact  # last quarter's actual, used only to learn which sleeve each limit belongs to
    limit_values: list[float] = field(default_factory=list)  # percentages parsed from limit


@dataclass
class PriorPMR:
    file: str  # path relative to inputs/
    quarter: Quarter | None
    pages: list[str]  # raw text per page
    guidelines: list[Guideline]

    # Whole report as one normalized string.
    @property
    def text(self) -> str:
        return squash(" ".join(self.pages))

    def client_code(self, client_name: str) -> Fact | None:
        """The short code the report defines for client_name, e.g. 'Full Name ("CODE")'."""
        # Name, then ("CODE") with 2-12 caps/digits.
        pattern = re.escape(squash(client_name)) + r'\s*\(\s*"([A-Z0-9]{2,12})"\s*\)'
        for i, page in enumerate(self.pages):
            m = re.search(pattern, squash(page))
            if m:
                return Fact(m.group(1), self.file, f"p{i + 1}")
        return None

    def section_lines(self, title: str, headings: list[str]) -> tuple[list[str], str]:
        """Text lines under a heading, up to the next heading or the end of its page, and the page locator."""
        stops = {squash(h) for h in headings}
        for i, page in enumerate(self.pages):
            lines = [squash(x) for x in page.splitlines() if squash(x)]
            if squash(title) in lines:
                out = []
                for line in lines[lines.index(squash(title)) + 1:]:
                    if line in stops:
                        break
                    out.append(line)
                return out, f"{self.file}#p{i + 1}"
        return [], ""

    def find(self, pattern: str, title: str, headings: list[str]) -> list[Fact]:
        """Regex groups found in one section's text, each as a Fact on that section's page."""
        lines, loc = self.section_lines(title, headings)
        m = re.search(pattern, " ".join(lines))
        if not m:
            return []
        file, where = loc.split("#")
        return [Fact(g, file, where) for g in m.groups()]


def cover(path: Path) -> tuple[str, int]:
    """First-page text and page count. Used by discovery."""
    with pymupdf.open(path) as doc:
        return squash(doc[0].get_text()), doc.page_count


def load(path: Path, inputs_root: Path) -> PriorPMR:
    file = path.relative_to(inputs_root).as_posix()
    with pymupdf.open(path) as doc:
        pages = [page_text(p) for p in doc]
        guidelines = _guidelines(doc, file)
    # Quarter comes from the cover page.
    return PriorPMR(file, Quarter.search(squash(pages[0])) if pages else None, pages, guidelines)


def _guidelines(doc, file: str) -> list[Guideline]:
    """Rows of the table whose header is Guideline | Limit | Actual | Status.

    find_tables gives reliable cell boxes but garbles spacing inside cells, so each cell's
    text is re-read with a clip of its box.
    """
    for pno, page in enumerate(doc):
        for table in page.find_tables().tables:
            # Re-read each cell inside its box; empty cells have no box.
            rows = [[squash(page_text(page, clip=c)) if c else "" for c in row.cells] for row in table.rows]
            if not rows or [c.lower() for c in rows[0]] != _GUIDELINE_HEADER:
                continue
            out = []
            for name, limit, actual, _status in rows[1:]:
                where = f"p{pno + 1}"
                out.append(Guideline(Fact(name, file, where), Fact(limit, file, where), Fact(actual, file, where),
                                     # Every "NN%" or "NN.N%" in the limit text.
                                     [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", limit)]))
            # First matching table only.
            return out
    return []
