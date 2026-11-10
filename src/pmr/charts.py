"""Report exhibits drawn with reportlab.graphics, the same toolkit the prior PMR's charts use.

Charts are vector drawings, so their labels are real PDF text. Every label with a number is
registered in the manifest: data labels with their source, axis ticks as layout.
"""

import math

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib.colors import HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth

from pmr.manifest import Manifest
from pmr.style import Style


def ticks(lo: float, hi: float, n: int = 5) -> tuple[float, float, float]:
    """A 'nice' axis (min, max, step) covering lo..hi with about n steps, always including 0 for bars."""
    # Width of the data range; falls back to a nonzero span when every value is equal.
    span = hi - lo or abs(hi) or 1.0
    # The step that would give exactly n intervals, before rounding to a 'nice' number.
    raw = span / n
    # Order of magnitude of that step (0.1, 1, 10, ...).
    mag = 10 ** math.floor(math.log10(raw))
    # Smallest 'nice' multiple (1, 2, 2.5, 5, 10 times the magnitude) that is wide enough.
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    # Round the ends outward to whole steps so every bar fits inside the axis.
    return math.floor(lo / step) * step, math.ceil(hi / step) * step, step


def _axis_labels(m: Manifest, lo: float, hi: float, step: float, label: str) -> None:
    # Register each tick the axis will print (reportlab prints them with %g-style formatting).
    v = lo
    while v <= hi + step / 2:
        m.fixed(f"{v:g}", label)
        v += step


def _legend(st: Style, d: Drawing, items: list[tuple[str, str]], right: float, top: float,
            vertical: bool = False, x: float = 0) -> None:
    """Square swatch plus label per series: a right-aligned row, or a column starting at x."""
    size, gap = 7.5, 14
    # Measured width of each entry: swatch, gap and label text.
    widths = [stringWidth(label, st.body.font, size) + 12 for _, label in items]
    # A horizontal legend is right-aligned, so it starts its total width left of the right edge.
    cx = x if vertical else right - sum(widths) - gap * (len(items) - 1)
    for i, ((color, label), w) in enumerate(zip(items, widths)):
        y = top - (i * 15 if vertical else 0)
        # The colored swatch, then the label (in the series color for a row, body color for a column).
        d.add(Rect(cx, y - 1, 7, 7, fillColor=HexColor(color), strokeColor=None))
        d.add(String(cx + 10, y, label, fontName=st.body.font, fontSize=size, fillColor=HexColor(color)
                     if not vertical else HexColor(st.body.color)))
        if not vertical:
            cx += w + gap


def _title(st: Style, d: Drawing, text: str, x: float, y: float) -> None:
    d.add(String(x, y, text, fontName=st.h2.font, fontSize=st.h2.size, fillColor=HexColor(st.h2.color)))


def grouped_bars(st: Style, m: Manifest, title: str, categories: list[str],
                 series: list[tuple[str, list[dict]]], width: float, height: float = 165) -> Drawing:
    """Vertical grouped bars with one-decimal value labels (the annualized TWR exhibit)."""
    d = Drawing(width, height)
    _title(st, d, title, 44, height - 14)
    # One list of values per series, in category order (the shape reportlab expects).
    values = [[s["value"] for s in vals] for _, vals in series]
    for name, vals in series:
        for v in vals:
            # Each bar's printed label (one decimal) is recorded with its source cell.
            m.fig(v, lambda x: f"{x:.1f}", f"{title}: {name}")
    for c in categories:
        m.fixed(c, f"{title}: category")
    flat = [v for row in values for v in row]
    # Bars grow from zero, so the axis always includes 0 even when every value is positive.
    lo, hi, step = ticks(min(0, *flat), max(0, *flat))
    _axis_labels(m, lo, hi, step, f"{title}: axis")
    # Plot area inside the drawing: room on the left for tick labels and at the top for title and legend.
    ch = VerticalBarChart()
    ch.x, ch.y, ch.width, ch.height = 44, 22, width - 100, height - 60
    ch.data = values
    ch.categoryAxis.categoryNames = categories
    ch.categoryAxis.labels.fontName, ch.categoryAxis.labels.fontSize = st.body.font, 8
    ch.valueAxis.valueMin, ch.valueAxis.valueMax, ch.valueAxis.valueStep = lo, hi, step
    ch.valueAxis.labels.fontName, ch.valueAxis.labels.fontSize = st.body.font, 7.5
    ch.valueAxis.labelTextFormat = "%g"
    # Value labels on the bars, matching the prior PMR's one-decimal style.
    ch.barLabelFormat = "%.1f"
    ch.barLabels.fontName, ch.barLabels.fontSize = st.body.font, 7.5
    ch.barLabels.nudge = 7
    ch.groupSpacing, ch.barSpacing = 18, 6
    for i, _ in enumerate(series):
        # Series colors come from the prior PMR's palette: portfolio first (navy), benchmark second (gold).
        ch.bars[i].fillColor = HexColor(st.palette[i % len(st.palette)])
    ch.categoryAxis.labelAxisMode = "low"  # category labels along the bottom, clear of negative bars
    d.add(ch)
    _legend(st, d, [(st.palette[i % len(st.palette)], n) for i, (n, _) in enumerate(series)], width - 10, height - 14)
    return d


def lines(st: Style, m: Manifest, title: str, categories: list[str], series: list[tuple[str, list[dict]]],
          width: float, height: float = 150) -> Drawing:
    """Line chart over periods (target allocation vs NAV)."""
    d = Drawing(width, height)
    _title(st, d, title, 44, height - 14)
    for c in categories:
        m.fixed(c, f"{title}: category")
    values = [[v["value"] for v in vals] for _, vals in series]
    for name, vals in series:
        for v in vals:
            # The line chart prints no value labels, but its data is still recorded (and marked
            # as not printed) so a reviewer can trace each point to the allocation history.
            m.fig(v, lambda x: f"{x:g}", f"{title}: {name} (data, not printed)")
    flat = [v for row in values for v in row]
    # Lines need no zero baseline, so the axis spans just the data range.
    lo, hi, step = ticks(min(flat), max(flat))
    _axis_labels(m, lo, hi, step, f"{title}: axis")
    ch = HorizontalLineChart()
    ch.x, ch.y, ch.width, ch.height = 44, 22, width - 100, height - 60
    ch.data = values
    ch.categoryAxis.categoryNames = categories
    ch.categoryAxis.labels.fontName, ch.categoryAxis.labels.fontSize = st.body.font, 7
    ch.valueAxis.valueMin, ch.valueAxis.valueMax, ch.valueAxis.valueStep = lo, hi, step
    ch.valueAxis.labels.fontName, ch.valueAxis.labels.fontSize = st.body.font, 7.5
    ch.valueAxis.labelTextFormat = "%g"
    for i, _ in enumerate(series):
        ch.lines[i].strokeColor = HexColor(st.palette[i % len(st.palette)])
        # 1.6pt matches the prior PMR's line weight.
        ch.lines[i].strokeWidth = 1.6
    d.add(ch)
    _legend(st, d, [(st.palette[i % len(st.palette)], n) for i, (n, _) in enumerate(series)], width - 10, height - 14)
    return d


def pie(st: Style, m: Manifest, title: str, slices: list[tuple[str, dict]], width: float, height: float = 170) -> Drawing:
    """Pie with a legend of 'Label value%' entries (property type mix)."""
    d = Drawing(width, height)
    _title(st, d, title, 0, height - 12)
    p = Pie()
    p.x, p.y, p.width, p.height = 20, 10, height - 40, height - 40
    p.data = [s["value"] for _, s in slices]
    # No labels on the slices themselves; the legend carries name and percentage.
    p.labels = None
    p.slices.strokeWidth = 0.5
    # Thin white borders separate adjacent slices.
    p.slices.strokeColor = HexColor("#ffffff")
    for i, _ in enumerate(slices):
        p.slices[i].fillColor = HexColor(st.palette[i % len(st.palette)])
    d.add(p)
    # Legend entries read "Apartment  23.5%"; each percentage is recorded with its source cell.
    pairs = [(st.palette[i % len(st.palette)], f"{name}  {m.fig(v, lambda x: f'{x:.1f}%', f'{title}: {name}')}")
             for i, (name, v) in enumerate(slices)]
    _legend(st, d, pairs, 0, height - 40, vertical=True, x=height + 10)
    return d


def hbars(st: Style, m: Manifest, title: str, bars: list[tuple[str, dict]], width: float, height: float = 190) -> Drawing:
    """Horizontal bars, smallest at the bottom, with one-decimal percentage labels (geographic mix)."""
    d = Drawing(width, height)
    _title(st, d, title, 0, height - 12)
    # Sorted ascending; reportlab draws the first category at the bottom, so the largest region ends on top.
    ordered = sorted(bars, key=lambda b: (b[1]["value"], b[0]))
    for name, v in ordered:
        m.fig(v, lambda x: f"{x:.1f}%", f"{title}: {name}")
    lo, hi, step = ticks(0, max(v["value"] for _, v in ordered))
    _axis_labels(m, lo, hi, step, f"{title}: axis")
    ch = HorizontalBarChart()
    # Wide left margin for region names, right margin for the value labels past each bar end.
    ch.x, ch.y, ch.width, ch.height = 110, 18, width - 160, height - 44
    ch.data = [[v["value"] for _, v in ordered]]
    ch.categoryAxis.categoryNames = [n for n, _ in ordered]
    ch.categoryAxis.labels.fontName, ch.categoryAxis.labels.fontSize = st.body.font, 7.5
    # Region names are right-aligned against the axis.
    ch.categoryAxis.labels.boxAnchor = "e"
    ch.valueAxis.valueMin, ch.valueAxis.valueMax, ch.valueAxis.valueStep = lo, hi, step
    ch.valueAxis.labels.fontName, ch.valueAxis.labels.fontSize = st.body.font, 7.5
    ch.valueAxis.labelTextFormat = "%g"
    ch.barLabelFormat = "%.1f%%"
    ch.barLabels.fontName, ch.barLabels.fontSize = st.body.font, 7
    # Percentage labels sit just past the end of each bar.
    ch.barLabels.boxAnchor = "w"
    ch.barLabels.dx = 4
    ch.bars[0].fillColor = HexColor(st.primary)
    ch.bars.strokeColor = None
    d.add(ch)
    return d
