"""The house-view market outlook deck: slide text, and values read from its chart images (REQ 1.10, 1.16).

Slide text is read directly with python-pptx and kept with a slide locator. The deck's charts
are pictures, so their numbers exist only in pixels: each image is sent to the model, which
reports every plotted value. Code then checks those values for plausibility, and every one is
flagged for a reviewer to confirm before the report is final, because there is no source text
to check them against.
"""

from dataclasses import dataclass, field
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from pmr import llm
from pmr.model import Issue
from pmr.periods import Quarter


@dataclass
class Slide:
    number: int  # 1-based, as a reader would cite it
    title: str  # first text box on the slide
    lines: list[str]  # every other line of text (bullets and captions), in reading order
    images: list[tuple[bytes, str]] = field(default_factory=list)  # (image bytes, media type)


def quarter_of(path: Path) -> Quarter | None:
    """The reporting quarter printed on the deck's first slide. Used by discovery."""
    # Only the first slide is read: decks open with a title slide naming the period.
    prs = Presentation(str(path))
    first = prs.slides[0] if len(prs.slides) else None
    text = " ".join(s.text_frame.text for s in first.shapes if s.has_text_frame) if first else ""
    return Quarter.search(text)


def load(path: Path) -> list[Slide]:
    """Every slide's text and picture shapes, in slide order."""
    slides = []
    for n, s in enumerate(Presentation(str(path)).slides, start=1):
        # Text and pictures on this slide, in the order the shapes are stacked.
        texts, images = [], []
        for shape in s.shapes:
            if shape.has_text_frame:
                # Each paragraph is one line; bullet glyphs and spacing are trimmed.
                texts += [p.text.strip(" •") for p in shape.text_frame.paragraphs if p.text.strip()]
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                images.append((shape.image.blob, shape.image.content_type))
        # The first line on a slide is its title; the rest is content.
        slides.append(Slide(n, texts[0] if texts else "", texts[1:], images))
    return slides


# What the model must return for one chart image. Structured outputs enforce this shape.
CHART_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "unit": {"type": "string"},
        "axis_min": {"type": "number"},
        "axis_max": {"type": "number"},
        "series": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "points": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "value": {"type": "number"},
                        "read": {"type": "string", "enum": ["label", "estimate"]},
                    },
                    "required": ["category", "value", "read"],
                    "additionalProperties": False,
                }},
            },
            "required": ["name", "points"],
            "additionalProperties": False,
        }},
    },
    "required": ["title", "unit", "axis_min", "axis_max", "series"],
    "additionalProperties": False,
}

CHART_TASK = (
    "Read the chart in the image. Report its title as printed; the unit of the value axis; the lowest "
    "and highest value-axis tick labels as printed; and every plotted data point. For each point give "
    "the series name from the legend (use the chart title when there is a single unlabeled series), the "
    "category exactly as printed on the category axis, and the value. When the value is printed on the "
    "chart as a data label, use that number and set read to 'label'. Otherwise read it against the "
    "gridlines to the precision the axis allows and set read to 'estimate'. Report only points that are "
    "plotted; do not interpolate, extend or round series you cannot see."
)


def read_charts(file: str, slides: list[Slide], claude: llm.Claude) -> tuple[list[dict], list[Issue]]:
    """Values from every chart image, each with a slide/image locator and plausibility checks."""
    charts, issues = [], []
    for s in slides:
        for k, (blob, media_type) in enumerate(s.images, start=1):
            # Locator a reviewer can follow: the deck file, the slide, and which picture on it.
            loc = f"{file}#slide{s.number}/image{k}"
            chart_id = f"slide{s.number}-image{k}"
            # The slide title and caption give context but are not where the numbers come from.
            # Slide text is wrapped as a document so the model treats it as material, not instructions.
            context = f"<document>Slide {s.number} title: {s.title}\nSlide text: {' | '.join(s.lines)}</document>"
            answer = claude.json(CHART_TASK, [llm.image_block(blob, media_type), {"type": "text", "text": context}],
                                 CHART_SCHEMA)
            if answer is None:
                # No key and no cached answer: the chart is listed but carries no values.
                charts.append({"id": chart_id, "loc": loc, "slide": s.number, "slide_title": s.title,
                               "status": "unavailable", "series": [], "checks": []})
                issues.append(Issue("warning", "chart_unread", f"Chart on slide {s.number} was not read "
                                    "(no model access and no cached answer); the market update omits it", loc))
                continue
            chart = {"id": chart_id, "loc": loc, "slide": s.number, "slide_title": s.title, "status": "read",
                     "title": answer["title"], "unit": answer["unit"],
                     "axis": [answer["axis_min"], answer["axis_max"]], "series": []}
            # Give every value a stable id so the narrative can cite it and a reviewer can correct it.
            for series in answer["series"]:
                points = [{**p, "id": f"{chart_id}:{series['name']}:{p['category']}"} for p in series["points"]]
                chart["series"].append({"name": series["name"], "points": points})
            chart["checks"] = check_chart(chart)
            for c in chart["checks"]:
                if not c["ok"]:
                    # A draft-stage warning: every chart value already needs reviewer confirmation
                    # before the report is final, and this tells the reviewer where to look first.
                    issues.append(Issue("warning", "chart_value_implausible",
                                        f"Slide {s.number} chart: {CHECK_PROBLEMS[c['check']]} ({c['detail']}); "
                                        "compare the values with the chart image", loc))
            charts.append(chart)
    return charts, issues


# What a failed check means, in the words shown to the reviewer.
CHECK_PROBLEMS = {
    "values within axis range": "some values fall outside the chart's printed axis",
    "series share categories": "the series list different categories, so values may be paired with the wrong bar",
    "chart has values": "no values were read from the chart",
}


def check_chart(chart: dict) -> list[dict]:
    """Plausibility checks in code (REQ 1.16): values inside the axis, series that line up."""
    checks = []
    # The axis range the model read from the tick labels.
    lo, hi = chart["axis"]
    # 1. Every value must sit inside the printed axis range (a small margin allows for labels
    #    printed just past the last tick).
    margin = 0.05 * (hi - lo) if hi > lo else 0
    outside = [f"{p['category']}={p['value']}" for s in chart["series"] for p in s["points"]
               if not lo - margin <= p["value"] <= hi + margin]
    checks.append({"check": "values within axis range", "ok": not outside and hi > lo,
                   "detail": f"axis {lo} to {hi}" + (f"; outside: {outside}" if outside else "")})
    # 2. Series on one chart share a category axis, so they should report the same categories.
    cats = [tuple(p["category"] for p in s["points"]) for s in chart["series"]]
    checks.append({"check": "series share categories", "ok": len(set(cats)) <= 1,
                   "detail": f"{len(chart['series'])} series"})
    # 3. A chart with no points was not read at all.
    checks.append({"check": "chart has values", "ok": any(s["points"] for s in chart["series"]),
                   "detail": f"{sum(len(s['points']) for s in chart['series'])} values"})
    return checks
