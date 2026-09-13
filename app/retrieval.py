"""Case retrieval: hybrid BM25 + vector candidates, citation-graph expansion, authority
priors and cross-encoder reranking.

Split in two so ranking can be tuned cheaply:
- gather(): everything that touches the database or GPU, once per query.
- rank(): a pure function of the gathered candidates and a RankingConfig.
"""

import math
import time
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Literal
from uuid import UUID

import psycopg
from pgvector import HalfVector

from app.embeddings import embed
from app.reranker import score_pairs

Mode = Literal["hybrid", "lexical", "vector"]

RRF_K = 60
CHUNK_CANDIDATES = 200
PASSAGES_PER_CASE = 3
BM25_INDEX = "case_chunks_bm25_idx"

# Candidate pools gathered once per query; configs choose how much of each to use.
MAX_SEEDS = 30          # top fused cases whose outbound citations are followed
RERANK_BASE_POOL = 40   # top fused cases scored by the cross-encoder
RERANK_GRAPH_POOL = 20  # best-supported graph cases also scored
RERANK_PASSAGES = 2     # passages per case sent to the cross-encoder

COURT_PRIOR = {"SCC": 1.0, "FCA": 0.6, "CMAC": 0.5, "FC": 0.4, "TCC": 0.4}
TRIBUNAL_PRIOR = 0.2
CITED_BY_NORM = math.log1p(10_000)


@dataclass(frozen=True)
class RankingConfig:
    graph_weight: float = 0.0       # RRF weight of the citation-graph list; 0 disables expansion
    seed_cases: int = 20
    graph_min_support: int = 2      # a case must be cited by this many seed cases
    court_weight: float = 0.0       # additive prior by court level
    citation_weight: float = 0.0    # additive prior by in-corpus citation count
    rerank_weight: float = 0.0      # RRF weight of the cross-encoder rank; 0 disables reranking
    rerank_depth: int = 40


PHASE2_CONFIG = RankingConfig()
# Chosen on the eval tune split only (scripts/eval_retrieval.py --tune): no group below
# phase 2, smallest weights among near-ties. Results in eval/runs/phase4.json and README.
DEFAULT_CONFIG = RankingConfig(
    graph_weight=1.0, seed_cases=20, graph_min_support=2,
    court_weight=0.008, citation_weight=0.004,
    rerank_weight=0.5, rerank_depth=40,
)


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: UUID
    case_id: UUID
    para_no: int | None
    para_end: int | None
    text: str
    source: Literal["lexical", "vector"]
    rank: int


@dataclass
class Passage:
    chunk_id: UUID
    para_no: int | None
    para_end: int | None
    text: str
    matched_by: list[str]
    best_rank: int


@dataclass
class CaseHit:
    case_id: UUID
    score: float = 0.0
    lexical_rank: int | None = None
    vector_rank: int | None = None
    graph_rank: int | None = None
    citing_seeds: int = 0
    rerank_score: float | None = None
    passages: list[Passage] = field(default_factory=list)


@dataclass(frozen=True)
class CaseMeta:
    citation: str | None
    citation2: str | None
    style_of_cause: str | None
    court: str | None
    decision_date: date | None
    url: str | None
    language: str | None
    cited_by_count: int


@dataclass
class Candidates:
    lexical: list[ChunkHit]
    vector: list[ChunkHit]
    seed_ranks: dict[UUID, list[int]]     # cited case -> fused ranks of the seed cases citing it
    meta: dict[UUID, CaseMeta]
    graph_passages: dict[UUID, Passage]   # best passage for cases reached only through the graph
    rerank_scores: dict[UUID, float]      # best cross-encoder score per case in the rerank pool
    timings_ms: dict[str, float]


@dataclass
class CaseResult:
    case_id: UUID
    citation: str | None
    citation2: str | None
    style_of_cause: str | None
    court: str | None
    decision_date: date | None
    url: str | None
    language: str | None
    cited_by_count: int
    score: float
    lexical_rank: int | None
    vector_rank: int | None
    graph_rank: int | None
    citing_seeds: int
    rerank_score: float | None
    passages: list[Passage]


@dataclass
class SearchResult:
    cases: list[CaseResult]
    timings_ms: dict[str, float]


def fuse(lexical: list[ChunkHit], vector: list[ChunkHit], rrf_k: int = RRF_K) -> list[CaseHit]:
    """Reciprocal Rank Fusion at case level.

    Each list is collapsed to case ranks by first (best) appearance, so a case whose
    passages surface in both lists outranks one that only one retriever found.
    """
    cases: dict[UUID, CaseHit] = {}
    passages: dict[UUID, dict[UUID, Passage]] = {}

    for hits, rank_attr in ((lexical, "lexical_rank"), (vector, "vector_rank")):
        case_rank = 0
        for hit in hits:
            case = cases.setdefault(hit.case_id, CaseHit(hit.case_id))
            if getattr(case, rank_attr) is None:
                case_rank += 1
                setattr(case, rank_attr, case_rank)
                case.score += 1 / (rrf_k + case_rank)

            by_chunk = passages.setdefault(hit.case_id, {})
            existing = by_chunk.get(hit.chunk_id)
            if existing:
                existing.matched_by.append(hit.source)
                existing.best_rank = min(existing.best_rank, hit.rank)
            else:
                by_chunk[hit.chunk_id] = Passage(
                    hit.chunk_id, hit.para_no, hit.para_end, hit.text, [hit.source], hit.rank
                )

    ranked = sorted(cases.values(), key=lambda c: c.score, reverse=True)
    for case in ranked:
        case.passages = sorted(passages[case.case_id].values(), key=lambda p: p.best_rank)[
            :PASSAGES_PER_CASE
        ]
    return ranked


def rank(candidates: Candidates, config: RankingConfig) -> list[CaseHit]:
    """Order cases from gathered candidates. Pure: no database or model calls."""
    ranked = fuse(candidates.lexical, candidates.vector)
    by_id = {hit.case_id: hit for hit in ranked}

    if config.graph_weight > 0:
        support = {}
        for case_id, seed_ranks in candidates.seed_ranks.items():
            citing = [r for r in seed_ranks if r <= config.seed_cases]
            if len(citing) >= config.graph_min_support and case_id in candidates.meta:
                support[case_id] = (sum(1 / (RRF_K + r) for r in citing), len(citing))
        ordered = sorted(support, key=lambda c: support[c][0], reverse=True)
        for graph_rank, case_id in enumerate(ordered, 1):
            hit = by_id.get(case_id)
            if hit is None:
                graph_passage = candidates.graph_passages.get(case_id)
                hit = by_id[case_id] = CaseHit(case_id, passages=[graph_passage] if graph_passage else [])
            hit.graph_rank = graph_rank
            hit.citing_seeds = support[case_id][1]
            hit.score += config.graph_weight / (RRF_K + graph_rank)

    if config.court_weight or config.citation_weight:
        for hit in by_id.values():
            meta = candidates.meta.get(hit.case_id)
            if meta:
                hit.score += config.court_weight * COURT_PRIOR.get(meta.court, TRIBUNAL_PRIOR)
                hit.score += config.citation_weight * math.log1p(meta.cited_by_count) / CITED_BY_NORM

    ordered = sorted(by_id.values(), key=lambda h: h.score, reverse=True)

    if config.rerank_weight > 0 and candidates.rerank_scores:
        head, tail = ordered[: config.rerank_depth], ordered[config.rerank_depth:]
        scored = sorted(
            (h for h in head if h.case_id in candidates.rerank_scores),
            key=lambda h: candidates.rerank_scores[h.case_id],
            reverse=True,
        )
        rerank_rank = {h.case_id: i for i, h in enumerate(scored, 1)}
        for position, hit in enumerate(head, 1):
            hit.rerank_score = candidates.rerank_scores.get(hit.case_id)
            hit.score = 1 / (RRF_K + position)
            if hit.case_id in rerank_rank:
                hit.score += config.rerank_weight / (RRF_K + rerank_rank[hit.case_id])
        ordered = sorted(head, key=lambda h: h.score, reverse=True) + tail

    return ordered


def prepare_session(conn: psycopg.Connection) -> None:
    # Keep walking the HNSW graph when filters discard candidates.
    conn.execute("SET hnsw.ef_search = 200")
    conn.execute("SET hnsw.iterative_scan = relaxed_order")
    conn.commit()


def gather(
    conn: psycopg.Connection,
    query: str,
    *,
    mode: Mode = "hybrid",
    courts: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Candidates:
    """Retrieve everything rank() may use. Graph and reranking only run in hybrid mode."""
    timings: dict[str, float] = {}
    clauses, params = _case_filter(courts, date_from, date_to)
    chunk_filter = f"WHERE ch.case_id IN (SELECT id FROM cases WHERE {clauses})" if clauses else ""

    lexical: list[ChunkHit] = []
    vector: list[ChunkHit] = []
    query_vector = None

    if mode in ("hybrid", "lexical"):
        started = time.perf_counter()
        lexical = _lexical(conn, query, CHUNK_CANDIDATES, chunk_filter, params)
        timings["lexical"] = _ms(started)

    if mode in ("hybrid", "vector"):
        started = time.perf_counter()
        query_vector = HalfVector(embed([query])[0])
        timings["embed"] = _ms(started)
        started = time.perf_counter()
        vector = _vector(conn, query_vector, CHUNK_CANDIDATES, chunk_filter, params)
        timings["vector"] = _ms(started)

    base = fuse(lexical, vector)
    seed_ranks: dict[UUID, list[int]] = {}
    graph_passages: dict[UUID, Passage] = {}
    rerank_scores: dict[UUID, float] = {}

    if mode == "hybrid" and base:
        started = time.perf_counter()
        seed_ranks = _cited_by_seeds(conn, [h.case_id for h in base[:MAX_SEEDS]], clauses, params)
        timings["graph"] = _ms(started)

    case_ids = {h.case_id for h in base} | set(seed_ranks)
    meta = _case_meta(conn, list(case_ids))

    if mode == "hybrid" and base:
        started = time.perf_counter()
        in_base = {h.case_id for h in base}
        supported = sorted(
            (c for c, ranks in seed_ranks.items() if len(ranks) >= 2 and c not in in_base),
            key=lambda c: sum(1 / (RRF_K + r) for r in seed_ranks[c]),
            reverse=True,
        )
        graph_passages = _best_passages(conn, supported, query_vector)
        timings["graph_passages"] = _ms(started)

        started = time.perf_counter()
        pool = [(h.case_id, h.passages[:RERANK_PASSAGES]) for h in base[:RERANK_BASE_POOL]]
        pool += [(c, [graph_passages[c]]) for c in supported[:RERANK_GRAPH_POOL] if c in graph_passages]
        pairs = [(case_id, p.text) for case_id, passages in pool for p in passages]
        scores = score_pairs(query, [text for _, text in pairs])
        for (case_id, _), score in zip(pairs, scores):
            rerank_scores[case_id] = max(score, rerank_scores.get(case_id, float("-inf")))
        timings["rerank"] = _ms(started)

    conn.commit()
    return Candidates(lexical, vector, seed_ranks, meta, graph_passages, rerank_scores, timings)


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    k: int = 10,
    mode: Mode = "hybrid",
    courts: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    config: RankingConfig = DEFAULT_CONFIG,
) -> SearchResult:
    candidates = gather(conn, query, mode=mode, courts=courts, date_from=date_from, date_to=date_to)
    started = time.perf_counter()
    if mode != "hybrid":
        config = replace(config, graph_weight=0.0, rerank_weight=0.0)
    top = rank(candidates, config)[:k]
    cases = [to_result(hit, candidates.meta[hit.case_id]) for hit in top]
    timings = {**candidates.timings_ms, "rank": _ms(started)}
    return SearchResult(cases=cases, timings_ms=timings)


def to_result(hit: CaseHit, meta: CaseMeta) -> CaseResult:
    return CaseResult(
        hit.case_id, meta.citation, meta.citation2, meta.style_of_cause, meta.court, meta.decision_date,
        meta.url, meta.language, meta.cited_by_count, hit.score, hit.lexical_rank, hit.vector_rank,
        hit.graph_rank, hit.citing_seeds, hit.rerank_score, hit.passages,
    )


def _case_filter(courts, date_from, date_to) -> tuple[str, dict]:
    clauses, params = [], {}
    if courts:
        clauses.append("court = ANY(%(courts)s)")
        params["courts"] = courts
    if date_from:
        clauses.append("decision_date >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        clauses.append("decision_date <= %(date_to)s")
        params["date_to"] = date_to
    return " AND ".join(clauses), params


def _lexical(conn, query: str, n: int, filters: str, params: dict) -> list[ChunkHit]:
    # pg_textsearch scores are negative BM25 (lower is better); 0 means no term matched.
    rows = conn.execute(
        f"""
        SELECT ch.id, ch.case_id, ch.para_no, ch.para_end, ch.text,
               ch.text <@> to_bm25query(%(q)s, '{BM25_INDEX}') AS score
        FROM case_chunks ch
        {filters}
        ORDER BY ch.text <@> to_bm25query(%(q)s, '{BM25_INDEX}')
        LIMIT %(n)s
        """,
        {**params, "q": query, "n": n},
    ).fetchall()
    matched = [r for r in rows if r[5] < 0]
    return [
        ChunkHit(r[0], r[1], r[2], r[3], r[4], "lexical", rank)
        for rank, r in enumerate(matched, 1)
    ]


def _vector(conn, query_vector: HalfVector, n: int, filters: str, params: dict) -> list[ChunkHit]:
    rows = conn.execute(
        f"""
        SELECT ch.id, ch.case_id, ch.para_no, ch.para_end, ch.text,
               ch.embedding <=> %(v)s AS distance
        FROM case_chunks ch
        {filters}
        ORDER BY ch.embedding <=> %(v)s
        LIMIT %(n)s
        """,
        {**params, "v": query_vector, "n": n},
    ).fetchall()
    # relaxed_order iterative scans can return slightly out-of-order rows.
    rows.sort(key=lambda r: r[5])
    return [
        ChunkHit(r[0], r[1], r[2], r[3], r[4], "vector", rank)
        for rank, r in enumerate(rows, 1)
    ]


def _cited_by_seeds(conn, seeds: list[UUID], clauses: str, params: dict) -> dict[UUID, list[int]]:
    seed_rank = {case_id: i for i, case_id in enumerate(seeds, 1)}
    target_filter = f"AND e.dst_id IN (SELECT id FROM cases WHERE {clauses})" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT e.src_id, e.dst_id FROM citation_edges e
        WHERE e.edge_kind = 'case_cites_case' AND e.src_id = ANY(%(seeds)s) {target_filter}
        """,
        {**params, "seeds": seeds},
    ).fetchall()
    cited: dict[UUID, list[int]] = {}
    for src_id, dst_id in rows:
        cited.setdefault(dst_id, []).append(seed_rank[src_id])
    return cited


def _best_passages(conn, case_ids: list[UUID], query_vector: HalfVector | None) -> dict[UUID, Passage]:
    """The chunk closest to the query in each case (for cases found only via citations)."""
    if not case_ids or query_vector is None:
        return {}
    rows = conn.execute(
        """
        SELECT DISTINCT ON (case_id) case_id, id, para_no, para_end, text
        FROM case_chunks WHERE case_id = ANY(%(ids)s)
        ORDER BY case_id, embedding <=> %(v)s
        """,
        {"ids": case_ids, "v": query_vector},
    ).fetchall()
    return {r[0]: Passage(r[1], r[2], r[3], r[4], ["graph"], 0) for r in rows}


def _case_meta(conn, case_ids: list[UUID]) -> dict[UUID, CaseMeta]:
    if not case_ids:
        return {}
    rows = conn.execute(
        """
        SELECT id, citation, citation2, style_of_cause, court, decision_date, url_official,
               language, cited_by_count
        FROM cases WHERE id = ANY(%s)
        """,
        (case_ids,),
    ).fetchall()
    return {r[0]: CaseMeta(*r[1:]) for r in rows}


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
