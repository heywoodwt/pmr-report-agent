"""Label-driven worksheet reading. Nothing here uses fixed cell coordinates."""

from openpyxl.worksheet.worksheet import Worksheet

from pmr.model import Fact


# Cell value as text with whitespace collapsed; blank cells become "".
def text(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


# True for int/float; bool is excluded because Python treats it as int.
def is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def find_row(ws: Worksheet, *labels: str, start: int = 1) -> int | None:
    """First row (1-based) at or after start containing every label as a cell value."""
    # Case-insensitive match.
    want = {lab.lower() for lab in labels}
    for r in range(start, ws.max_row + 1):
        have = {text(c.value).lower() for c in ws[r] if c.value is not None}
        # Subset test: the row may hold extra cells.
        if want <= have:
            return r
    return None


def column_keys(ws: Worksheet, header_row: int, sub_row: int | None = None) -> dict[int, str]:
    """Map column index -> key from a header row, optionally with a sub-header row.

    A header spans every column up to the next non-empty header cell. With a sub-header,
    keys are "Header Sub" (e.g. "Quarter NET"); without one, the first column of a span is
    "Header" and any further columns are "Header #2", "Header #3". Without a sub-header the
    last header spans only its own column, since nothing marks where its span would end.
    """
    # Column index -> header text, non-empty cells only.
    header = {c.column: text(c.value) for c in ws[header_row] if c.value is not None}
    # Columns where a header span begins.
    starts = sorted(header)
    keys: dict[int, str] = {}
    for i, col in enumerate(starts):
        # Span ends just before the next header, or at the sheet edge for the last one.
        if i + 1 < len(starts):
            end = starts[i + 1] - 1
        else:
            end = ws.max_column if sub_row is not None else col
        for j, c in enumerate(range(col, end + 1)):
            if sub_row is not None:
                sub = text(ws.cell(sub_row, c).value)
                if sub:
                    # Two-level key, e.g. "Quarter NET".
                    keys[c] = f"{header[col]} {sub}"
                elif j == 0:
                    # No sub-header under the span's first column: use the header alone.
                    keys[c] = header[col]
            else:
                # Single-level: number repeated columns, e.g. "Market Value #2".
                keys[c] = header[col] if j == 0 else f"{header[col]} #{j + 1}"
    return keys


def read_rows(ws: Worksheet, file: str, header_row: int, sub_row: int | None = None,
              stop_row: int | None = None) -> list[tuple[Fact, dict[str, Fact]]]:
    """Rows below a header as (label fact, {column key: fact}).

    The label is the first column's text. Rows with no label are skipped. Values are kept
    as None when the cell is blank, so "missing" is never silently turned into zero.
    """
    keys = column_keys(ws, header_row, sub_row)
    # Leftmost keyed column holds the row label.
    label_col = min(keys)
    # Data starts below the lowest header row.
    first = (sub_row or header_row) + 1
    last = stop_row - 1 if stop_row else ws.max_row
    out = []
    for r in range(first, last + 1):
        lab = text(ws.cell(r, label_col).value)
        if not lab:
            continue
        # Every non-label column, each value tagged with its sheet!cell locator.
        values = {
            key: Fact(ws.cell(r, c).value, file, f"{ws.title}!{ws.cell(r, c).coordinate}")
            for c, key in keys.items()
            if c != label_col
        }
        out.append((Fact(lab, file, f"{ws.title}!{ws.cell(r, label_col).coordinate}"), values))
    return out


# True if any value in the row is numeric (data row vs. a heading row).
def has_numbers(values: dict[str, Fact]) -> bool:
    return any(is_number(f.value) for f in values.values())
