"""Find a corpus case for the case-brief page: by citation, by name, or by keywords.

Tried in that order, stopping at the first that finds anything, so "2013 ONCA 585" never
drowns in keyword matches and "Kazemi" finds R. v. Kazemi without a semantic search.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

import psycopg

from app import retrieval
from app.citations import canonical, case_citations
from app.config import settings
from app.filac import PROMPT_VERSION, USER_SOURCES

# Names are short; a longer query is a description or pasted text, which only keywords can match.
MAX_NAME_QUERY_CHARS = 200
MAX_KEYWORD_QUERY_CHARS = 4000

Match = Literal["citation", "name", "keywords"]


@dataclass
class LookupHit:
    case_id: UUID
    citation: str | None
    style_of_cause: str | None
    court: str | None
    decision_date: date | None
    match: Match
    has_brief: bool = False


_COLUMNS = "id, citation, style_of_cause, court, decision_date"


def lookup(conn: psycopg.Connection, query: str, limit: int = 10) -> list[LookupHit]:
    query = query.strip()
    if not query:
        return []
    hits = by_citation(conn, query, limit)
    if not hits and len(query) <= MAX_NAME_QUERY_CHARS:
        hits = by_name(conn, query, limit)
    if not hits:
        hits = by_keywords(conn, query, limit)
    return mark_briefs(conn, hits)


def by_citation(conn: psycopg.Connection, query: str, limit: int) -> list[LookupHit]:
    citations = list(dict.fromkeys(canonical(c) for c in case_citations(query)))
    if not citations:
        return []
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM cases WHERE (citation = ANY(%(c)s) OR citation2 = ANY(%(c)s)) "
        "AND source <> ALL(%(user)s) ORDER BY cited_by_count DESC LIMIT %(limit)s",
        {"c": citations, "user": list(USER_SOURCES), "limit": limit},
    ).fetchall()
    return [LookupHit(*row, match="citation") for row in rows]


def by_name(conn: psycopg.Connection, query: str, limit: int) -> list[LookupHit]:
    # `<%` is pg_trgm's word similarity: the query against the best-matching stretch of the name,
    # so "Kazemi" matches "R. v. Kazemi" fully. Uses the trigram index on style_of_cause.
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM cases WHERE %(q)s <%% style_of_cause AND source <> ALL(%(user)s) "
        "ORDER BY word_similarity(%(q)s, style_of_cause) DESC, cited_by_count DESC LIMIT %(limit)s",
        {"q": query, "user": list(USER_SOURCES), "limit": limit},
    ).fetchall()
    return [LookupHit(*row, match="name") for row in rows]


def by_keywords(conn: psycopg.Connection, query: str, limit: int) -> list[LookupHit]:
    result = retrieval.search(conn, query[:MAX_KEYWORD_QUERY_CHARS], k=limit, k_sections=0)
    return [
        LookupHit(c.case_id, c.citation, c.style_of_cause, c.court, c.decision_date, match="keywords")
        for c in result.cases
    ]


def mark_briefs(conn: psycopg.Connection, hits: list[LookupHit]) -> list[LookupHit]:
    if hits:
        briefed = {r[0] for r in conn.execute(
            "SELECT case_id FROM filac_summaries WHERE case_id = ANY(%s) AND prompt_version = %s AND model = %s",
            ([h.case_id for h in hits], PROMPT_VERSION, settings.filac_model),
        )}
        for hit in hits:
            hit.has_brief = hit.case_id in briefed
    return hits
