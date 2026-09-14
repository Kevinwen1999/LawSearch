"""Statute section retrieval: BM25 + vector over legislation sections, plus the sections that
the top case results actually cite. Same gather/rank split as app/retrieval.py."""

import math
import time
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from pgvector import HalfVector

RRF_K = 60
SECTION_CANDIDATES = 100
BM25_INDEX = "legislation_sections_bm25_idx"
CITED_BY_NORM = math.log1p(20_000)

SectionKey = tuple[UUID, str]  # (legislation_id, section_no)


@dataclass(frozen=True)
class SectionConfig:
    graph_weight: float = 1.0       # RRF weight of "cited by the top case results"; 0 disables
    min_citing_cases: int = 2
    citation_weight: float = 0.004  # additive prior by how many decisions cite the section


@dataclass(frozen=True)
class SectionChunkRow:
    chunk_id: UUID
    legislation_id: UUID
    code: str
    kind: str
    title: str
    citation: str | None
    consolidation_date: date | None
    section_no: str
    section_label: str
    marginal_note: str | None
    hierarchy_path: str | None
    text: str
    url: str | None
    in_force_start: date | None
    cited_by_count: int


@dataclass
class SectionCandidates:
    lexical: list[UUID]                     # chunk ids, best first
    vector: list[UUID]
    seed_ranks: dict[SectionKey, list[int]]  # section -> ranks of top cases citing it
    seed_chunks: dict[SectionKey, UUID]      # the chunk those cases cite
    rows: dict[UUID, SectionChunkRow]
    timings_ms: dict[str, float]


@dataclass
class SectionResult:
    chunk_id: UUID
    code: str
    kind: str
    title: str
    citation: str | None
    consolidation_date: date | None
    section_no: str
    section_label: str
    marginal_note: str | None
    hierarchy_path: str | None
    text: str
    url: str | None
    in_force_start: date | None
    cited_by_count: int
    score: float
    lexical_rank: int | None = None
    vector_rank: int | None = None
    citing_cases: int = 0


def gather_sections(conn, query: str, query_vector: HalfVector | None, seed_case_ids: list[UUID]) -> SectionCandidates:
    timings: dict[str, float] = {}

    started = time.perf_counter()
    rows = conn.execute(
        f"""
        SELECT id, text <@> to_bm25query(%(q)s, '{BM25_INDEX}') AS score FROM legislation_sections
        ORDER BY text <@> to_bm25query(%(q)s, '{BM25_INDEX}') LIMIT %(n)s
        """,
        {"q": query, "n": SECTION_CANDIDATES},
    ).fetchall()
    lexical = [r[0] for r in rows if r[1] < 0]
    timings["sections_lexical"] = _ms(started)

    vector: list[UUID] = []
    if query_vector is not None:
        started = time.perf_counter()
        rows = conn.execute(
            "SELECT id, embedding <=> %(v)s AS d FROM legislation_sections ORDER BY embedding <=> %(v)s LIMIT %(n)s",
            {"v": query_vector, "n": SECTION_CANDIDATES},
        ).fetchall()
        vector = [r[0] for r in sorted(rows, key=lambda r: r[1])]
        timings["sections_vector"] = _ms(started)

    started = time.perf_counter()
    seed_rank = {case_id: i for i, case_id in enumerate(seed_case_ids, 1)}
    seed_ranks: dict[SectionKey, list[int]] = {}
    seed_chunks: dict[SectionKey, UUID] = {}
    if seed_case_ids:
        for case_id, legislation_id, section_no, chunk_id in conn.execute(
            """
            SELECT e.src_id, s.legislation_id, s.section_no, min(s.id::text)::uuid
            FROM citation_edges e JOIN legislation_sections s ON s.id = e.dst_id
            WHERE e.edge_kind = 'case_cites_statute' AND e.src_id = ANY(%s)
            GROUP BY e.src_id, s.legislation_id, s.section_no
            """,
            (seed_case_ids,),
        ):
            key = (legislation_id, section_no)
            seed_ranks.setdefault(key, []).append(seed_rank[case_id])
            seed_chunks.setdefault(key, chunk_id)
    timings["sections_graph"] = _ms(started)

    ids = set(lexical) | set(vector) | set(seed_chunks.values())
    return SectionCandidates(lexical, vector, seed_ranks, seed_chunks, _rows(conn, list(ids)), timings)


def rank_sections(candidates: SectionCandidates, config: SectionConfig = SectionConfig()) -> list[SectionResult]:
    """Group chunks into sections and fuse lexical, vector and citing-case ranks. Pure."""
    results: dict[SectionKey, SectionResult] = {}

    def result_for(chunk_id: UUID) -> SectionResult | None:
        row = candidates.rows.get(chunk_id)
        if row is None:
            return None
        key = (row.legislation_id, row.section_no)
        if key not in results:
            results[key] = SectionResult(
                row.chunk_id, row.code, row.kind, row.title, row.citation, row.consolidation_date,
                row.section_no, row.section_label, row.marginal_note, row.hierarchy_path, row.text,
                row.url, row.in_force_start, row.cited_by_count, 0.0,
            )
        return results[key]

    for chunk_ids, attr in ((candidates.lexical, "lexical_rank"), (candidates.vector, "vector_rank")):
        rank = 0
        for chunk_id in chunk_ids:
            result = result_for(chunk_id)
            if result is None or getattr(result, attr) is not None:
                continue
            rank += 1
            setattr(result, attr, rank)
            result.score += 1 / (RRF_K + rank)

    if config.graph_weight > 0:
        support = {
            key: sum(1 / (RRF_K + r) for r in ranks)
            for key, ranks in candidates.seed_ranks.items()
            if len(ranks) >= config.min_citing_cases
        }
        for graph_rank, key in enumerate(sorted(support, key=support.get, reverse=True), 1):
            result = result_for(candidates.seed_chunks[key])
            if result is None:
                continue
            result.citing_cases = len(candidates.seed_ranks[key])
            result.score += config.graph_weight / (RRF_K + graph_rank)

    if config.citation_weight:
        for result in results.values():
            result.score += config.citation_weight * math.log1p(result.cited_by_count) / CITED_BY_NORM

    return sorted(results.values(), key=lambda r: r.score, reverse=True)


def _rows(conn, chunk_ids: list[UUID]) -> dict[UUID, SectionChunkRow]:
    if not chunk_ids:
        return {}
    rows = conn.execute(
        """
        SELECT s.id, s.legislation_id, l.code, l.kind, l.title, l.citation, l.consolidation_date,
               s.section_no, s.section_label, s.marginal_note, s.hierarchy_path, s.text,
               s.url_official, s.in_force_start, s.cited_by_count
        FROM legislation_sections s JOIN legislation l ON l.id = s.legislation_id
        WHERE s.id = ANY(%s)
        """,
        (chunk_ids,),
    ).fetchall()
    return {r[0]: SectionChunkRow(*r) for r in rows}


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
