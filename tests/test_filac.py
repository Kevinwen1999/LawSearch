from datetime import datetime
from uuid import uuid4

from app.citations import canonical, case_citations
from app.filac import FILAC_SCHEMA, SECTIONS, FilacRecord, build_document, related_authority_query, verify

META = {
    "case_id": uuid4(), "citation": "2020 TEST 1", "style_of_cause": "A v B",
    "court": "TEST", "decision_date": None, "language": "en",
}

NUMBERED = "\n".join([
    "Headnote summary before the reasons.",
    "[1] The appellant slipped on a wet floor in the store.",
    "[2] The issue is the occupier's duty under the Occupiers’ Liability Act, R.S.O. 1990, c. O.2, s. 3.",
    "[3] Applying Waldick v. Malcolm, [1991] 2 S.C.R. 456 and Rankin v. J.J., 2018 SCC 19, the appeal is allowed.",
])


def empty_summary() -> dict:
    return {name: {"status": "not_stated_in_text", "items": []} for name in SECTIONS}


def test_numbered_decision_anchors_to_paragraphs():
    doc = build_document(META, NUMBERED, chunk_texts=["unused"])

    assert doc.anchor_type == "paragraph"
    assert sorted(doc.anchors) == [1, 2, 3]
    assert doc.anchors[2].startswith("[2] The issue")
    assert "paragraphs are numbered" in doc.render()


def test_unnumbered_decision_anchors_to_passages():
    doc = build_document(META, "An old judgment without numbers.", chunk_texts=["first part", "second part"])

    assert doc.anchor_type == "passage"
    assert doc.anchors == {1: "first part", 2: "second part"}
    assert "[P2] second part" in doc.render()


def test_case_citations_normalize_to_corpus_format():
    assert case_citations("Rankin v. J.J., 2018 SCC 19") == ["2018 SCC 19"]
    assert case_citations("Waldick v. Malcolm, [1991] 2 S.C.R. 456") == ["[1991] 2 SCR 456"]
    assert case_citations("Occupiers' Liability Act, RSO 1990, c O.2, s 3") == []


def test_french_neutral_citations_map_to_english_court_codes():
    assert canonical("2019 CSC 65") == "2019 SCC 65"
    assert canonical("2020 CAF 12") == "2020 FCA 12"
    assert canonical("2019 SCC 65") == "2019 SCC 65"
    assert canonical("MB8-15979") == "MB8-15979"


def test_verify_flags_missing_anchor_and_invented_authority():
    doc = build_document(META, NUMBERED, chunk_texts=[])
    summary = empty_summary()
    summary["facts"] = {"status": "stated", "items": [
        {"text": "Slipped on a wet floor.", "anchor": 1},
        {"text": "Invented fact.", "anchor": 99},
    ]}
    summary["law"] = {"status": "stated", "items": [
        {"authority": "Occupiers' Liability Act, s. 3", "kind": "statute", "relied_on_by": "court", "anchor": 2},
        {"authority": "Rankin v. J.J., 2018 SCC 19", "kind": "case", "relied_on_by": "court", "anchor": 3},
        {"authority": "Donoghue v. Stevenson, [1932] AC 562", "kind": "case", "relied_on_by": "court", "anchor": 3},
    ]}
    resolved = {"2018 SCC 19": {"case_id": "x", "citation": "2018 SCC 19"}}

    result = verify(summary, doc, lambda cites: {c: resolved[c] for c in cites if c in resolved})

    facts, law = result["sections"]["facts"], result["sections"]["law"]
    assert [c["anchor_ok"] for c in facts] == [True, False]
    assert law[0]["found_in_anchor"] and law[0]["found_in_document"]
    assert law[1]["resolved_case"]["citation"] == "2018 SCC 19"
    assert not law[2]["found_in_document"]
    assert result["problems"] == 2
    assert set(result["anchor_text"]) == {"1", "2", "3"}


def test_schema_requires_every_field_and_forbids_extras():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        elif node.get("type") == "array":
            walk(node["items"])

    walk(FILAC_SCHEMA)
    assert set(FILAC_SCHEMA["required"]) == set(SECTIONS)


def make_record(**summary_overrides) -> FilacRecord:
    summary = empty_summary()
    summary.update(summary_overrides)
    return FilacRecord(
        case_id=uuid4(), prompt_version="filac-v1", model="m", backend="b",
        summary=summary, verification={}, usage={}, created_at=datetime.now(),
    )


def test_related_authority_query_joins_issues_then_facts():
    record = make_record(
        issues={"status": "stated", "items": [{"text": "duty of care", "anchor": 1}]},
        facts={"status": "stated", "items": [{"text": "slip on a wet floor", "anchor": 2}]},
    )

    assert related_authority_query(record) == "duty of care; slip on a wet floor"


def test_related_authority_query_empty_when_nothing_stated():
    record = make_record()

    assert related_authority_query(record) == ""


def test_onca_named_citations_need_an_ontario_court_of_appeal_marker():
    from app.citations import onca_named_citations, onca_party_key

    text = (
        "See Hobbs v. TDI Canada Ltd. (2004), 246 D.L.R. (4th) 43 (Ont. C.A.), and Kieran v. Ingram "
        "Micro Inc., [2004] O.J. No. 3118 (C.A.); R. v. Nichols, 2001 CanLII 5680 (ON CA). But not "
        "Smith v. Jones (2004), 30 B.C.L.R. 1 (B.C.C.A.) or R. v. Brown (2003), 1 S.C.R. 5."
    )
    assert onca_named_citations(text) == [("hobbs", "tdi", 2004), ("kieran", "ingram", 2004), ("r", "nichols", 2001)]
    assert onca_party_key("Hobbs v. TDI Canada Ltd.", 2004) == ("hobbs", "tdi", 2004)
    assert onca_party_key("Smith v. The Queen", 2001) == ("smith", "queen", 2001)
    assert onca_party_key("Re Smith Estate", 2001) is None


def test_ontario_regulation_refs_and_citing_threshold():
    from app.ontario_regs import cited_regulations, regulation_refs

    refs = regulation_refs("O. Reg. 288/01, s. 2(1), para. 3; O.Reg. 34/2010; R.R.O. 1990, Reg. 194, r. 20.04")
    assert {(r.citation, r.alias) for r in refs} == {
        ("O. Reg. 288/01", "010288"), ("O. Reg. 34/10", "100034"), ("R.R.O. 1990, Reg. 194", "900194"),
    }
    cited = cited_regulations([("A", "O. Reg. 288/01"), ("B", "under O. Reg. 288/01 and O. Reg. 1/99"), ("C", "")])
    assert [(r.ref.citation, r.cited_by) for r in cited] == [("O. Reg. 288/01", ["A", "B"])]
    assert cited[0].ref.url == "https://www.ontario.ca/laws/regulation/010288"


def test_onca_named_citation_survives_pdf_line_breaks():
    from app.citations import onca_named_citations

    text = "Hobbs v. TDI Canada Ltd. (2004), 246 D.L.R. (4th) 43 (\nC.A.\n), rev'g [2003] O.J. No. 2646"
    assert onca_named_citations(text) == [("hobbs", "tdi", 2004)]
