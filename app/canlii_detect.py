"""Phase 8: detect Ontario Superior Court and tribunal decisions on CanLII that are likely
relevant to a scenario, for link-out only (no text, no FILAC).

CanLII's API has no text search, so detection goes through the citator: the corpus cases our own
retrieval ranked highest are the seeds; Ontario decisions outside the corpus that cite them are
the candidates. Candidates citing several seeds, or citing seeds few others cite, pool first
(a seed like Housen, cited by ~2,000 Ontario decisions on standard of review alone, says little
about topic). The pool is then reranked by the cross-encoder against CanLII's own keywords and
topics for each candidate — the only descriptive text the API offers.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from app.canlii import BudgetExhausted, CanLIIClient, CanLIIError

# Our court code -> CanLII case database, for courts whose neutral citation maps to a CanLII
# case id ("2019 ONCA 830" -> onca/2019onca830). Older decisions cited only by report or docket
# number ([1951] SCR 470, C36847) have CanLII ids we can't derive, so they can't seed.
SEED_DATABASES = {"SCC": "csc-scc", "ONCA": "onca", "FCA": "fca", "FC": "fct", "TCC": "cci-tcc"}
CANDIDATE_JURISDICTION = "on"
IN_CORPUS_DATABASES = {"onca"}  # every ONCA decision since 1998 is already in our corpus

MAX_SEEDS = 8          # citator queries per scenario
POOL_SIZE = 12         # metadata queries per scenario
RANK_DECAY = 0.1       # seed weight falls off gently with its retrieval rank
# Cross-encoder logit floor, scoring the fingerprint query against CanLII's keywords. Calibrated
# on the three Ontario eval scenarios (2026-09-24): on-point decisions scored -4.2 and up,
# off-topic ones sharing a seed (a Charter-evidence ruling citing a slip-and-fall appeal)
# mostly below. Re-checked 2026-09-25 on on-002/003/004 with per-issue seeds: everything above
# the floor was employment/termination-clause case law, the weakest (-2 to -3.3) only loosely on
# point. Loose on purpose: these are leads to check, not holdings.
MIN_RERANK = -4.0

_NEUTRAL = re.compile(r"(\d{4}) ([A-Z]+) (\d+)")


@dataclass(frozen=True)
class Seed:
    citation: str
    title: str | None
    database_id: str
    case_id: str


@dataclass
class Candidate:
    database_id: str
    case_id: str
    title: str | None
    citation: str | None
    cites: list[Seed]
    cocite_score: float
    court_name: str | None = None
    decision_date: date | None = None
    url: str | None = None
    keywords: str | None = None
    topics: str | None = None
    rerank_score: float | None = None

    @property
    def year(self) -> int:
        return int(self.case_id[:4]) if self.case_id[:4].isdigit() else 0


@dataclass
class Detection:
    status: str  # "ok" | "partial" | "no_seeds" | "disabled" | "budget_exhausted" | "error"
    message: str | None
    candidates: list[Candidate]
    seeds: list[Seed] = field(default_factory=list)
    queries_sent: int = 0


def to_seed(citation: str | None, court: str | None, title: str | None) -> Seed | None:
    database_id = SEED_DATABASES.get(court or "")
    m = _NEUTRAL.fullmatch(citation or "")
    if not database_id or not m or m.group(2) != court:
        return None
    year, code, number = m.groups()
    return Seed(citation, title, database_id, f"{year}{code.lower()}{number}")


def unique_seeds(seeds: list[Seed]) -> list[Seed]:
    """First occurrence of each case: a case leading several issue groups is one seed, not three."""
    return list(dict.fromkeys(seeds))


def pool(
    citing_by_seed: list[tuple[Seed, list[dict]]], candidate_databases: set[str], size: int = POOL_SIZE
) -> list[Candidate]:
    """Co-citation pool. Each seed (in retrieval order) contributes its weight to every
    candidate citing it; weight = rank decay / sqrt(number of candidates citing that seed)."""
    by_id: dict[tuple[str, str], Candidate] = {}
    for rank, (seed, citing) in enumerate(citing_by_seed):
        relevant = {
            (c["databaseId"], c["caseId"]): c for c in citing if c["databaseId"] in candidate_databases
        }
        if not relevant:
            continue
        weight = 1 / (1 + RANK_DECAY * rank) / math.sqrt(len(relevant))
        for key, c in relevant.items():
            cand = by_id.get(key)
            if cand is None:
                cand = by_id[key] = Candidate(key[0], key[1], c.get("title"), c.get("citation"), [], 0.0)
            cand.cites.append(seed)
            cand.cocite_score += weight
    ranked = sorted(by_id.values(), key=lambda c: (-c.cocite_score, -len(c.cites), -c.year, c.case_id))
    return ranked[:size]


def describe(c: Candidate) -> str:
    """What the cross-encoder sees: CanLII's title, topics and keywords for the decision."""
    return ". ".join(p for p in (c.title, c.topics, c.keywords) if p)


def detect(
    client: CanLIIClient,
    query: str,
    seeds: list[Seed],
    score: Callable[[str, list[str]], list[float]],
    *,
    k: int = 8,
) -> Detection:
    if not client.configured:
        return Detection("disabled", "CanLII detection is off: CANLII_API_KEY is not set.", [])
    seeds = unique_seeds(seeds)[:MAX_SEEDS]
    if not seeds:
        return Detection(
            "no_seeds",
            "None of the top cases has a neutral citation CanLII can look up, so there was "
            "nothing to trace citations from.",
            [],
        )

    status, message = "ok", None
    citing_by_seed: list[tuple[Seed, list[dict]]] = []
    candidates: list[Candidate] = []
    try:
        databases = client.databases()
        candidate_dbs = {
            db for db, info in databases.items()
            if info["jurisdiction"] == CANDIDATE_JURISDICTION and db not in IN_CORPUS_DATABASES
        }
        for seed in seeds:
            citing = client.citing_cases(seed.database_id, seed.case_id)
            if citing is not None:
                citing_by_seed.append((seed, citing))
        candidates = pool(citing_by_seed, candidate_dbs)
        for cand in candidates:
            info = databases.get(cand.database_id, {})
            cand.court_name = info.get("name")
            # CanLII's long-URL pattern, so a candidate still links out if its metadata call
            # never happens (budget ran out part-way through the pool).
            cand.url = (
                f"https://www.canlii.org/en/{info.get('jurisdiction', CANDIDATE_JURISDICTION)}/"
                f"{cand.database_id}/doc/{cand.year}/{cand.case_id}/{cand.case_id}.html"
            )
            if meta := client.case_metadata(cand.database_id, cand.case_id):
                _apply_metadata(cand, meta)
    except BudgetExhausted as exc:
        status, message = "budget_exhausted", str(exc)
    except CanLIIError as exc:
        status, message = "error", str(exc)

    if status != "ok":
        if not candidates:
            return Detection(status, message, [], seeds, client.queries_sent)
        # Show what we have: candidates already pooled, some possibly without metadata.
        status = "partial"

    if candidates:
        for cand, s in zip(candidates, score(query, [describe(c) for c in candidates])):
            cand.rerank_score = s
        candidates = sorted(
            (c for c in candidates if c.rerank_score >= MIN_RERANK), key=lambda c: -c.rerank_score
        )
    return Detection(status, message, candidates[:k], seeds, client.queries_sent)


def _apply_metadata(cand: Candidate, meta: dict) -> None:
    cand.title = meta.get("title") or cand.title
    cand.citation = meta.get("citation") or cand.citation
    cand.url = meta.get("url") or meta.get("longUrl") or cand.url
    cand.keywords = meta.get("keywords") or None
    cand.topics = meta.get("topics") or None
    if meta.get("decisionDate"):
        try:
            cand.decision_date = date.fromisoformat(meta["decisionDate"])
        except ValueError:
            pass
