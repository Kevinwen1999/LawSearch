"""Authorities the scenario's top cases keep citing that the results don't show.

Bardal v. Globe & Mail (1960, Ont. H.C.), the reasonable-notice factors case, is named in 66
corpus decisions but isn't in the corpus, so it could never be retrieved and simply didn't
appear. CanLII's citator lists every decision a case cites, in the corpus or not: an authority
cited by several of the scenario's top cases is likely one the reader needs. Each is labelled
in LawSearch (resolved to a corpus row the results didn't include) or not (link-out only).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from app.canlii import BudgetExhausted, CanLIIClient, CanLIIError
from app.canlii_detect import MAX_SEEDS, Seed, unique_seeds
from app.citations import build_onca_name_resolver, onca_party_key, resolve_onca_named

MIN_CITING_SEEDS = 2

_NEUTRAL = re.compile(r"\b(\d{4}) ([A-Z]{2,6}) (\d+)\b")
_SCR = re.compile(r"\[(\d{4})\] (\d) SCR (\d+)")
_CANLII_YEAR = re.compile(r"\b(\d{4}) CanLII\b")


@dataclass
class CitedAuthority:
    database_id: str
    case_id: str
    title: str | None
    citation: str | None
    url: str | None
    cited_by: list[Seed] = field(default_factory=list)
    court_name: str | None = None
    corpus_case_id: UUID | None = None

    def corpus_lookups(self) -> tuple[list[str], tuple[str, str, int] | None]:
        """Corpus citations this CanLII entry could be stored under, plus an ONCA name key for
        pre-2007 Court of Appeal decisions ('2001 CanLII 8589 (ON CA)', no neutral citation)."""
        text = self.citation or ""
        citations = [f"{y} {c} {n}" for y, c, n in _NEUTRAL.findall(text) if c != "CanLII"]
        citations += [f"[{y}] {v} SCR {p}" for y, v, p in _SCR.findall(text)]
        onca_key = None
        if self.database_id == "onca" and (m := _CANLII_YEAR.search(text)):
            onca_key = onca_party_key(self.title or "", int(m.group(1)))
        return citations, onca_key


@dataclass
class CitedResult:
    status: str  # "ok" | "partial" | "no_seeds" | "disabled" | "budget_exhausted" | "error"
    message: str | None
    authorities: list[CitedAuthority]
    seeds: list[Seed] = field(default_factory=list)
    queries_sent: int = 0


def aggregate(cited_by_seed: list[tuple[Seed, list[dict]]], min_citing: int = MIN_CITING_SEEDS) -> list[CitedAuthority]:
    """Authorities cited by at least `min_citing` seeds, most-cited first. Pure."""
    by_id: dict[tuple[str, str], CitedAuthority] = {}
    for seed, cited in cited_by_seed:
        for c in {(c["databaseId"], c["caseId"]): c for c in cited}.values():
            key = (c["databaseId"], c["caseId"])
            if key == (seed.database_id, seed.case_id):
                continue
            auth = by_id.setdefault(key, CitedAuthority(key[0], key[1], c.get("title"), c.get("citation"), c.get("url")))
            auth.cited_by.append(seed)
    kept = [a for a in by_id.values() if len(a.cited_by) >= min_citing]
    return sorted(kept, key=lambda a: (-len(a.cited_by), a.title or ""))


def find_cited(
    client: CanLIIClient,
    seeds: list[Seed],
    resolve: Callable[[list[CitedAuthority]], None],
    *,
    exclude: set[UUID] = frozenset(),
    k: int = 10,
) -> CitedResult:
    """`resolve` sets corpus_case_id on authorities that are in the corpus; those in `exclude`
    (already shown as results) are dropped. Not-in-corpus authorities sort ahead of in-corpus
    ones cited equally often, since they're the ones the reader can't reach any other way."""
    if not client.configured:
        return CitedResult("disabled", "CanLII lookups are off: CANLII_API_KEY is not set.", [])
    seeds = unique_seeds(seeds)[:MAX_SEEDS]
    if not seeds:
        return CitedResult("no_seeds", "None of the top cases can be looked up on CanLII.", [])

    status, message = "ok", None
    cited_by_seed: list[tuple[Seed, list[dict]]] = []
    databases: dict[str, dict] = {}
    try:
        databases = client.databases()
        for seed in seeds:
            if (cited := client.cited_cases(seed.database_id, seed.case_id)) is not None:
                cited_by_seed.append((seed, cited))
    except BudgetExhausted as exc:
        status, message = "budget_exhausted", str(exc)
    except CanLIIError as exc:
        status, message = "error", str(exc)
    if status != "ok":
        if not cited_by_seed:
            return CitedResult(status, message, [], seeds, client.queries_sent)
        status = "partial"

    authorities = aggregate(cited_by_seed)
    resolve(authorities)
    authorities = [a for a in authorities if a.corpus_case_id is None or a.corpus_case_id not in exclude]
    for a in authorities:
        a.court_name = databases.get(a.database_id, {}).get("name")
    authorities.sort(key=lambda a: (-len(a.cited_by), a.corpus_case_id is not None, a.title or ""))
    return CitedResult(status, message, authorities[:k], seeds, client.queries_sent)


def resolve_in_corpus(connection, authorities: list[CitedAuthority]) -> None:
    """Point each authority at its corpus row, if it has one: by neutral or SCR citation, or for a
    pre-2007 ONCA decision by party names and year. `connection` is a connection factory."""
    lookups = {id(a): a.corpus_lookups() for a in authorities}
    citations = sorted({c for cits, _ in lookups.values() for c in cits})
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, citation, citation2 FROM cases WHERE citation = ANY(%(c)s) OR citation2 = ANY(%(c)s)",
            {"c": citations},
        ).fetchall() if citations else []
        by_name = build_onca_name_resolver(conn) if any(key for _, key in lookups.values()) else {}
    by_citation: dict[str, UUID] = {}
    for case_id, citation, citation2 in rows:
        for c in (citation, citation2):
            if c:
                by_citation.setdefault(c, case_id)
    for a in authorities:
        cits, onca_key = lookups[id(a)]
        a.corpus_case_id = next((by_citation[c] for c in cits if c in by_citation), None) or (
            resolve_onca_named(by_name, onca_key) if onca_key else None
        )
