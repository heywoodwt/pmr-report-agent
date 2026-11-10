"""Reporting periods: '4Q25'-style codes and 'Fourth Quarter 2025'-style labels."""

import re
from dataclasses import dataclass
from datetime import date, timedelta

# Index + 1 = quarter number.
_ORDINALS = ["First", "Second", "Third", "Fourth"]
# Matches "Fourth Quarter 2025" in any case.
_LABEL = re.compile(r"\b(First|Second|Third|Fourth)\s+Quarter\s+(\d{4})\b", re.IGNORECASE)


# order=True: quarters compare chronologically (year first, then q).
@dataclass(frozen=True, order=True)
class Quarter:
    year: int  # four-digit year
    q: int  # 1-4

    @classmethod
    def parse(cls, text: str) -> "Quarter":
        """Parse a run parameter such as '4Q25' or a label such as 'Fourth Quarter 2025'."""
        s = text.strip()
        # Code form: "4Q25" or "4Q2025".
        m = re.fullmatch(r"([1-4])Q(\d{2}|\d{4})", s, re.IGNORECASE)
        if m:
            year = int(m.group(2))
            # Two-digit years are 20xx.
            return cls(year + 2000 if year < 100 else year, int(m.group(1)))
        # Label form: "Fourth Quarter 2025".
        m = _LABEL.fullmatch(s)
        if m:
            return cls(int(m.group(2)), _ORDINALS.index(m.group(1).title()) + 1)
        raise ValueError(f"Unrecognized quarter {text!r}; expected a code like 4Q25")

    @classmethod
    def search(cls, text: str) -> "Quarter | None":
        """Find the first 'Fourth Quarter 2025'-style label anywhere in text."""
        m = _LABEL.search(text or "")
        return cls.parse(m.group(0)) if m else None

    # "4Q25"
    @property
    def code(self) -> str:
        return f"{self.q}Q{self.year % 100:02d}"

    # "Fourth Quarter 2025"
    @property
    def label(self) -> str:
        return f"{_ORDINALS[self.q - 1]} Quarter {self.year}"

    # First day: Jan 1, Apr 1, Jul 1 or Oct 1.
    @property
    def start(self) -> date:
        return date(self.year, 3 * self.q - 2, 1)

    # Last day: the day before the next quarter starts.
    @property
    def end(self) -> date:
        nxt = self.next().start
        return nxt - timedelta(days=1)

    # Inclusive on both ends.
    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    # Rolls 4Q into 1Q of the next year.
    def next(self) -> "Quarter":
        return Quarter(self.year + 1, 1) if self.q == 4 else Quarter(self.year, self.q + 1)

    # Rolls 1Q back to 4Q of the prior year.
    def previous(self) -> "Quarter":
        return Quarter(self.year - 1, 4) if self.q == 1 else Quarter(self.year, self.q - 1)
