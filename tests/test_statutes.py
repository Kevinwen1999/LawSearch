from app.statutes import parse_xml

STATUTE = """<?xml version="1.0" encoding="UTF-8"?>
<Statute xmlns:lims="http://justice.gc.ca/lims" lims:current-date="2026-09-01">
  <Identification>
    <ShortTitle>Test Act</ShortTitle>
    <Chapter><ConsolidatedNumber>T-1</ConsolidatedNumber></Chapter>
  </Identification>
  <Body>
    <Section lims:inforce-start-date="2020-01-01">
      <MarginalNote>Preamble</MarginalNote>
      <Label>5</Label>
      <Subsection><Label>(1)</Label><Text>{first}</Text></Subsection>
      <Subsection><Label>(2)</Label><Text>{second}</Text></Subsection>
      <Subsection><MarginalNote>Penalty</MarginalNote><Label>(3)</Label><Text>{third}</Text></Subsection>
    </Section>
    <Section>
      <MarginalNote>Short section</MarginalNote>
      <Label>6</Label>
      <Text>Everything in one piece.</Text>
    </Section>
  </Body>
</Statute>
"""


def test_marginal_notes_follow_the_subsections_they_head(tmp_path):
    path = tmp_path / "T-1.xml"
    path.write_text(STATUTE.format(first="a " * 700, second="b " * 700, third="c " * 700), encoding="utf-8")

    doc = parse_xml(path)

    headers = [chunk.text.split("\n", 1)[0] for chunk in doc.chunks]
    assert headers == [
        "Test Act, s. 5(1) — Preamble",
        "Test Act, s. 5(2) — Preamble",  # no note of its own: the preceding note still covers it
        "Test Act, s. 5(3) — Penalty",
        "Test Act, s. 6 — Short section",
    ]
    assert [chunk.marginal_note for chunk in doc.chunks] == ["Preamble", "Preamble", "Penalty", "Short section"]
