"""Scenario fingerprinting (§5.1): turn a client's scenario prose into a structured object that
drives retrieval, plus the jurisdiction gate that asks before retrieving when the answer would be
unreliable.

search_terms/candidate_statutes are the precise, lexical-search-shaped fields; the whole object
(issues, key_facts included) carries the semantic signal for the embedding side. This step must
never assert that a case exists or predict an outcome — it proposes concepts and terms, not
holdings, so downstream retrieval and FILAC remain the only places an authority is ever asserted.
"""

from dataclasses import dataclass

from app.config import settings
from app.llm import extract_with_fallback

PROMPT_VERSION = "fingerprint-v2"  # v2: coverage note updated for Phase 7 (Ontario)

SYSTEM_PROMPT = """\
You turn a client's legal scenario into a structured fingerprint that drives search over a \
Canadian case-law and legislation database, for a legal researcher.

Grounding
- Work only from what the scenario states or clearly implies. Do not invent facts not in the text.
- Propose legal concepts, terms and candidate statutes to search for. Never assert that a \
particular case, statute section or outcome applies — this is a search aid, not legal advice or \
a prediction.

Jurisdiction
- The database currently covers federal case law and legislation, and Ontario case law and \
legislation. No other province's case law or legislation is covered yet. Set jurisdiction to \
what actually governs the scenario, even when that is a jurisdiction not yet covered.
- If the province or level of court/government isn't stated and it would change what's relevant \
(most private-law areas — tenancy, most torts and contracts, family law, provincial regulatory \
schemes — are provincial), add one short, specific item to needs_clarification asking for it. \
Leave needs_clarification empty when jurisdiction plainly doesn't matter to finding the right \
authorities (e.g. immigration, tax, federal criminal procedure, employment insurance, patents).

Output
- search_terms: short keyword phrases suited to a keyword search engine (legal tests, terms of \
art, statute names) — not full sentences.
- candidate_statutes: named Acts or regulations that plausibly govern, only if the facts \
actually suggest one.
- Keep every list to what's genuinely supported by the scenario; short lists are fine."""

INSTRUCTION = "Produce the fingerprint for the scenario provided."


def _list_of_strings() -> dict:
    return {"type": "array", "items": {"type": "string"}}


FINGERPRINT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "jurisdiction", "areas_of_law", "issues", "key_facts",
        "causes_of_action", "candidate_statutes", "search_terms", "needs_clarification",
    ],
    "properties": {
        "jurisdiction": {
            "type": "string",
            "enum": ["federal", "ontario", "other_province", "unknown"],
            "description": "Best-supported jurisdiction; 'unknown' only if genuinely indeterminate.",
        },
        "areas_of_law": _list_of_strings(),
        "issues": _list_of_strings(),
        "key_facts": _list_of_strings(),
        "causes_of_action": _list_of_strings(),
        "candidate_statutes": _list_of_strings(),
        "search_terms": _list_of_strings(),
        "needs_clarification": _list_of_strings(),
    },
}

_FIELDS = (
    "jurisdiction", "areas_of_law", "issues", "key_facts",
    "causes_of_action", "candidate_statutes", "search_terms", "needs_clarification",
)


@dataclass
class Fingerprint:
    jurisdiction: str
    areas_of_law: list[str]
    issues: list[str]
    key_facts: list[str]
    causes_of_action: list[str]
    candidate_statutes: list[str]
    search_terms: list[str]
    needs_clarification: list[str]
    model: str
    backend: str
    usage: dict

    @classmethod
    def from_result(cls, data: dict, *, model: str, backend: str, usage: dict) -> "Fingerprint":
        return cls(**{k: data[k] for k in _FIELDS}, model=model, backend=backend, usage=usage)


def generate(text: str) -> Fingerprint:
    result = extract_with_fallback(
        system=SYSTEM_PROMPT, instruction=INSTRUCTION, document=text, schema=FINGERPRINT_SCHEMA,
        backend=settings.fingerprint_backend, model=settings.fingerprint_model, effort=settings.fingerprint_effort,
        fallback_backend=settings.fingerprint_fallback_backend,
        fallback_model=settings.fingerprint_fallback_model,
        fallback_effort=settings.fingerprint_fallback_effort,
    )
    return Fingerprint.from_result(result.data, model=result.model, backend=result.backend, usage=result.usage)


def search_query(fp: Fingerprint) -> str:
    """One query string for both retrievers: precise terms first (best for lexical/BM25), then
    issues and facts (carry the semantic signal for the embedding side)."""
    parts = [*fp.search_terms, *fp.candidate_statutes, *fp.issues, *fp.key_facts]
    query = "; ".join(p for p in parts if p)
    return query or "; ".join(fp.areas_of_law)


# Federal and Ontario (ONCA cases + Ontario statutes, Phase 7) are covered; other provinces
# aren't yet. Ontario Superior Court/tribunal cases still aren't (Phase 8, gated on CanLII) —
# the search itself is still useful for Ontario, it just won't surface those specifically.
NOT_YET_COVERED = {"other_province"}


@dataclass
class Gate:
    status: str  # "ok" | "needs_clarification" | "unsupported_jurisdiction"
    message: str | None = None


def check_jurisdiction(fp: Fingerprint) -> Gate:
    """Ask before retrieving when the answer would be unreliable: an unsupported jurisdiction, or
    one that's unstated and matters (flagged by the model itself via needs_clarification)."""
    if fp.jurisdiction in NOT_YET_COVERED:
        return Gate(
            "unsupported_jurisdiction",
            f"This looks like a {fp.jurisdiction.replace('_', ' ')} matter. LawSearch currently "
            "covers federal case law and federal legislation only — provincial coverage is "
            "planned but not yet available, so results would not be reliable here.",
        )
    if fp.needs_clarification:
        return Gate("needs_clarification", "; ".join(fp.needs_clarification))
    return Gate("ok")
