"""Controlled entity resolution (REQ 1.14).

Labels are reduced to a canonical key by a fixed list of rules. Two labels are the same
entity only when their keys are equal; every rule that fired is recorded so a reviewer
can see why. Near misses are reported as ambiguous, never merged.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass, field

# Trailing tokens dropped from names: "Acme LLC" == "Acme".
_LEGAL_SUFFIXES = {"llc", "lp", "llp", "inc", "ltd", "plc", "corp", "co"}
# Roman numerals -> integers: "Fund III" == "Fund 3".
_ROMAN = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15,
}
# Number words -> integers: "Fund Three" == "Fund 3".
_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def canonical(label: str) -> tuple[str, list[str]]:
    """Return (key, rules_applied) for a fund or client label."""
    rules: list[str] = []
    # Unify Unicode forms and curly apostrophes, then lowercase.
    s = unicodedata.normalize("NFKC", label).replace("’", "'").lower()
    if "&" in s:
        s = s.replace("&", " and ")
        rules.append("'&' -> 'and'")
    stripped = re.sub(r"[.']", "", s)  # "U.S." -> "us", "Employees'" -> "employees"
    stripped = re.sub(r"[^\w\s]", " ", stripped)  # "Value-Add" -> "value add"
    # Record the rule only if punctuation changed the words.
    if stripped.split() != s.split():
        rules.append("punctuation removed")
    tokens = stripped.split()

    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
        rules.append("leading 'the' removed")
    # Loop: names can carry several suffixes, e.g. "Co LLC".
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        rules.append(f"legal suffix '{tokens[-1]}' removed")
        tokens = tokens[:-1]

    out = []
    for i, t in enumerate(tokens):
        # Numerals only count as numerals after the first word ("V" in "Fund V", not a name).
        if i > 0 and t in _ROMAN:
            out.append(str(_ROMAN[t]))
            rules.append(f"roman numeral '{t}' -> {_ROMAN[t]}")
        elif i > 0 and t in _WORDS:
            out.append(str(_WORDS[t]))
            rules.append(f"number word '{t}' -> {_WORDS[t]}")
        else:
            out.append(t)
    return " ".join(out), rules


@dataclass
class Match:
    """Outcome of resolving one label against a set of known labels."""

    label: str  # the label being resolved
    status: str  # "exact" | "normalized" | "ambiguous" | "none"
    target: str | None = None  # the matched known label, if one
    candidates: list[str] = field(default_factory=list)  # possible matches when ambiguous
    rules: list[str] = field(default_factory=list)  # normalization rules that fired

    # Every field, as a dict, for facts.json.
    def to_json(self) -> dict:
        return asdict(self)


def resolve(label: str, known: list[str]) -> Match:
    """Resolve label to one of known. Conservative: anything short of one key match is not merged."""
    # Identical text: no rules needed.
    if label in known:
        return Match(label, "exact", target=label)
    key, rules = canonical(label)
    # Canonical key and rules for every known label.
    keyed = {k: canonical(k) for k in known}
    hits = sorted(k for k, (kk, _) in keyed.items() if kk == key)
    # One key match: same entity.
    if len(hits) == 1:
        return Match(label, "normalized", target=hits[0], rules=rules + keyed[hits[0]][1])
    # Several key matches: cannot pick; reviewer decides.
    if len(hits) > 1:
        return Match(label, "ambiguous", candidates=hits, rules=rules)
    # A label whose words are a subset of another's (e.g. a missing "Fund") is a near miss.
    words = set(key.split())
    near = sorted(k for k, (kk, _) in keyed.items() if words and (words < set(kk.split()) or set(kk.split()) < words))
    if near:
        return Match(label, "ambiguous", candidates=near, rules=rules)
    return Match(label, "none", rules=rules)
