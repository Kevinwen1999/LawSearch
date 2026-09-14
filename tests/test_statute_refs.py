import pytest

from app.statute_refs import StatuteIndex, _laws_from_rows

LAWS = {
    "Immigration and Refugee Protection Act": ("I-2.5", "act"),
    "Immigration and Refugee Protection Regulations": ("SOR/2002-227", "regulation"),
    "Income Tax Act": ("I-3.3", "act"),
    "Employment Insurance Act": ("E-5.6", "act"),
    "Constitution Act, 1982": ("CONST-1982", "constitution"),
    "Criminal Code": ("C-46", "act"),
}


@pytest.fixture(scope="module")
def index():
    return StatuteIndex(LAWS)


def refs(index, text):
    return [(r.code, r.section_no, r.pinpoint) for r in index.extract(text)]


def test_forward_reference_with_full_title(index):
    text = "The claim was rejected under paragraph 111(1)(c) of the Immigration and Refugee Protection Act."
    assert refs(index, text) == [("I-2.5", "111", "(1)(c)")]


def test_backward_reference_and_section_lists(index):
    assert refs(index, "See Income Tax Act, s. 18(1)(a) and 18(1)(h).") == [
        ("I-3.3", "18", "(1)(a)"), ("I-3.3", "18", "(1)(h)"),
    ]
    assert refs(index, "under sections 29 and 30 of the Employment Insurance Act") == [
        ("E-5.6", "29", ""), ("E-5.6", "30", ""),
    ]


def test_acronyms_and_charter(index):
    assert refs(index, "a claim under s. 97 of IRPA") == [("I-2.5", "97", "")]
    assert refs(index, "IRPR, s 4(1)") == [("SOR/2002-227", "4", "(1)")]
    assert refs(index, "a breach of section 8 of the Charter") == [("CONST-1982", "8", "")]


def test_document_defined_alias_and_generic_the_act(index):
    text = (
        'The Immigration and Refugee Protection Act, SC 2001, c 27 ("IRPA" or the "Act") governs. '
        "Relief is sought under subsection 25(1) of the Act and section 96 of IRPA."
    )
    found = index.extract(text)
    assert [(r.code, r.section_no, r.pinpoint) for r in found] == [("I-2.5", "25", "(1)"), ("I-2.5", "96", "")]
    assert found[0].explicit is False and found[1].explicit is True


def test_the_act_resolves_to_most_recent_named_act(index):
    text = "The Employment Insurance Act sets the rules. Under section 30 of the Act, a claimant is disqualified."
    assert refs(index, text) == [("E-5.6", "30", "")]


def test_bare_section_and_unknown_law_are_ignored(index):
    assert refs(index, "At para 12 the court applied s. 96 generally.") == []
    assert refs(index, "section 12 of the Education Act, R.S.S. 1978") == []


def test_longest_title_wins_over_contained_title(index):
    assert refs(index, "s. 4 of the Immigration and Refugee Protection Regulations") == [("SOR/2002-227", "4", "")]


def test_statute_citation_between_name_and_section(index):
    assert refs(index, "Immigration and Refugee Protection Act, SC 2001, c 27, s 97(1)(b)") == [("I-2.5", "97", "(1)(b)")]
    assert refs(index, "Income Tax Act, RSC 1985, c 1 (5th Supp), s 245(4)") == [("I-3.3", "245", "(4)")]
    assert refs(index, "Immigration and Refugee Protection Regulations, SOR/2002-227, s. 4(1)") == [("SOR/2002-227", "4", "(1)")]


def test_pick_chunk_uses_subsection_range():
    from app.statute_refs import StatuteRef, pick_chunk

    rows = [("a", "97(1)-(2)", 0), ("b", "97(3)", 1)]
    assert pick_chunk(rows, StatuteRef("I-2.5", "97", "(1)(b)", "", 0, True))[0] == "a"
    assert pick_chunk(rows, StatuteRef("I-2.5", "97", "(3)", "", 0, True))[0] == "b"
    assert pick_chunk(rows, StatuteRef("I-2.5", "97", "", "", 0, True))[0] == "a"


def test_alias_must_follow_the_title_it_names():
    laws = {**LAWS, "Privacy Act": ("P-21", "act")}
    index = StatuteIndex(laws)
    text = ("Both the Privacy Act and the Freedom of Information and Protection of Privacy Act (FIPPA) "
            "were argued. The claim under s. 14 of FIPPA fails.")
    assert [(r.code, r.section_no) for r in index.extract(text)] == []
    defined = 'The Income Tax Act, RSC 1985, c 1 (5th Supp) (the "Act") applies; see s. 245 of the Act.'
    assert [(r.code, r.section_no) for r in index.extract(defined)] == [("I-3.3", "245")]


def test_federal_title_inside_a_longer_provincial_name_is_not_matched():
    index = StatuteIndex({**LAWS, "Human Rights Code": ("X-1", "act")})
    assert [(r.code, r.section_no) for r in index.extract("s. 5 of the Ontario Human Rights Code")] == []
    assert [(r.code, r.section_no) for r in index.extract("The Human Rights Code, s. 5, applies")] == [("X-1", "5")]


def test_ontario_citation_tail_resolves_like_the_federal_one():
    index = StatuteIndex({**LAWS, "Occupiers' Liability Act": ("RSO1990cO2", "act")})
    assert [(r.code, r.section_no) for r in index.extract(
        "Occupiers' Liability Act, R.S.O. 1990, c. O.2, s. 3(1)"
    )] == [("RSO1990cO2", "3")]


def test_colliding_title_resolves_by_citation_jurisdiction():
    laws = {**LAWS, "Income Tax Act": ("I-3.3", "act")}  # federal default, as from_db would pick
    collisions = {"Income Tax Act": {"federal": ("I-3.3", "act"), "ontario": ("RSO1990cI2", "act")}}
    index = StatuteIndex(laws, collisions)

    assert [(r.code, r.section_no) for r in index.extract(
        "Income Tax Act, R.S.C. 1985, c. 1 (5th Supp.), s. 18(1)(a)"
    )] == [("I-3.3", "18")]
    assert [(r.code, r.section_no) for r in index.extract(
        "Income Tax Act, R.S.O. 1990, c. I.2, s. 4"
    )] == [("RSO1990cI2", "4")]


def test_colliding_title_with_no_citation_tail_falls_back_to_default():
    laws = {**LAWS, "Income Tax Act": ("I-3.3", "act")}
    collisions = {"Income Tax Act": {"federal": ("I-3.3", "act"), "ontario": ("RSO1990cI2", "act")}}
    index = StatuteIndex(laws, collisions)

    assert [(r.code, r.section_no) for r in index.extract("under s. 4 of the Income Tax Act")] == [("I-3.3", "4")]


def test_laws_from_rows_prefers_federal_and_reports_collision():
    rows = [
        ("Income Tax Act", "I-3.3", "act", "federal"),
        ("Income Tax Act", "RSO1990cI2", "act", "ontario"),
        ("Occupiers' Liability Act", "RSO1990cO2", "act", "ontario"),
    ]
    laws, collisions = _laws_from_rows(rows)

    assert laws["Income Tax Act"] == ("I-3.3", "act")
    assert collisions == {
        "Income Tax Act": {"federal": ("I-3.3", "act"), "ontario": ("RSO1990cI2", "act")}
    }
    assert "Occupiers' Liability Act" not in collisions


def test_laws_from_rows_registers_year_stripped_alias():
    rows = [("Municipal Act, 2001", "SO2001c25", "act", "ontario")]
    laws, _ = _laws_from_rows(rows)

    assert laws["Municipal Act"] == ("SO2001c25", "act")
    assert laws["Municipal Act, 2001"] == ("SO2001c25", "act")


def test_laws_from_rows_year_alias_does_not_override_a_distinct_title():
    rows = [
        ("Some Act, 2001", "SO2001cX", "act", "ontario"),
        ("Some Act", "C-99", "act", "federal"),  # a genuinely different law, loaded either order
    ]
    laws, _ = _laws_from_rows(rows)

    assert laws["Some Act"] == ("C-99", "act")

    rows_reversed = list(reversed(rows))
    laws_reversed, _ = _laws_from_rows(rows_reversed)
    assert laws_reversed["Some Act"] == ("C-99", "act")


def test_named_codes_finds_laws_without_section_references(index):
    assert index.named_codes("Criminal Code; Income Tax Act (federal); Ontario Human Rights Code") == {"C-46", "I-3.3"}
