from datetime import date

import pytest

from pmr.periods import Quarter


@pytest.mark.parametrize("text", ["4Q25", "4q25", "4Q2025", "Fourth Quarter 2025", " fourth quarter 2025 "])
def test_parse_forms(text):
    assert Quarter.parse(text) == Quarter(2025, 4)


def test_bounds_and_labels():
    q = Quarter.parse("4Q25")
    assert (q.start, q.end) == (date(2025, 10, 1), date(2025, 12, 31))
    assert (q.code, q.label) == ("4Q25", "Fourth Quarter 2025")
    assert Quarter.parse("1Q26").previous() == q
    assert Quarter.parse("1Q24").end == date(2024, 3, 31)


def test_search_and_reject():
    assert Quarter.search("Quarterly Investor Report - Third Quarter 2025") == Quarter(2025, 3)
    assert Quarter.search("no period here") is None
    with pytest.raises(ValueError):
        Quarter.parse("Q5 2025")
