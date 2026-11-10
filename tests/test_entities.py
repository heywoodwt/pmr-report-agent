from pmr.entities import canonical, resolve

KNOWN = ["Ironwood Value-Add Fund III", "Kestrel U.S. Real Estate Fund", "Meridian Opportunity Fund V"]


def test_numeral_suffix_and_punctuation_variants_resolve():
    for label, target in [
        ("Ironwood Value-Add Fund 3", "Ironwood Value-Add Fund III"),
        ("Ironwood Value Add Fund Three", "Ironwood Value-Add Fund III"),
        ("Kestrel US Real Estate Fund, LLC", "Kestrel U.S. Real Estate Fund"),
        ("The Meridian Opportunity Fund V L.P.", "Meridian Opportunity Fund V"),
    ]:
        m = resolve(label, KNOWN)
        assert (m.status, m.target) == ("normalized", target), label
        assert m.rules, "a normalized match must say which rules fired"


def test_different_vintages_are_not_merged():
    assert resolve("Ironwood Value-Add Fund IV", KNOWN).status == "none"


def test_near_miss_is_flagged_not_merged():
    m = resolve("Meridian Opportunity", KNOWN)
    assert m.status == "ambiguous" and m.target is None
    assert m.candidates == ["Meridian Opportunity Fund V"]


def test_canonical_is_stable():
    assert canonical("Cascadia Public Employees' Retirement System")[0] == "cascadia public employees retirement system"
