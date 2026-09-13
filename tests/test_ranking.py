from uuid import uuid4

from app.retrieval import (
    PHASE2_CONFIG,
    RRF_K,
    Candidates,
    CaseMeta,
    ChunkHit,
    Passage,
    RankingConfig,
    fuse,
    rank,
)

FC_1, FC_2, FC_3, SCC_LEADING, TRIBUNAL = (uuid4() for _ in range(5))


def meta(court: str, cited_by: int = 0) -> CaseMeta:
    return CaseMeta(f"cit-{court}", None, "A v B", court, None, None, "en", cited_by)


def hit(case_id, source, rank_no):
    return ChunkHit(uuid4(), case_id, 1, 1, "text", source, rank_no)


def candidates(**overrides) -> Candidates:
    base = dict(
        lexical=[hit(FC_1, "lexical", 1), hit(FC_2, "lexical", 2), hit(FC_3, "lexical", 3)],
        vector=[hit(FC_2, "vector", 1), hit(FC_1, "vector", 2), hit(TRIBUNAL, "vector", 3)],
        seed_ranks={SCC_LEADING: [1, 2, 3]},
        meta={FC_1: meta("FC", 5), FC_2: meta("FC", 3), FC_3: meta("FC"), TRIBUNAL: meta("SST"),
              SCC_LEADING: meta("SCC", 8000)},
        graph_passages={SCC_LEADING: Passage(uuid4(), 12, 12, "leading test", ["graph"], 0)},
        rerank_scores={},
        timings_ms={},
    )
    base.update(overrides)
    return Candidates(**base)


def ids(hits):
    return [h.case_id for h in hits]


def test_phase2_config_matches_plain_fusion():
    c = candidates()
    assert ids(rank(c, PHASE2_CONFIG)) == ids(fuse(c.lexical, c.vector))


def test_graph_expansion_adds_case_cited_by_top_results_with_its_passage():
    ranked = rank(candidates(), RankingConfig(graph_weight=1.0))

    leading = next(h for h in ranked if h.case_id == SCC_LEADING)
    assert leading.graph_rank == 1 and leading.citing_seeds == 3
    assert leading.passages[0].para_no == 12
    assert leading.score == 1 / (RRF_K + 1)


def test_graph_min_support_and_seed_cutoff():
    c = candidates(seed_ranks={SCC_LEADING: [1, 25]})
    assert SCC_LEADING not in ids(rank(c, RankingConfig(graph_weight=1.0, seed_cases=20, graph_min_support=2)))
    assert SCC_LEADING in ids(rank(c, RankingConfig(graph_weight=1.0, seed_cases=30, graph_min_support=2)))


def test_priors_favour_higher_court_and_more_cited_authority():
    c = candidates(lexical=[hit(TRIBUNAL, "lexical", 1)], vector=[hit(FC_1, "vector", 1)], seed_ranks={})
    assert ids(rank(c, PHASE2_CONFIG))[0] == TRIBUNAL
    assert ids(rank(c, RankingConfig(court_weight=0.01)))[0] == FC_1


def test_rerank_reorders_head_and_leaves_tail():
    c = candidates(rerank_scores={FC_3: 9.0, FC_1: 1.0, FC_2: 0.5})
    before = ids(rank(c, PHASE2_CONFIG))

    after = rank(c, RankingConfig(rerank_weight=5.0, rerank_depth=3))

    assert after[0].case_id == FC_3 and after[0].rerank_score == 9.0
    assert ids(after)[3:] == before[3:]


def test_missing_rerank_scores_leave_order_unchanged():
    c = candidates()
    assert ids(rank(c, RankingConfig(rerank_weight=2.0))) == ids(rank(c, PHASE2_CONFIG))
