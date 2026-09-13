from uuid import uuid4

from app.retrieval import PASSAGES_PER_CASE, RRF_K, ChunkHit, fuse

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
