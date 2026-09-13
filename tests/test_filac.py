from uuid import uuid4

from app.filac import FILAC_SCHEMA, SECTIONS, build_document, case_citations, verify

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
