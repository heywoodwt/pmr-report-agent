from pmr import style


def test_house_style_is_measured_from_prior_pmr(inputs):
    st, issues = style.load(inputs / "prior_pmr_3Q25.pdf")
    assert not issues
    assert (st.h1.size, st.h2.size, st.body.size, st.table.size, st.appendix_table.size) == (15.0, 10.0, 9.2, 8.0, 6.5)
    assert (st.primary, st.accent, st.stripe, st.grid) == ("#1b2a4a", "#c8a24b", "#eef1f6", "#c9d0dc")
    assert st.palette[:2] == [st.primary, st.accent] and len(st.palette) == 6
    assert st.footer_text.startswith("Corvane Advisors")


def test_sections_come_from_the_contents_page(inputs):
    st, _ = style.load(inputs / "prior_pmr_3Q25.pdf")
    assert len(st.sections) == 14
    assert st.sections[0] == style.Section("Portfolio Overview", 1, True)
    assert style.find_section(st.sections, "managers") == style.Section("Portfolio Managers", 2, False)
    assert style.find_section(st.sections, "performance update").new_page is False
    assert st.sections[-1].title.startswith("Appendix")


def test_style_round_trips_through_json(inputs):
    from dataclasses import asdict

    st, _ = style.load(inputs / "prior_pmr_3Q25.pdf")
    assert style.from_json(asdict(st)) == st
