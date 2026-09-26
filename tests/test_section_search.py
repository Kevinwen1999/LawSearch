from datetime import date
from uuid import uuid4

from app.filac import build_document, verify
from app.section_search import RRF_K, SectionCandidates, SectionChunkRow, SectionConfig, rank_sections
from tests.test_filac import empty_summary

LEG_IRPA, LEG_ITA = uuid4(), uuid4()


def row(legislation_id, code, section_no, label=None, cited_by=0):
    return SectionChunkRow(uuid4(), legislation_id, code, "act", code, None, None, section_no,
                           label or section_no, None, None, f"{code} s. {section_no}", None, None, cited_by)


def candidates(rows, lexical, vector, seed_ranks=None, seed_chunks=None):
    return SectionCandidates(
        lexical=[r.chunk_id for r in lexical], vector=[r.chunk_id for r in vector],
        seed_ranks=seed_ranks or {}, seed_chunks=seed_chunks or {},
        rows={r.chunk_id: r for r in rows}, timings_ms={},
    )


def test_chunks_of_one_section_group_into_one_result():
    s97a = row(LEG_IRPA, "I-2.5", "97", "97(1)-(2)")
    s97b = row(LEG_IRPA, "I-2.5", "97", "97(3)")
    s96 = row(LEG_IRPA, "I-2.5", "96")
    c = candidates([s97a, s97b, s96], lexical=[s97b, s97a, s96], vector=[s96, s97a])

    ranked = rank_sections(c, SectionConfig(graph_weight=0, citation_weight=0))

    assert [(r.code, r.section_no) for r in ranked] == [("I-2.5", "97"), ("I-2.5", "96")]
    assert ranked[0].lexical_rank == 1 and ranked[0].vector_rank == 2
    assert ranked[0].chunk_id == s97b.chunk_id  # best-ranked chunk represents the section


def test_sections_cited_by_top_cases_are_added_and_boosted():
    s18 = row(LEG_ITA, "I-3.3", "18")
    s96 = row(LEG_IRPA, "I-2.5", "96")
    c = candidates([s18, s96], lexical=[s96], vector=[s96],
                   seed_ranks={(LEG_ITA, "18"): [1, 2, 5]}, seed_chunks={(LEG_ITA, "18"): s18.chunk_id})

    text_only = rank_sections(c, SectionConfig(graph_weight=0, citation_weight=0))
    with_graph = rank_sections(c, SectionConfig(graph_weight=3.0, citation_weight=0))

    assert [r.section_no for r in text_only] == ["96"]
    assert with_graph[0].section_no == "18" and with_graph[0].citing_cases == 3
    assert with_graph[0].score == 3.0 / (RRF_K + 1)


def test_min_citing_cases_filters_weak_graph_signal():
    s18 = row(LEG_ITA, "I-3.3", "18")
    c = candidates([s18], lexical=[], vector=[], seed_ranks={(LEG_ITA, "18"): [4]},
                   seed_chunks={(LEG_ITA, "18"): s18.chunk_id})
    assert rank_sections(c, SectionConfig(min_citing_cases=2)) == []


def test_filac_verify_resolves_statutes_and_flags_later_wording():
    meta = {"case_id": uuid4(), "citation": "2010 FC 1", "style_of_cause": "A v B", "court": "FC",
            "decision_date": date(2010, 5, 1), "language": "en"}
    doc = build_document(meta, "[1] Facts.\n[2] Under IRPA s. 97(1)(b) the claim fails.\n[3] Dismissed.", [])
    summary = empty_summary()
    summary["law"] = {"status": "stated", "items": [
        {"authority": "IRPA, s. 97(1)(b)", "kind": "statute", "proposition": "risk to life",
         "relied_on_by": "court", "treatment": "applied", "anchor": 2},
    ]}

    def resolve_statutes(authority):
        assert authority == "IRPA, s. 97(1)(b)"
        return [{"chunk_id": "x", "code": "I-2.5", "title": "Immigration and Refugee Protection Act",
                 "section": "97(1)(b)", "section_label": "97(1)-(2)", "url": "u", "in_force_start": date(2012, 12, 15)}]

    result = verify(summary, doc, lambda cites: {}, resolve_statutes)

    [check] = result["sections"]["law"]
    [section] = check["resolved_sections"]
    assert section["in_force_start"] == "2012-12-15"
    assert section["in_force_after_decision"] is True
