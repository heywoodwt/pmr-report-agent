"""Shared types: a sourced value and a reviewer-facing issue."""

from dataclasses import dataclass
from typing import Any, Literal


# Frozen: a fact never changes after it is read.
@dataclass(frozen=True)
class Fact:
    """A value plus the exact place it was read from (REQ 1.17)."""

    value: Any
    file: str  # path relative to the inputs folder, forward slashes
    where: str  # "Sheet!B5" for workbooks, "p10" for PDF pages

    # Full locator, e.g. "book.xlsx#Sheet!F11".
    @property
    def loc(self) -> str:
        return f"{self.file}#{self.where}"

    # Shape written to facts.json.
    def to_json(self) -> dict:
        return {"value": self.value, "loc": self.loc}


@dataclass(frozen=True)
class Issue:
    """Something a reviewer must see.

    A 'warning' is safe to proceed past at draft stage; a 'blocker' means a required
    figure cannot be produced and the report cannot be finalized (REQ 1.15).
    """

    level: Literal["warning", "blocker"]
    code: str  # stable machine-readable id, e.g. "footing_failed"
    message: str  # human-readable explanation
    loc: str = ""  # where the problem is, if known

    # Shape written to facts.json.
    def to_json(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message, "loc": self.loc}
