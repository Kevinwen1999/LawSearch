from datetime import date, datetime
from uuid import uuid4

from app.brief_format import case_name, display, long_date, to_markdown
from app.citations import canonical, case_citations
from app.filac import (
    BRIEF_MAX_WORDS, BRIEF_SCHEMA, SECTIONS, FilacRecord, brief_texts, brief_word_count, build_document,
    detect_input_kind, enforce_decided_issues, overlap,
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


def conclusion(issue_id: int, answer: str, anchor: int) -> dict:
    return {"issue_id": issue_id, "kind": "conclusion", "step": "", "answer": answer, "anchor": anchor}


def step(issue_id: int, label: str, answer: str, anchor: int) -> dict:
    return {"issue_id": issue_id, "kind": "step", "step": label, "answer": answer, "anchor": anchor}


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
    summary["decision"] = {"status": "stated", "items": [conclusion(1, "The appeal is allowed.", 3)]}
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
        decision={"status": "stated", "items": [conclusion(1, "The appeal judge erred.", 16)]},
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
    summary["decision"] = {"status": "stated", "items": [conclusion(1, "Not decided.", 3)]}

    assert "issues: also listed as an undecided issue" in warning_messages(verify(summary, doc, lambda c: {}))

    summary["facts"] = {"status": "stated", "items": [
        {"text": "The appellant slipped.", "kind": "event", "issue_ids": [1], "anchor": 1}]}
    assert enforce_decided_issues(summary) == [question]
    assert summary["issues"] == {"status": "not_stated_in_text", "items": []}
    assert summary["decision"]["items"] == [] and summary["facts"]["items"][0]["issue_ids"] == []
    assert len(summary["undecided_issues"]["items"]) == 1


APPEAL = "\n".join([
    "COURT OF APPEAL",
    "DATE: 20161026",
    "Smith, Jones and Brown JJ.A.",
    "BETWEEN",
    "Her Majesty the Queen",
    "Respondent",
    "and",
    "Frank Doe",
    "Appellant",
    "Frank Doe, acting in person",
    "Paul Norton, appearing as duty counsel",
    "Jane Speyer, for the respondent",
    "Heard: October 3, 2016",
    "[1] The appellant's argument is that the verdict is unreasonable.",
    "[2] A balaclava with the appellant's DNA was found in the stolen car.",
    "[3] Where the evidence is circumstantial, the question is whether guilt is the only reasonable "
    "conclusion: R. v. Charemski, [1998] 1 S.C.R. 679.",
    "[4] In this case, the Crown must show first that the inference of contact at the time of the theft is "
    "reasonable, and second that guilt is the only rational conclusion on the totality of the evidence.",
    "[5] We are not satisfied that the inference that the appellant wore the balaclava during the theft was "
    "reasonable. His conviction is therefore unreasonable.",
    "[6] The trial judge required proof of contact at some time, which would leave uncertain how close in time "
    "the contact must be.",
])
APPEAL_META = {**META, "decision_date": date(2016, 10, 26)}
STAGED_RULE = {
    "text": "The Crown must show first that the inference of contact at the time of the theft is reasonable, and "
            "second that guilt is the only rational conclusion on the totality of the evidence.",
    "role": "rule", "issue_ids": [1], "anchor": 4,
}


def appeal_summary() -> dict:
    """A brief of APPEAL that follows every rule."""
    summary = empty_summary()
    summary["preliminary"].update(decision_date="2016-10-26", parties=[
        party("Her Majesty the Queen", "respondent"), party("Frank Doe", "appellant")])
    summary["issues"] = {"status": "stated", "items": [
        {"id": 1, "question": "Whether the verdict is unreasonable", "kind": "mixed", "anchor": 1, "sub_issues": []}]}
    summary["facts"] = {"status": "stated", "items": [
        {"text": "A balaclava with the appellant's DNA was found in the stolen car.", "kind": "event",
         "issue_ids": [1], "anchor": 2}]}
    summary["ratio"] = {"status": "stated", "items": [dict(STAGED_RULE)]}
    summary["decision"] = {"status": "stated", "items": [
        step(1, "Stage 1", "The inference that the appellant wore the balaclava during the theft was not reasonable.", 5),
        conclusion(1, "His conviction is therefore unreasonable.", 5),
    ]}
    return summary


def test_a_brief_that_follows_the_rules_has_no_warnings():
    doc = build_document(APPEAL_META, APPEAL, chunk_texts=[])
    assert doc.anchor_type == "paragraph"

    result = verify(appeal_summary(), doc, lambda c: {})

    assert warning_messages(result) == []
    assert result["problems"] == 0
    assert result["word_count"] == brief_word_count(appeal_summary())


def test_verify_warns_on_counsel_or_judges_as_parties_and_the_hearing_date():
    doc = build_document(APPEAL_META, APPEAL, chunk_texts=[])
    summary = appeal_summary()
    summary["preliminary"]["decision_date"] = "2016-10-03"
    summary["preliminary"]["parties"] += [party("Paul Norton", "other"), party("Smith", "other")]

    messages = warning_messages(verify(summary, doc, lambda c: {}))

    assert "preliminary: Paul Norton is named in the header only as counsel or a judge, not a party" in messages
    assert "preliminary: Smith is named in the header only as counsel or a judge, not a party" in messages
    # Frank Doe also appears as "acting in person", but the BETWEEN block names him as a party.
    assert not any("Frank Doe" in m for m in messages)
    assert "preliminary: date 2016-10-03 is the hearing date, not the date of decision" in messages


def test_verify_warns_when_the_ratio_holds_the_application_and_stages_are_sub_issues():
    doc = build_document(APPEAL_META, APPEAL, chunk_texts=[])
    summary = appeal_summary()
    summary["issues"]["items"][0].update(anchor=3, sub_issues=[
        {"question": "Specifically, whether guilt is the only rational conclusion on the totality of the evidence",
         "anchor": 4}])
    summary["ratio"]["items"].append({
        "text": "The inference that the appellant wore the balaclava during the theft was not reasonable.",
        "role": "reasoning", "issue_ids": [1], "anchor": 5})
    summary["decision"]["items"] = [conclusion(1, "His conviction is therefore unreasonable.", 5)]

    messages = warning_messages(verify(summary, doc, lambda c: {}))

    assert "issues: sub-issue restates the ratio's test: its stages belong in the ratio and the decision" in messages
    assert "issues: anchored to a paragraph that reviews another case (it ends in a citation)" in messages
    assert "ratio: applies the rule to this case's parties: that belongs in the decision" in messages
    assert "decision: issue 1: the rule is a staged test, but no stage has its own answer" in messages


def test_verify_warns_on_sub_issues_from_the_law_review_and_reasoning_about_the_court_below():
    doc = build_document(APPEAL_META, APPEAL, chunk_texts=[])
    summary = appeal_summary()
    summary["issues"]["items"][0]["sub_issues"] = [
        {"question": "Specifically, whether guilt is the only reasonable conclusion", "anchor": 3}]
    summary["ratio"]["items"].append({
        "text": "Requiring proof of contact at some time would leave uncertain how close in time it must be.",
        "role": "reasoning", "issue_ids": [1], "anchor": 6})

    messages = warning_messages(verify(summary, doc, lambda c: {}))

    assert "issues: sub-issue anchored to a paragraph that reviews another case (it ends in a citation)" in messages
    assert "ratio: comes from a paragraph about the court below: why it erred belongs in the decision" in messages


def test_verify_warns_on_decisions_that_do_not_answer_the_issue():
    doc = build_document(APPEAL_META, APPEAL, chunk_texts=[])
    summary = appeal_summary()
    summary["decision"]["items"] = [
        step(1, "Stage 1", "The first stage fails.", 5),
        conclusion(1, "The appeal is allowed.", 5),
        conclusion(1, STAGED_RULE["text"], 4),
    ]

    messages = warning_messages(verify(summary, doc, lambda c: {}))

    assert "decision: does not read as an answer to its issue" in messages
    assert "decision: repeats a ratio item" in messages
    assert "decision: issue 1 has 2 conclusions, not one" in messages

    summary["decision"]["items"] = [step(1, "Stage 1", "The first stage fails.", 5)]
    assert "decision: issue 1 has 0 conclusions, not one" in warning_messages(verify(summary, doc, lambda c: {}))


def test_verify_warns_when_the_brief_is_longer_than_a_page():
    doc = build_document(APPEAL_META, APPEAL, chunk_texts=[])
    summary = appeal_summary()
    summary["facts"]["items"].append({"text": "word " * BRIEF_MAX_WORDS, "kind": "event", "issue_ids": [1], "anchor": 2})

    result = verify(summary, doc, lambda c: {})

    assert result["word_count"] > BRIEF_MAX_WORDS
    assert f"brief: {result['word_count']} words: longer than one page (about {BRIEF_MAX_WORDS})" in warning_messages(result)


def test_brief_word_count_covers_the_parts_shown_in_the_brief():
    summary = appeal_summary()
    summary["facts"]["items"] += [
        {"text": "Convicted at trial.", "kind": "outcome_below", "issue_ids": [1], "anchor": 1},
        {"text": "The Crown relied on proximity.", "kind": "procedural_history", "issue_ids": [1], "anchor": 1},
    ]

    texts = brief_texts(summary)

    assert "Convicted at trial." in texts and "The Crown relied on proximity." not in texts
    assert brief_word_count(summary) == sum(len(t.split()) for t in texts)


def test_markdown_shows_the_outcome_below_after_events_and_stage_answers_before_the_conclusion():
    record = make_record(
        issues={"status": "stated", "items": [
            {"id": 1, "question": "Whether the verdict is unreasonable", "kind": "mixed", "anchor": 1, "sub_issues": []}]},
        facts={"status": "stated", "items": [
            {"text": "Convicted of theft at trial.", "kind": "outcome_below", "issue_ids": [1], "anchor": 3},
            {"text": "The Crown relied on proximity.", "kind": "procedural_history", "issue_ids": [1], "anchor": 2},
            {"text": "A balaclava was found in the car.", "kind": "event", "issue_ids": [1], "anchor": 1},
        ]},
        ratio={"status": "stated", "items": [dict(STAGED_RULE)]},
        decision={"status": "stated", "items": [
            step(1, "Stage 1", "The inference was not reasonable.", 5),
            conclusion(1, "The conviction is unreasonable.", 5),
        ]},
    )

    md = to_markdown(record)

    facts = md.split("## Facts of the case\n")[1].split("\n\n")[0].splitlines()
    assert facts == ["- A balaclava was found in the car.", "- Convicted of theft at trial."]
    decision = md.split("## Decision\n")[1].splitlines()
    assert decision == ["- Stage 1: The inference was not reasonable.", "- The conviction is unreasonable."]
    full = to_markdown(record, full_reading=True)
    assert "*Outcome below:* Convicted of theft at trial." in full
    assert "*Procedural history:* The Crown relied on proximity." in full
    assert "Issue 1: Stage 1: The inference was not reasonable." in full


def test_markdown_groups_ratio_and_decision_by_issue_when_there_are_several():
    record = make_record(
        issues={"status": "stated", "items": [
            {"id": 1, "question": "Whether the claim discloses a cause of action", "kind": "law", "anchor": 1,
             "sub_issues": []},
            {"id": 2, "question": "Whether the plaintiff has standing", "kind": "law", "anchor": 2, "sub_issues": []},
        ]},
        ratio={"status": "stated", "items": [
            {"text": "Only a person can sue.", "role": "rule", "issue_ids": [2], "anchor": 4},
            {"text": "Pleadings are read generously.", "role": "rule", "issue_ids": [1, 2], "anchor": 3},
        ]},
        decision={"status": "stated", "items": [
            conclusion(2, "The plaintiff has no standing.", 5), conclusion(1, "No cause of action is disclosed.", 5)]},
    )

    md = to_markdown(record)

    ratio = md.split("## Ratio Decidendi\n")[1].split("\n\n")[0].splitlines()
    assert ratio == [
        "- *Issue 1: Whether the claim discloses a cause of action*", "  - Pleadings are read generously.",
        "- *Issue 2: Whether the plaintiff has standing*", "  - Only a person can sue.",
    ]
    decision = md.split("## Decision\n")[1].splitlines()
    assert decision == [
        "- *Issue 1: Whether the claim discloses a cause of action*", "  - No cause of action is disclosed.",
        "- *Issue 2: Whether the plaintiff has standing*", "  - The plaintiff has no standing.",
    ]


def test_the_ratio_application_is_kept_for_the_full_reading():
    record = make_record(
        issues={"status": "stated", "items": [
            {"id": 1, "question": "Whether the verdict is unreasonable", "kind": "mixed", "anchor": 1, "sub_issues": []}]},
        ratio={"status": "stated", "items": [
            dict(STAGED_RULE),
            {"text": "No evidence ties the contact to the theft.", "role": "application", "issue_ids": [1], "anchor": 5},
        ]},
    )

    assert "No evidence ties" not in to_markdown(record)
    assert "*Application:* No evidence ties the contact to the theft." in to_markdown(record, full_reading=True)
    assert "No evidence ties the contact to the theft." not in brief_texts(record.summary)


def test_a_costs_request_beside_the_merits_is_dropped_but_a_costs_appeal_is_kept():
    summary = empty_summary()
    summary["issues"] = {"status": "stated", "items": [
        {"id": 1, "question": "Whether the respondent was holding the phone", "kind": "law", "anchor": 3, "sub_issues": []},
        {"id": 2, "question": "Whether the respondent should be awarded costs", "kind": "mixed", "anchor": 7,
         "sub_issues": []},
    ]}
    summary["decision"] = {"status": "stated", "items": [
        conclusion(1, "The appeal judge erred.", 16), conclusion(2, "No costs.", 18)]}

    assert enforce_decided_issues(summary) == ["Whether the respondent should be awarded costs"]
    assert [i["id"] for i in summary["issues"]["items"]] == [1]
    assert [d["issue_id"] for d in summary["decision"]["items"]] == [1]

    summary["issues"]["items"] = [
        {"id": 1, "question": "Whether the motion judge erred in awarding costs", "kind": "law", "anchor": 2,
         "sub_issues": []}]
    assert enforce_decided_issues(summary) == []
