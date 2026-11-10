"""Number formatting, and the manifest of every number-bearing string the report prints (REQ 1.17).

The renderer never writes a number directly: it calls Manifest.fig (a sourced figure),
Manifest.text (sourced prose, such as a quoted sentence) or Manifest.fixed (layout text such as
"Page 3"). Each call returns the display string and records it with its locators, so the
rendered PDF can be checked number by number against this list (see verify.py).
"""

import re

# A number as it appears in text: digits with optional thousands commas and decimals.
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    """Every number in text, commas kept, trailing sentence punctuation dropped."""
    return [n.rstrip(",") for n in NUMBER.findall(text)]


# Display formats. Each takes a raw value and returns text.
# Whole dollars with a sign: "$1,367,036", "-$469,294" (prose style).
def usd(v: float) -> str:
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


# Millions to one decimal: "$54.4M" (market values, commitments).
def usd_m(v: float) -> str:
    return f"-${abs(v) / 1e6:,.1f}M" if v < 0 else f"${v / 1e6:,.1f}M"


# Billions to two decimals: "$0.44 billion" (portfolio-level totals in the overview).
def usd_b(v: float) -> str:
    return f"${v / 1e9:,.2f} billion"


def acct(v: float) -> str:
    # Accounting style: negatives in parentheses, no dollar sign.
    return f"({abs(v):,.0f})" if round(v) < 0 else f"{v:,.0f}"



# A value already in percent (1.284 -> "1.28%"); returns use two decimals.
def pct(v: float, nd: int = 2) -> str:
    return f"{v:.{nd}f}%"


# One decimal: LTV, sleeve shares, mix percentages.
def pct1(v: float) -> str:
    return pct(v, 1)


def frac(v: float, nd: int = 1) -> str:
    # A fraction (0.088) shown as a percentage (8.8%).
    return pct(100 * v, nd)


# Equity multiple: 1.4495 -> "1.45x".
def mult(v: float) -> str:
    return f"{v:.2f}x"


def year(v) -> str:
    return f"{int(v)}"


def count(v) -> str:
    return f"{int(v)}"


# Format names the figure tables in figures.py refer to (e.g. an appendix column "usd").
FORMATS = {"usd": usd, "usd_m": usd_m, "acct": acct, "pct": pct, "pct1": pct1, "frac": frac,
           "mult": mult, "year": year, "count": count}


def _locs(src) -> list[str]:
    # A figure carries "loc" (read) or "locs" (derived); a list is taken as locators directly.
    if isinstance(src, dict):
        return [src["loc"]] if "loc" in src else list(src.get("locs", []))
    return list(src or [])


# The record of every number-bearing string the report prints. verify.py later checks the
# rendered PDF against it, number by number, and re-reads every cited workbook cell.
class Manifest:
    def __init__(self):
        self.entries: list[dict] = []
        self.section = ""  # set by the renderer as it moves through the report

    def _add(self, text: str, kind: str, src, label: str) -> str:
        # One entry per printed string: where in the report (section, label), what kind it is
        # (figure, quote, narrative, layout, missing), the text printed, the raw value, and its sources.
        self.entries.append({
            "section": self.section, "label": label, "kind": kind, "text": text,
            "value": src.get("value") if isinstance(src, dict) else None,
            "formula": src.get("formula") if isinstance(src, dict) else None,
            "locs": _locs(src),
        })
        return text

    def fig(self, src: dict | None, fmt, label: str = "", missing: str = "n/a") -> str:
        """Format a sourced figure. A missing figure prints `missing` and is recorded as missing."""
        # A figure the inputs did not supply prints a visible marker rather than a guess,
        # and is recorded as missing so the reviewer sees it.
        if src is None or src.get("value") is None:
            return self._add(missing, "missing", src or {}, label)
        # fmt is either a format name from FORMATS or a one-off formatting function.
        f = FORMATS[fmt] if isinstance(fmt, str) else fmt
        return self._add(f(src["value"]), "figure", src, label)

    def text(self, src: dict, label: str = "") -> str:
        """Sourced prose (a quoted sentence or name) whose numbers come from its source."""
        return self._add(str(src["value"]), "quote", src, label)

    def narrative(self, sentence: dict, label: str = "") -> str:
        """A model-drafted sentence that passed the source checks, kept with its evidence quotes."""
        self._add(sentence["text"], "narrative", sentence["locs"], label)
        # The quotes the sentence was checked against travel with it, for the reviewer.
        self.entries[-1]["quotes"] = sentence.get("quotes") or [sentence.get("quote", "")]
        return sentence["text"]

    def fixed(self, text: str, label: str = "", locs: list[str] | None = None) -> str:
        """Layout text that contains numbers: page numbers, contents numbering, period labels."""
        return self._add(text, "layout", locs or [], label)

    # Every number appearing in any recorded string: the set verify.py checks the PDF against.
    def numbers(self) -> set[str]:
        return {n for e in self.entries for n in numbers_in(e["text"])}
