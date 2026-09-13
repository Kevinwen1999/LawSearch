"""Hybrid case retrieval: BM25 and vector search over chunks, fused per case with RRF."""

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Literal
from uuid import UUID

import psycopg
from pgvector import HalfVector

from app.embeddings import embed

Mode = Literal["hybrid", "lexical", "vector"]

RRF_K = 60
CHUNK_CANDIDATES = 200
PASSAGES_PER_CASE = 3
BM25_INDEX = "case_chunks_bm25_idx"


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
    passages: list[Passage] = field(default_factory=list)


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
    score: float
    lexical_rank: int | None
    vector_rank: int | None
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


def prepare_session(conn: psycopg.Connection) -> None:
    # Keep walking the HNSW graph when filters discard candidates.
    conn.execute("SET hnsw.ef_search = 200")
    conn.execute("SET hnsw.iterative_scan = relaxed_order")
    conn.commit()


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    k: int = 10,
    mode: Mode = "hybrid",
    courts: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    candidates: int = CHUNK_CANDIDATES,
) -> SearchResult:
    timings: dict[str, float] = {}
    filters, params = _case_filter(courts, date_from, date_to)

    lexical: list[ChunkHit] = []
    vector: list[ChunkHit] = []

    if mode in ("hybrid", "lexical"):
        started = time.perf_counter()
        lexical = _lexical(conn, query, candidates, filters, params)
        timings["lexical"] = _ms(started)

    if mode in ("hybrid", "vector"):
        started = time.perf_counter()
        query_vector = HalfVector(embed([query])[0])
        timings["embed"] = _ms(started)
        started = time.perf_counter()
        vector = _vector(conn, query_vector, candidates, filters, params)
        timings["vector"] = _ms(started)

    started = time.perf_counter()
    top = fuse(lexical, vector)[:k]
    cases = _attach_metadata(conn, top)
    timings["fuse_and_fetch"] = _ms(started)
    conn.commit()

    return SearchResult(cases=cases, timings_ms=timings)


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
    if not clauses:
        return "", params
    return f"WHERE ch.case_id IN (SELECT id FROM cases WHERE {' AND '.join(clauses)})", params


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


def _attach_metadata(conn, hits: list[CaseHit]) -> list[CaseResult]:
    if not hits:
        return []
    rows = conn.execute(
        """
        SELECT id, citation, citation2, style_of_cause, court, decision_date,
               url_official, language
        FROM cases WHERE id = ANY(%s)
        """,
        ([h.case_id for h in hits],),
    ).fetchall()
    meta = {r[0]: r[1:] for r in rows}
    return [
        CaseResult(h.case_id, *meta[h.case_id], h.score, h.lexical_rank, h.vector_rank, h.passages)
        for h in hits
    ]


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
