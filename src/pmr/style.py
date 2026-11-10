"""The prior PMR's visual system and section layout, measured from the PDF (REQ 1.1, 1.19).

Nothing about the house style is typed into the code: fonts, sizes, colors, table fills,
margins, footer text, section order and page breaks are all read from last quarter's report.
If something cannot be measured, a neutral default is used and a warning is raised.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pmr.model import Issue
from pmr.prior_pmr import squash


@dataclass
class TextStyle:
    font: str
    size: float
    color: str  # "#rrggbb"


@dataclass
class Section:
    title: str  # exactly as listed on the contents page
    level: int  # 1 = page heading with rule, 2 = sub-heading
    new_page: bool  # starts at the top of a page in the prior PMR


@dataclass
class Style:
    page_width: float = 612.0
    page_height: float = 792.0
    margin_x: float = 56.0
    margin_top: float = 55.0
    h1: TextStyle = field(default_factory=lambda: TextStyle("Helvetica-Bold", 15.0, "#000000"))
    h2: TextStyle = field(default_factory=lambda: TextStyle("Helvetica-Bold", 10.0, "#000000"))
    body: TextStyle = field(default_factory=lambda: TextStyle("Helvetica", 9.0, "#000000"))
    body_leading: float = 13.0
    table: TextStyle = field(default_factory=lambda: TextStyle("Helvetica", 8.0, "#000000"))
    appendix_table: TextStyle = field(default_factory=lambda: TextStyle("Helvetica", 6.5, "#000000"))
    note: TextStyle = field(default_factory=lambda: TextStyle("Helvetica", 7.5, "#555555"))
    footer: TextStyle = field(default_factory=lambda: TextStyle("Helvetica", 7.0, "#666666"))
    footer_text: str = ""
    footer_x: float = 50.0  # left edge of the footer text; the page number mirrors it on the right
    footer_y: float = 763.0  # baseline, measured from the top of the page
    cover: list[tuple[str, TextStyle, float, float]] = field(default_factory=list)  # (text, style, x, baseline y)
    primary: str = "#000000"  # headings, table header fill, first chart series
    accent: str = "#888888"  # second chart series (the benchmark)
    stripe: str = "#f2f2f2"  # alternate table row fill
    grid: str = "#cccccc"  # thin table rules
    grid_width: float = 0.25
    rule_width: float = 0.8  # the rule under each page heading
    palette: list[str] = field(default_factory=list)  # chart series colors in legend order
    sections: list[Section] = field(default_factory=list)


def find_section(sections: list[Section], *keywords: str) -> Section | None:
    """The first section whose title contains every keyword (case-insensitive)."""
    return next((s for s in sections if all(k in s.title.lower() for k in keywords)), None)


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def _span_style(s) -> TextStyle:
    return TextStyle(s["font"], round(s["size"], 1), f"#{s['color']:06x}")


def _table_text(page, primary: str) -> TextStyle | None:
    """Style of the first row under the page's first header fill (a primary-colored rectangle)."""
    header = next((d["rect"] for d in page.get_drawings()
                   if d.get("fill") and not d.get("color") and _hex(d["fill"]) == primary), None)
    if header is None:
        return None
    below = [s for s in _spans(page) if header.y1 <= s["bbox"][1] <= header.y1 + 20]
    return _span_style(below[0]) if below else None


def _spans(page):
    # Every non-blank text span on the page.
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                if s["text"].strip():
                    yield s


def load(path: Path) -> tuple[Style, list[Issue]]:
    # Start from neutral defaults; every measurement below overwrites one when it succeeds.
    st, issues = Style(), []
    with pymupdf.open(path) as doc:
        pages = list(doc)
        st.page_width, st.page_height = pages[0].rect.width, pages[0].rect.height
        # Every text span in the report, tagged with its page index.
        spans = [(i, s) for i, p in enumerate(pages) for s in _spans(p)]
        # The cover is laid out differently, so it is measured separately from the body pages.
        cover = [s for i, s in spans if i == 0]
        rest = [(i, s) for i, s in spans if i > 0]

        # Headings: the two largest bold sizes after the cover; body: the commonest style of long lines.
        bold = Counter((s["size"], s["font"], s["color"]) for _, s in rest if "Bold" in s["font"])
        # Distinct bold sizes, largest first: the largest is the page heading, the next the sub-heading.
        sizes = sorted({round(k[0], 1) for k in bold}, reverse=True)
        heads = [next(s for _, s in rest if "Bold" in s["font"] and round(s["size"], 1) == z) for z in sizes[:2]]
        if len(heads) == 2:
            st.h1, st.h2 = _span_style(heads[0]), _span_style(heads[1])
            st.primary = st.h1.color
            # The highest page heading on any page marks the top of the text frame.
            st.margin_top = min(s["bbox"][1] for _, s in rest if round(s["size"], 1) == st.h1.size)
        else:
            issues.append(Issue("warning", "style_headings", "Could not measure heading styles", path.name))
        # Long spans are running text, so their commonest style is the body style.
        body = Counter((s["font"], round(s["size"], 1), s["color"]) for _, s in rest if len(s["text"]) > 60)
        if body:
            font, size, color = body.most_common(1)[0][0]
            st.body = TextStyle(font, size, f"#{color:06x}")
            # Line spacing: the commonest gap between consecutive body lines on the first content page.
            tops = sorted({round(s["bbox"][1], 1) for i, s in rest if i == 2 and round(s["size"], 1) == size})
            gaps = Counter(round(b - a, 1) for a, b in zip(tops, tops[1:]))
            st.body_leading = gaps.most_common(1)[0][0] if gaps else st.body_leading

        # Footer: the repeated text in the bottom band that is not a page number.
        # The bottom 7% of the page holds the footer; "Page N" is drawn separately on the right.
        bottom = [s for _, s in rest if s["bbox"][1] > 0.93 * st.page_height and not s["text"].startswith("Page")]
        if bottom:
            st.footer, st.footer_text = _span_style(bottom[0]), bottom[0]["text"]
            st.footer_x, st.footer_y = bottom[0]["origin"]
        # Cover: every span above the footer band, kept with its exact position.
        # Each cover line is kept with its exact position, so the cover can be redrawn span for span.
        st.cover = [(s["text"], _span_style(s), s["origin"][0], s["origin"][1])
                    for s in cover if s["bbox"][1] < 0.93 * st.page_height]

        # Note text: the commonest non-bold gray style below body size, outside the footer band.
        gray = Counter((s["font"], round(s["size"], 1), s["color"]) for _, s in rest
                       if s["size"] < st.body.size and s["bbox"][1] < 0.93 * st.page_height
                       and "Bold" not in s["font"] and s["color"] not in (0, 0xFFFFFF)
                       and f"#{s['color']:06x}" != st.primary)
        # Notes are set in italics where the prior PMR has italic gray text; chart labels share the gray but not the slant.
        italic = Counter({k: n for k, n in gray.items() if "Oblique" in k[0] or "Italic" in k[0]})
        if italic or gray:
            font, size, color = (italic or gray).most_common(1)[0][0]
            st.note = TextStyle(font, size, f"#{color:06x}")

        # Table text: the first row under the first header fill (a primary-colored rectangle).
        tables = [t for t in map(lambda p: _table_text(p, st.primary), pages[1:]) if t]
        if tables:
            st.table = tables[0]

        # Fills and rules: header fill = primary; stripe = commonest other unstroked fill.
        fills, strokes, rules = Counter(), Counter(), Counter()
        for p in pages:
            for d in p.get_drawings():
                if d.get("fill") and not d.get("color"):
                    fills[_hex(d["fill"])] += 1
                if d.get("color") and not d.get("fill") and d.get("width"):
                    (strokes if d["width"] < 0.5 else rules)[(_hex(d["color"]), round(d["width"], 2))] += 1
        # The header fill is the primary color; the other common unstroked fill is the row stripe.
        stripes = [c for c, _ in fills.most_common() if c != st.primary]
        if stripes:
            st.stripe = stripes[0]
        if strokes:
            # Thin strokes (under 0.5pt) are table grid lines; thicker primary strokes are heading rules.
            (st.grid, st.grid_width), _ = strokes.most_common(1)[0]
        prim_rules = [k for k in rules if k[0] == st.primary]
        if prim_rules:
            st.rule_width = prim_rules[0][1]
            # The heading rule starts at the left text margin, so its x position is the margin.
            st.margin_x = min(d["rect"].x0 for p in pages for d in p.get_drawings()
                              if d.get("color") and _hex(d["color"]) == st.primary)
        # Chart series colors: bordered fills (the bars) that are not the primary color.
        # Chart bars are filled and outlined; the commonest non-primary bar color is the second series color.
        bars = Counter(_hex(d["fill"]) for p in pages for d in p.get_drawings()
                       if d.get("fill") and d.get("color") and _hex(d["fill"]) != st.primary)
        if bars:
            st.accent = bars.most_common(1)[0][0]
        else:
            issues.append(Issue("warning", "style_accent", "No second chart series color found", path.name))
        # Palette: legend swatches (small filled squares) in the order they first appear.
        for p in pages:
            for d in p.get_drawings():
                r = d["rect"]
                # Legend swatches are small filled squares (4 to 10pt wide, width equal to height).
                if d.get("fill") and 4 < r.width < 10 and abs(r.width - r.height) < 0.5:
                    c = _hex(d["fill"])
                    if c not in st.palette:
                        st.palette.append(c)
        if len(st.palette) < 2:
            st.palette = [st.primary, st.accent]
            issues.append(Issue("warning", "style_palette", "No chart legend swatches found; using two colors", path.name))

        # Section order, heading levels and page breaks, from the contents page.
        st.sections = _sections(pages, st)
        # Appendix tables are set smaller: the commonest plain black table text from the appendix onward.
        appendix = find_section(st.sections, "appendix")
        # First page whose text includes the appendix heading (page 2 is the contents page, so it is skipped).
        start = next((p.number for p in pages if appendix and p.number > 1
                      and any(squash(x["text"]) == appendix.title for x in _spans(p))), None)
        if start is not None:
            sizes = Counter((x["font"], round(x["size"], 1)) for p in pages[start:] for x in _spans(p)
                            if x["color"] == 0 and "Bold" not in x["font"] and x["size"] < st.body.size)
            if sizes:
                (font, size), _ = sizes.most_common(1)[0]
                st.appendix_table = TextStyle(font, size, "#000000")
        if not st.sections:
            issues.append(Issue("blocker", "style_sections", "No numbered contents list found in the prior PMR", path.name))
    return st, issues


def _sections(pages, st: Style) -> list[Section]:
    """Section list from the contents page; level and page breaks from where each heading sits."""
    contents = None
    for p in pages:
        lines = [squash(s["text"]) for s in _spans(p)]
        numbered = [m.group(1) for m in (re.match(r"^\d+\.\s+(.+)$", t) for t in lines) if m]
        if len(numbered) >= 5:
            contents = (p.number, numbered)
            break
    if contents is None:
        return []
    out = []
    for title in contents[1]:
        level, new_page = 2, False
        for p in pages[contents[0] + 1:]:
            hits = [s for s in _spans(p) if squash(s["text"]) == title and "Bold" in s["font"]]
            if hits:
                s = hits[0]
                level = 1 if round(s["size"], 1) == st.h1.size else 2
                new_page = level == 1 and abs(s["bbox"][1] - st.margin_top) < 2
                break
        out.append(Section(title, level, new_page))
    return out


def from_json(d: dict) -> Style:
    """Rebuild a Style from its facts.json form (dataclasses.asdict output)."""
    text_fields = {k for k, v in Style().__dict__.items() if isinstance(v, TextStyle)}
    kw = {k: (TextStyle(**v) if k in text_fields else v) for k, v in d.items()}
    kw["sections"] = [Section(**s) for s in d.get("sections", [])]
    kw["cover"] = [(t, TextStyle(**ts), x, y) for t, ts, x, y in d.get("cover", [])]
    return Style(**kw)
