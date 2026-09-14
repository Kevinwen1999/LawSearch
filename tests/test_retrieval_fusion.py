from types import SimpleNamespace
from uuid import uuid4

from app.retrieval import PASSAGES_PER_CASE, RRF_K, ChunkHit, SearchResult, fuse, interleave, merge_issue_results

CASE_A, CASE_B, CASE_C = uuid4(), uuid4(), uuid4()


def hit(case_id, source, rank, chunk_id=None, para=None):
    return ChunkHit(chunk_id or uuid4(), case_id, para, para, f"text {rank}", source, rank)


def test_case_found_by_both_retrievers_outranks_single_list_leaders():
    lexical = [hit(CASE_A, "lexical", 1), hit(CASE_C, "lexical", 2)]
    vector = [hit(CASE_B, "vector", 1), hit(CASE_C, "vector", 2)]

    ranked = fuse(lexical, vector)

    assert ranked[0].case_id == CASE_C
    assert ranked[0].score == 2 / (RRF_K + 2)
    assert (ranked[0].lexical_rank, ranked[0].vector_rank) == (2, 2)


def test_case_rank_counts_distinct_cases_not_chunks():
    lexical = [hit(CASE_A, "lexical", 1), hit(CASE_A, "lexical", 2), hit(CASE_B, "lexical", 3)]

    ranked = fuse(lexical, [])

    assert [c.case_id for c in ranked] == [CASE_A, CASE_B]
    assert ranked[1].lexical_rank == 2
    assert ranked[1].score == 1 / (RRF_K + 2)


def test_same_chunk_from_both_lists_is_one_passage_marked_by_both():
    chunk = uuid4()
    lexical = [hit(CASE_A, "lexical", 3, chunk_id=chunk, para=7)]
    vector = [hit(CASE_A, "vector", 1, chunk_id=chunk, para=7)]

    [case] = fuse(lexical, vector)

    assert len(case.passages) == 1
    assert case.passages[0].matched_by == ["lexical", "vector"]
    assert case.passages[0].best_rank == 1


def test_passages_ordered_by_best_rank_and_capped():
    vector = [hit(CASE_A, "vector", r, para=r) for r in (5, 1, 4, 2, 3)]

    [case] = fuse([], sorted(vector, key=lambda h: h.rank))

    assert [p.para_no for p in case.passages] == [1, 2, 3][:PASSAGES_PER_CASE]


def test_empty_inputs():
    assert fuse([], []) == []


def test_interleave_gives_every_list_its_best_before_seconds():
    combined, issue_a, issue_b = ["w", "x", "y"], ["x", "h"], ["s", "t"]
    assert interleave([combined, issue_a, issue_b], str, 5) == ["w", "x", "s", "h", "t"]


def test_interleave_skips_duplicates_and_stops_at_k():
    assert interleave([["a", "b"], ["a", "c"], ["a"]], str, 3) == ["a", "b", "c"]
    assert interleave([["a", "b"], ["a", "c"]], str, 2) == ["a", "b"]
    assert interleave([["a"], []], str, 5) == ["a"]
    assert interleave([], str, 5) == []


def _case(name, rerank):
    return SimpleNamespace(case_id=name, rerank_score=rerank)


def _section(name, rerank=None, citing_cases=0):
    return SimpleNamespace(code=name, section_no="1", rerank_score=rerank, citing_cases=citing_cases)


def test_merge_issue_results_only_takes_issue_hits_the_reranker_judges_relevant():
    combined = SearchResult(cases=[_case("w", None), _case("x", -9.0)], sections=[_section("esa")], timings_ms={})
    issue = SearchResult(
        cases=[_case("noise", -3.0), _case("honda", 4.0)],
        sections=[_section("hrc-weak", -10.0), _section("hrc", -2.0)],
        timings_ms={},
    )

    cases, sections = merge_issue_results([combined, issue], k=5, k_sections=5, named_codes={"hrc", "hrc-weak"})

    assert [c.case_id for c in cases] == ["w", "x", "honda"]
    assert [s.code for s in sections] == ["esa", "hrc"]


def test_merge_issue_results_keeps_the_combined_querys_lead_then_interleaves():
    combined = SearchResult(cases=[_case(n, None) for n in "abcde"], sections=[], timings_ms={})
    issue = SearchResult(cases=[_case("i1", 2.0), _case("a", 3.0), _case("i2", 1.0)], sections=[], timings_ms={})

    cases, _ = merge_issue_results([combined, issue], k=6, k_sections=5)

    assert [c.case_id for c in cases] == ["a", "b", "c", "i1", "i2", "d"]


def test_merge_issue_results_keeps_issue_sections_to_laws_the_scenario_supports():
    combined = SearchResult(cases=[], sections=[_section("esa")], timings_ms={})
    issue = SearchResult(
        cases=[],
        sections=[
            _section("tenancies", -1.0), _section("esa", -2.0),
            _section("hrc", -3.0), _section("cited", -3.0, citing_cases=2),
        ],
        timings_ms={},
    )

    _, sections = merge_issue_results([combined, issue], k=5, k_sections=5, named_codes={"hrc"})

    assert [s.code for s in sections] == ["esa", "hrc", "cited"]
