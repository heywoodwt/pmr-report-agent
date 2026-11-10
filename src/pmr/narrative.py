"""Narrative written by the model and accepted only when code can trace every claim (REQ 1.15-1.16).

Three kinds of prose need judgment, so a model drafts them:
  - manager commentary for each featured fund, from its manager report;
  - a strategy description for each new commitment, from its one-pager;
  - the market update, from the outlook deck's text and chart values.

The model never decides what is featured, never computes a figure and never writes a number of
its own. Each sentence it drafts must cite its evidence, and code then checks every sentence:
  1. each quote must appear verbatim in the cited document or slide;
  2. each number in the sentence must appear in a cited quote, a cited chart value or a cited
     report figure (the model may not compute spreads, changes or averages);
  3. each capitalized name must appear in the sources (no invented assets, cities or firms).
A sentence that fails any check is dropped and listed for the reviewer; it never reaches the page.
"""

import json
import re

from pmr import deck as dk
from pmr.llm import Claude
from pmr.managers import ManagerDoc
from pmr.manifest import numbers_in
from pmr.model import Issue

# An HTML or XML tag, a comment or a character reference such as &lt;.
MARKUP = re.compile(r"<\s*[/!?]?\s*[A-Za-z][^>]*>|<!--|&[#A-Za-z0-9]+;")

# Words the report itself supplies, which may be capitalized without appearing in a source.
HOUSE_WORDS = {"The", "Fund", "Manager", "Portfolio", "Quarter", "Program", "Market", "Update", "U.S.", "Its",
               "It", "This", "These", "In", "On", "At", "As", "Over", "With", "While", "Across", "Both", "A", "An"}

# Sentence-level schema shared by commentary and strategy descriptions.
SENTENCES_SCHEMA = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "quote": {"type": "string"}},
        "required": ["text", "quote"],
        "additionalProperties": False,
    }}},
    "required": ["sentences"],
    "additionalProperties": False,
}

# The market update cites slide quotes, chart value ids, report figure ids and last quarter's text.
MARKET_SCHEMA = {
    "type": "object",
    "properties": {"paragraphs": {"type": "array", "items": {
        "type": "object",
        "properties": {"sentences": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "slide_quotes": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"slide": {"type": "integer"}, "quote": {"type": "string"}},
                    "required": ["slide", "quote"],
                    "additionalProperties": False,
                }},
                "chart_values": {"type": "array", "items": {"type": "string"}},
                "report_figures": {"type": "array", "items": {"type": "string"}},
                "prior_report_quote": {"type": "string"},
            },
            "required": ["text", "slide_quotes", "chart_values", "report_figures", "prior_report_quote"],
            "additionalProperties": False,
        }}},
        "required": ["sentences"],
        "additionalProperties": False,
    }}},
    "required": ["paragraphs"],
    "additionalProperties": False,
}

# Rules shared by every drafting task.
STYLE = ("Write in plain, formal English for an institutional client report. Do not use em dashes or en "
         "dashes; use commas or semicolons. Do not compute differences, spreads, averages or changes; state "
         "only values as they appear in the sources. Do not use a number that is not in the text you cite.")


# ---------------------------------------------------------------- text checks

def norm(s: str) -> str:
    """Lowercase, straight quotes, plain hyphens, single spaces: so a quote matches its source."""
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub("[‐-―]", "-", s)  # every dash variant becomes "-"
    return " ".join(s.split()).lower()


def found(quote: str, source: str) -> bool:
    """True when the quote appears verbatim (after normalization) in the source text."""
    q = norm(quote).strip(" .")
    return bool(q) and q in norm(source)


def unknown_numbers(text: str, allowed: set[str]) -> list[str]:
    """Numbers in the sentence that no cited source contains."""
    return [n for n in numbers_in(text) if n not in allowed]


def unknown_names(text: str, source: str) -> list[str]:
    """Capitalized words (names of assets, places, firms) that the sources never mention.

    The comparison ignores case, so a common word capitalized at the start of a sentence still
    matches its lowercase use in the source; a name absent from the source fails either way.
    """
    words = []
    # Check sentence by sentence, so each sentence's opening word can be judged on its own.
    for sentence in re.split(r"(?<=[.;:])\s+", text):
        found_words = re.findall(r"\b[A-Z][A-Za-z&'.-]*[A-Za-z]", sentence)
        # A sentence's first word is capitalized by grammar, not because it is a name ("During",
        # "Looking"). It is skipped unless the next word is also capitalized, which marks a
        # multi-word name such as "Eastgate Industrial" that must be checked.
        tokens = sentence.split()
        if found_words and tokens and tokens[0].strip("\"'(").startswith(found_words[0]):
            nxt = tokens[1] if len(tokens) > 1 else ""
            if not nxt[:1].isupper():
                found_words = found_words[1:]
        words += found_words
    # Possessives ("Fund's") are checked as the bare word.
    words = [re.sub(r"'s$", "", w) for w in words]
    src = source.lower()
    return [w for w in dict.fromkeys(words) if w not in HOUSE_WORDS and w.lower() not in src]


def label_years(label: str) -> set[str]:
    """Four-digit years that quarter labels such as "25Q4" or "4Q25" stand for.

    A chart category "25Q4" supports "the fourth quarter of 2025", which numbers_in() would
    otherwise reject because "2025" is not printed in the label.
    """
    yy = re.findall(r"\b\d{2}(?=Q[1-4]\b)|(?<=\b[1-4]Q)\d{2}\b", label)
    return {f"20{y}" for y in yy}


def period_year(text: str, bad: list[str], period: dict | None) -> tuple[list[str], list[str]]:
    """Accept the reporting period's own year ("the first quarter of 2026") and cite where it is read.

    Returns the numbers still unsupported and the locators to add to the sentence.
    """
    if not period or period["year"] not in bad:
        return bad, []
    return [n for n in bad if n != period["year"]], list(period["locs"])


def chart_value_strings(v: float) -> set[str]:
    """Ways a chart value may legitimately be printed: as read, to one or two decimals, or with commas.

    Signs are dropped because numbers_in() extracts unsigned numbers ("negative 2.4%" and
    "-2.4%" both yield "2.4"); the sign is part of the wording, which the reviewer reads.
    """
    v = abs(v)
    out = {f"{v:g}", f"{v:.1f}", f"{v:.2f}"}
    if v >= 1000:
        out.add(f"{v:,.0f}")
    return out


# ---------------------------------------------------------------- per-document narrative

def _sentences_from(doc: ManagerDoc, task: str, claude: Claude, context: str, period: dict | None = None) -> dict:
    """Draft sentences about one document, then keep only those whose quote, numbers and names check out."""
    # The whole document as one text, for the quote and name checks.
    source = "\n".join(doc.pages)
    content = [{"type": "text", "text": f"<document name=\"{doc.file}\">\n{source}\n</document>"},
               {"type": "text", "text": context}]
    # None means no key and no cached answer: the caller shows a placeholder.
    answer = claude.json(task, content, SENTENCES_SCHEMA)
    if answer is None:
        return {"status": "unavailable", "source": doc.file, "sentences": [], "dropped": []}
    # Every drafted sentence is either kept (all checks passed) or dropped with its reasons.
    kept, dropped = [], []
    for s in answer["sentences"]:
        problems = []
        if not found(s["quote"], source):
            problems.append("quote not found in the document")
        # Numbers may come only from the sentence's own quote.
        # A manager-report sentence may only use numbers its own quote contains.
        bad_numbers = unknown_numbers(s["text"], set(numbers_in(s["quote"])))
        # The reporting period's year is the one number a sentence may add, cited to the flash.
        bad_numbers, period_locs = period_year(s["text"], bad_numbers, period)
        if bad_numbers:
            problems.append(f"numbers not in the quote: {bad_numbers}")
        # Names may come from the document or the context line (the fund's own name).
        bad_names = unknown_names(s["text"], source + " " + context)
        if bad_names:
            problems.append(f"names not in the document: {bad_names}")
        if problems:
            dropped.append({"text": s["text"], "quote": s["quote"], "problems": problems})
            continue
        # Locate the quote's page for the manifest.
        page = next((i + 1 for i, p in enumerate(doc.pages) if found(s["quote"], p)), 1)
        kept.append({"text": s["text"], "quote": s["quote"], "locs": [f"{doc.file}#p{page}"] + period_locs})
    return {"status": "generated" if kept else "rejected", "source": doc.file, "sentences": kept, "dropped": dropped}


COMMENTARY_TASK = (
    "Write 2 to 4 sentences of commentary on {fund} for this quarter, drawn from the manager report above. "
    "The client report already states the Fund's role, return, income, appreciation, contribution, market "
    "value and loan-to-value from the client's own data; do not repeat any of those figures. Focus on the "
    "named assets and locations that drove the quarter, leasing or valuation events, and the Manager's "
    "outlook. Refer to the fund as \"the Fund\" and to its manager as \"the Manager\". For each sentence, "
    "give a supporting quote of at most 40 words copied character for character from the document. " + STYLE
)

STRATEGY_TASK = (
    "Write 1 or 2 sentences describing the strategy of {fund}, a new commitment, drawn from the one-pager "
    "above: its sector, its style (for example core-plus or value-add) and what it will invest in. Do not "
    "mention the commitment amount, dates, targets or performance. Begin the first sentence with the fund's "
    "name. For each sentence, give a supporting quote of at most 40 words copied character for character "
    "from the document. " + STYLE
)


def commentary(doc: ManagerDoc, fund: str, roles: list[str], claude: Claude, period: dict | None = None) -> dict:
    """Manager commentary for one featured fund.

    period is {"year": "2026", "locs": [...]}: the reporting year, which a sentence may name.
    """
    context = f"The Fund is {fund}. In the client report it is described as the Portfolio's {' and '.join(roles)}."
    return _sentences_from(doc, COMMENTARY_TASK.format(fund=fund), claude, context, period)


def strategy(doc: ManagerDoc, fund: str, claude: Claude) -> dict:
    """Strategy description for one new commitment."""
    return _sentences_from(doc, STRATEGY_TASK.format(fund=fund), claude, f"The fund is {fund}.")


# ---------------------------------------------------------------- market update

MARKET_TASK = (
    "Write the Market Update section of a quarterly client report: a concise, portfolio-relevant "
    "condensation of the house-view outlook deck above, in 2 or 3 short paragraphs of 8 to 12 sentences in "
    "total. Cover the return backdrop, capital markets (cap rates and Treasury yields), the sector outlook "
    "and the risks the deck names. Reflect the quantitative signals in the chart values, citing each value "
    "you use by its id. Relate the market to the Portfolio using the report figures provided, citing each by "
    "its id; for example, compare the Portfolio's quarterly return with the market's latest-quarter return, "
    "and connect the sector outlook to the Portfolio's largest property exposures. Where last quarter's "
    "report described a market signal that has since changed, say how it changed and cite last quarter's "
    "words as prior_report_quote; otherwise leave prior_report_quote empty. Cap rates and property values "
    "move inversely: a rising cap rate means falling values. The deck's market indices are not the client's "
    "benchmark; never call them the benchmark. For every sentence list its evidence: slide_quotes copied "
    "character for character from the slide text, chart_values ids, and report_figures ids. Every sentence "
    "needs at least one piece of evidence. " + STYLE
)


def market(file: str, slides: list[dk.Slide], charts: list[dict], figures: dict[str, dict],
           prior_text: str, prior_loc: str, claude: Claude, period: dict | None = None) -> dict:
    """The market update, sentence by sentence, each traced to slide text, chart values or report figures.

    figures maps an id to {"text": display string, "locs": [...]}, e.g. the Portfolio's quarterly return.
    """
    # Every chart value the model may cite, by id.
    # Every chart value the model may cite, keyed by id, with the slide/image it came from.
    values = {p["id"]: {**p, "loc": c["loc"], "chart": c.get("title", "")} for c in charts
              for s in c["series"] for p in s["points"]}
    # Each slide's full text, so a slide quote is checked against the slide it claims to come from.
    slide_text = {s.number: " ".join([s.title] + s.lines) for s in slides}
    deck_text = "\n".join(f"[slide {n}] {t}" for n, t in slide_text.items())
    # Everything the market update may draw names from: deck text, last quarter's text, report figures.
    all_source = deck_text + " " + prior_text + " " + " ".join(v["text"] for v in figures.values())
    content = [
        {"type": "text", "text": f"<document name=\"{file}\">\n{deck_text}\n</document>"},
        # ensure_ascii=False keeps characters such as dashes in chart titles as printed. Escaped as
        # "\\u2014", the model copied the escape into the ids it cited, and those ids no longer matched.
        {"type": "text", "text": "<document name=\"chart values read from the deck's images\">\n"
                                 + json.dumps([{"id": k, "chart": v["chart"], "value": v["value"]}
                                               for k, v in values.items()], indent=1, ensure_ascii=False)
                                 + "\n</document>"},
        {"type": "text", "text": "<document name=\"report figures\">\n"
                                 + json.dumps({k: v["text"] for k, v in figures.items()}, indent=1, ensure_ascii=False)
                                 + "\n</document>"},
        {"type": "text", "text": f"<document name=\"last quarter's market update\">\n{prior_text}\n</document>"},
    ]
    # The model sees the deck text, the chart values, the report figures and last quarter's update.
    answer = claude.json(MARKET_TASK, content, MARKET_SCHEMA)
    if answer is None:
        return {"status": "unavailable", "source": file, "paragraphs": [], "dropped": [], "cited_values": []}

    # Kept paragraphs, dropped sentences, and the chart values the kept sentences cite.
    paragraphs, dropped, cited = [], [], []
    for para in answer["paragraphs"]:
        kept = []
        for s in para["sentences"]:
            # Per sentence: what failed, which numbers its evidence permits, and where that evidence is.
            problems, allowed, locs = [], set(), []
            # 1. Slide quotes must be on the slide they cite.
            for q in s["slide_quotes"]:
                if found(q["quote"], slide_text.get(q["slide"], "")):
                    allowed |= set(numbers_in(q["quote"]))
                    locs.append(f"{file}#slide{q['slide']}")
                else:
                    problems.append(f"quote not on slide {q['slide']}: {q['quote'][:60]!r}")
            # 2. Chart value ids must exist; their values may be printed.
            for vid in s["chart_values"]:
                if vid in values:
                    allowed |= chart_value_strings(values[vid]["value"])
                    # The value's own labels may be named too: a series "2025" or "10-Yr Treasury Yield".
                    allowed |= set(numbers_in(vid))
                    # A quarter label ("25Q4") supports the year it stands for ("2025").
                    allowed |= label_years(vid)
                    locs.append(values[vid]["loc"])
                else:
                    problems.append(f"unknown chart value {vid!r}")
            # 3. Report figure ids must exist; their display strings may be printed.
            for fid in s["report_figures"]:
                if fid in figures:
                    allowed |= set(numbers_in(figures[fid]["text"]))
                    locs += figures[fid]["locs"]
                else:
                    problems.append(f"unknown report figure {fid!r}")
            # 4. A quote from last quarter's report must really be in it.
            if s["prior_report_quote"]:
                if found(s["prior_report_quote"], prior_text):
                    allowed |= set(numbers_in(s["prior_report_quote"]))
                    locs.append(prior_loc)
                else:
                    problems.append("prior-report quote not found")
            # A sentence citing nothing has no evidence at all.
            if not locs:
                problems.append("no evidence cited")
            # Every number must come from the cited evidence; the model may not compute new ones.
            bad = unknown_numbers(s["text"], allowed)
            bad, period_locs = period_year(s["text"], bad, period)
            locs += period_locs
            if bad:
                problems.append(f"numbers not in the cited evidence: {bad}")
            names = unknown_names(s["text"], all_source)
            if names:
                problems.append(f"names not in the sources: {names}")
            if problems:
                dropped.append({"text": s["text"], "problems": problems})
                continue
            kept.append({"text": s["text"], "locs": list(dict.fromkeys(locs)),
                         "quotes": [q["quote"] for q in s["slide_quotes"]] + ([s["prior_report_quote"]]
                                                                               if s["prior_report_quote"] else [])})
            # Chart values a printed sentence relies on: the reviewer must confirm these first.
            cited += [v for v in s["chart_values"] if v in values]
        if kept:
            paragraphs.append({"sentences": kept})
    return {"status": "generated" if paragraphs else "rejected", "source": file, "paragraphs": paragraphs,
            "dropped": dropped, "cited_values": list(dict.fromkeys(cited))}


def edit_problems(sentence: dict, new_text: str) -> list[str]:
    """Why a reviewer's rewording of a checked sentence cannot replace it; empty when it can.

    The edit keeps the drafted sentence's evidence, so it may reword, shorten or reorder but not add
    facts: every number must already be in the drafted sentence or its quotes, and every name in them.
    """
    if not new_text.strip():
        return ["the edited sentence is empty; exclude the sentence instead"]
    # The report is plain text: a tag would print literally in the PDF, so markup is refused.
    if MARKUP.search(new_text):
        return ["the edit contains markup such as <i> or <svg>; type plain text only"]
    evidence = " ".join([sentence["text"], *(sentence.get("quotes") or [sentence.get("quote", "")])])
    problems = []
    bad = unknown_numbers(new_text, set(numbers_in(evidence)))
    if bad:
        problems.append(f"numbers not in the drafted sentence or its evidence: {bad}")
    names = unknown_names(new_text, evidence)
    if names:
        problems.append(f"names not in the drafted sentence or its evidence: {names}")
    return problems


def issues_for(name: str, block: dict) -> list[Issue]:
    """Reviewer issues for one narrative block: dropped sentences, or nothing usable at all."""
    out = []
    for d in block.get("dropped", []):
        out.append(Issue("warning", "narrative_sentence_dropped",
                         f"{name}: dropped a drafted sentence ({'; '.join(d['problems'])}): {d['text']!r}",
                         block.get("source", "")))
    if block["status"] == "rejected":
        out.append(Issue("warning", "narrative_rejected",
                         f"{name}: no drafted sentence passed the source checks; a placeholder is shown",
                         block.get("source", "")))
    return out
