from datetime import date, datetime
from uuid import uuid4

from app.brief_format import case_name, display, long_date, to_markdown
from app.citations import canonical, case_citations
from app.filac import (
    BRIEF_SCHEMA, SECTIONS, FilacRecord, build_document, detect_input_kind, enforce_decided_issues, overlap,
    related_authority_query, verify,
)

META = {
    "case_id": uuid4(), "citation": "2020 TEST 1", "style_of_cause": "A v B",
    "court": "TEST", "decision_date": date(2020, 3, 4), "language": "en",
}

NUMBERED = "\n".join([
    "BETWEEN",
    "Jane Appellant-Person",
    "Appellant",
    "and",
    "Store Inc.",
    "Respondent",
    "[1] The appellant slipped on a wet floor in the store.",
    "[2] The issue is the occupier's duty under the Occupiers’ Liability Act, R.S.O. 1990, c. O.2, s. 3.",
    "[3] Applying Waldick v. Malcolm, [1991] 2 S.C.R. 456 and Rankin v. J.J., 2018 SCC 19, the appeal is allowed.",
])


def empty_summary() -> dict:
    summary = {name: {"status": "not_stated_in_text", "items": []} for name in SECTIONS}
    summary["preliminary"] = {"case_name": "", "citation": "", "decision_date": "", "court": "", "parties": []}
    return summary


def party(name: str, status: str) -> dict:
    return {"name": name, "status": status, "status_as_written": status.capitalize()}


def test_numbered_decision_anchors_to_paragraphs():
    doc = build_document(META, NUMBERED, chunk_texts=["unused"])

    assert doc.anchor_type == "paragraph"
    assert sorted(doc.anchors) == [1, 2, 3]
    assert doc.anchors[2].startswith("[2] The issue")
    assert "paragraphs are numbered" in doc.render()
    assert "Input: the decision." in doc.render()


def test_unnumbered_decision_anchors_to_passages():
    doc = build_document(META, "An old judgment without numbers.", chunk_texts=["first part", "second part"])

    assert doc.anchor_type == "passage"
    assert doc.anchors == {1: "first part", 2: "second part"}
    assert "[P2] second part" in doc.render()


def test_description_always_anchors_to_passages():
    doc = build_document(META, NUMBERED, chunk_texts=["whole text"], input_kind="description")

    assert doc.anchor_type == "passage"
    assert "a description of a decision" in doc.render()


def test_input_kind_detection():
    assert detect_input_kind(NUMBERED) == "decision"
    header_only = "R. v. Smith\nBETWEEN\nHer Majesty the Queen\nAppellant\nand\nJohn Smith\nRespondent\nReasons..."
    assert detect_input_kind(header_only) == "decision"
    assert detect_input_kind("The Kazemi cell phone case: a driver picked up her phone at a red light.") == "description"


def test_case_citations_normalize_to_corpus_format():
    assert case_citations("Rankin v. J.J., 2018 SCC 19") == ["2018 SCC 19"]
    assert case_citations("Waldick v. Malcolm, [1991] 2 S.C.R. 456") == ["[1991] 2 SCR 456"]
    assert case_citations("Occupiers' Liability Act, RSO 1990, c O.2, s 3") == []


def test_french_neutral_citations_map_to_english_court_codes():
    assert canonical("2019 CSC 65") == "2019 SCC 65"
    assert canonical("2020 CAF 12") == "2020 FCA 12"
    assert canonical("2019 SCC 65") == "2019 SCC 65"
    assert canonical("MB8-15979") == "MB8-15979"


def law_item(authority: str, anchor: int, proposition: str = "states the rule") -> dict:
    return {"authority": authority, "kind": "case", "proposition": proposition, "relied_on_by": "court",
            "treatment": "applied", "anchor": anchor}


def test_verify_flags_missing_anchor_invented_authority_and_party():
    doc = build_document(META, NUMBERED, chunk_texts=[])
    summary = empty_summary()
    summary["preliminary"]["parties"] = [party("Jane Appellant-Person", "appellant"), party("Nobody Ltd.", "respondent")]
    summary["facts"] = {"status": "stated", "items": [
        {"text": "The appellant slipped on a wet floor in the store.", "kind": "event", "issue_ids": [1], "anchor": 1},
        {"text": "Invented fact.", "kind": "event", "issue_ids": [1], "anchor": 99},
    ]}
    summary["law"] = {"status": "stated", "items": [
        {**law_item("Occupiers' Liability Act, s. 3", 2), "kind": "statute"},
        law_item("Rankin v. J.J., 2018 SCC 19", 3),
        law_item("Donoghue v. Stevenson, [1932] AC 562", 3),
    ]}
    resolved = {"2018 SCC 19": {"case_id": "x", "citation": "2018 SCC 19"}}

    result = verify(summary, doc, lambda cites: {c: resolved[c] for c in cites if c in resolved})

    facts, law = result["sections"]["facts"], result["sections"]["law"]
    assert [c["anchor_ok"] for c in facts] == [True, False]
    assert law[0]["found_in_anchor"] and law[0]["found_in_document"]
    assert law[1]["resolved_case"]["citation"] == "2018 SCC 19"
    assert not law[2]["found_in_document"]
    assert [p["found_in_document"] for p in result["preliminary"]["parties"]] == [True, False]
    assert result["problems"] == 3  # the missing anchor, Donoghue, and "Nobody Ltd."
    assert set(result["anchor_text"]) == {"1", "2", "3"}


def warning_messages(result: dict) -> list[str]:
    return [f"{w['section']}: {w['message']}" for w in result["rule_warnings"]]


def test_verify_warns_on_case_brief_format_rules():
    doc = build_document(META, NUMBERED, chunk_texts=[])
    summary = empty_summary()
    summary["preliminary"].update(citation="2020 TEST 2", decision_date="2020-03-05")
    summary["issues"] = {"status": "stated", "items": [
        {"id": 1, "question": "Whether the occupier owed a duty", "kind": "law", "anchor": 2,
         "sub_issues": [{"question": "Specifically, whether the floor was wet", "anchor": 1}]},
        {"id": 2, "question": "Is the store liable?", "kind": "mixed", "anchor": 3,
         "sub_issues": [{"question": "And the damages?", "anchor": 9}]},
    ]}
    summary["facts"] = {"status": "stated", "items": [
        {"text": "The appellant slipped on a wet floor.", "kind": "event", "issue_ids": [], "anchor": 1},
        {"text": "Something about taxation of trusts and estates.", "kind": "event", "issue_ids": [7], "anchor": 1},
    ]}
    summary["ratio"] = {"status": "stated", "items": [
        {"text": "Applying Waldick the appeal is allowed.", "role": "reasoning", "issue_ids": [1], "anchor": 3},
    ]}
    summary["decision"] = {"status": "stated", "items": [{"issue_id": 1, "answer": "The appeal is allowed.", "anchor": 3}]}
    summary["law"] = {"status": "stated", "items": [law_item("Rankin v. J.J., 2018 SCC 19", 3, proposition=" ")]}

    result = verify(summary, doc, lambda cites: {})

    messages = warning_messages(result)
    assert 'issues: issue is not a question beginning with "Whether"' in messages
    assert 'issues: sub-issue is not a "whether" question' in messages
    assert "facts: not tied to any issue" in messages
    assert "facts: refers to unknown issue id(s) [7]" in messages
    assert any(m.startswith("facts: little wording in common") for m in messages)
    assert "decision: issue 2 has no decision" in messages
    assert "ratio: ratio does not start with the rule" in messages
    assert "law: no proposition given for the authority" in messages
    assert any("citation '2020 TEST 2' differs" in m for m in messages)
    assert any("date 2020-03-05 differs" in m for m in messages)
    # The first sub-issue ("Specifically, whether") is fine; the second's anchor 9 doesn't exist.
    assert [s["anchor_ok"] for s in result["sections"]["issues"][1]["sub_issues"]] == [False]
    assert result["problems"] == 1


def test_overlap_scores_wording_shared_with_the_anchor():
    anchor = "[1] The facts of this case are simple. The respondent was driving home from work alone."
    assert overlap("The respondent was driving home from work alone.", anchor) == 1.0
    assert overlap("A motorist commuted solo.", anchor) == 0.0


def test_schema_requires_every_field_and_forbids_extras():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        elif node.get("type") == "array":
            walk(node["items"])

    walk(BRIEF_SCHEMA)
    assert list(BRIEF_SCHEMA["required"]) == ["preliminary", *SECTIONS]


def make_record(case_meta: dict | None = None, **summary_overrides) -> FilacRecord:
    summary = empty_summary()
    summary.update(summary_overrides)
    return FilacRecord(
        case_id=uuid4(), prompt_version="brief-v1", model="m", backend="b",
        summary=summary, verification={"anchor_type": "paragraph"}, usage={}, created_at=datetime.now(),
        case_meta=case_meta or {"source": "a2aj"},
    )


def test_related_authority_query_joins_issues_then_event_facts():
    record = make_record(
        issues={"status": "stated", "items": [
            {"id": 1, "question": "Whether a duty of care is owed", "kind": "law", "anchor": 1, "sub_issues": []},
        ]},
        facts={"status": "stated", "items": [
            {"text": "slip on a wet floor", "kind": "event", "issue_ids": [1], "anchor": 2},
            {"text": "the trial judge dismissed the claim", "kind": "procedural_history", "issue_ids": [1], "anchor": 3},
        ]},
    )

    assert related_authority_query(record) == "a duty of care is owed; slip on a wet floor"


def test_related_authority_query_empty_when_nothing_stated():
    assert related_authority_query(make_record()) == ""


def test_case_names_drop_periods_and_dates_are_long_form():
    assert case_name("R. v. Kazemi") == "R v Kazemi"
    assert case_name("Rankin v. J.J.") == "Rankin v JJ"
    assert case_name("Canada (A.G.) v. Bedford Holdings Ltd.") == "Canada (AG) v Bedford Holdings Ltd"
    assert long_date(date(2013, 9, 27)) == "September 27, 2013"
    assert long_date("2013-09-07") == "September 7, 2013"
    assert long_date("sometime in 2013") == "sometime in 2013"


def kazemi_like_record(case_meta: dict) -> FilacRecord:
    return make_record(
        case_meta=case_meta,
        preliminary={"case_name": "R. v. Kazemi", "citation": "2013 ONCA 585", "decision_date": "2013-09-27",
                     "court": "Court of Appeal for Ontario",
                     "parties": [party("Her Majesty the Queen", "appellant"), party("Khojasteh Kazemi", "respondent")]},
        issues={"status": "stated", "items": [
            {"id": 1, "question": "Whether the respondent was holding the phone", "kind": "law", "anchor": 3,
             "sub_issues": [{"question": "Specifically, whether holding must be sustained", "anchor": 5}]},
        ]},
        facts={"status": "stated", "items": [
            {"text": "She picked it up at the red light.", "kind": "event", "issue_ids": [1], "anchor": 1},
            {"text": "The appeal judge acquitted.", "kind": "procedural_history", "issue_ids": [1], "anchor": 5},
        ]},
        ratio={"status": "stated", "items": [
            {"text": "Holding means having it in one's hand.", "role": "rule", "issue_ids": [1], "anchor": 11},
        ]},
        decision={"status": "stated", "items": [{"issue_id": 1, "answer": "The appeal judge erred.", "anchor": 16}]},
    )


def test_markdown_follows_the_model_answer_layout():
    record = kazemi_like_record({"source": "a2aj", "style_of_cause": "R. v. Kazemi", "citation": "2013 ONCA 585",
                                 "decision_date": date(2013, 9, 27)})

    md = to_markdown(record)

    assert md.splitlines()[:8] == [
        "# Case Brief of R v Kazemi",
        "",
        "## Preliminary Information",
        "- **Name and Citation of Case:** *R v Kazemi*, 2013 ONCA 585",
        "- **Date of Decision:** September 27, 2013",
        "- **Parties:**",
        "  - Her Majesty the Queen – appellant",
        "  - Khojasteh Kazemi – respondent",
    ]
    headings = [line for line in md.splitlines() if line.startswith("## ")]
    assert headings == ["## Preliminary Information", "## Legal Issue(s)", "## Facts of the case",
                        "## Ratio Decidendi", "## Decision"]
    assert "  - Specifically, whether holding must be sustained" in md
    assert "acquitted" not in md  # procedural history stays out of the brief
    assert "¶" not in md and "Court of Appeal" not in md
    assert "(¶11)" in to_markdown(record, anchors=True)
    full = to_markdown(record, full_reading=True)
    assert "## Full case reading" in full and "*Procedural history:* The appeal judge acquitted." in full


def test_user_text_takes_preliminary_information_from_the_brief():
    record = kazemi_like_record({"source": "pasted", "input_kind": "description"})

    shown = display(record)

    assert shown["name_and_citation"] == "R v Kazemi, 2013 ONCA 585"
    assert shown["decision_date"] == "September 27, 2013"
    assert "Based on a description" in to_markdown(record)


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


def test_statute_named_after_a_leading_pinpoint_is_found():
    from app.filac import _key_terms

    assert _key_terms("s. 78.1(1) of the Highway Traffic Act, R.S.O. 1990, c. H.8") == ["highway traffic act"]
    assert _key_terms("ss. 64(1) and (2) of the Legislation Act, 2006") == ["legislation act"]
    assert _key_terms("Occupiers' Liability Act, s. 3") == ["occupiers' liability act"]


def test_issue_also_listed_as_undecided_is_flagged():
    doc = build_document(META, NUMBERED, chunk_texts=[])
    summary = empty_summary()
    question = "Whether the trial judge misapprehended the facts"
    summary["issues"] = {"status": "stated", "items": [
        {"id": 1, "question": question, "kind": "fact", "anchor": 2, "sub_issues": []}]}
    summary["undecided_issues"] = {"status": "stated", "items": [
        {"question": question, "reason": "not necessary", "anchor": 3}]}
    summary["decision"] = {"status": "stated", "items": [{"issue_id": 1, "answer": "Not decided.", "anchor": 3}]}

    assert "issues: also listed as an undecided issue" in warning_messages(verify(summary, doc, lambda c: {}))

    summary["facts"] = {"status": "stated", "items": [
        {"text": "The appellant slipped.", "kind": "event", "issue_ids": [1], "anchor": 1}]}
    assert enforce_decided_issues(summary) == [question]
    assert summary["issues"] == {"status": "not_stated_in_text", "items": []}
    assert summary["decision"]["items"] == [] and summary["facts"]["items"][0]["issue_ids"] == []
    assert len(summary["undecided_issues"]["items"]) == 1
