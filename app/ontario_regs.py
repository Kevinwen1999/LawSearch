"""Ontario regulation citations in decision text ("O. Reg. 288/01", "R.R.O. 1990, Reg. 194").

A2AJ's `canadian-laws` has Ontario Acts only, so a scenario can turn on a regulation LawSearch
doesn't hold (O. Reg. 288/01's "wilful misconduct" standard decides for-cause termination
clauses). Regulations the top cases cite are flagged with an e-Laws link rather than dropped.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

ELAWS_REGULATION_URL = "https://www.ontario.ca/laws/regulation/{alias}"

_O_REG = re.compile(r"\bO\.\s?Reg\.?\s?(\d{1,4})/(\d{2}|\d{4})\b")
_RRO = re.compile(r"\bR\.R\.O\.?\s?(1980|1990),?\s?Reg\.?\s?(\d{1,4})\b")


@dataclass(frozen=True)
class RegulationRef:
    citation: str  # "O. Reg. 288/01" or "R.R.O. 1990, Reg. 194" (also the legislation.code)
    alias: str     # e-Laws id: "010288", "900194"

    @property
    def url(self) -> str:
        return ELAWS_REGULATION_URL.format(alias=self.alias)


def regulation_refs(text: str) -> set[RegulationRef]:
    refs = set()
    for number, year in _O_REG.findall(text):
        yy = year[-2:]
        refs.add(RegulationRef(f"O. Reg. {int(number)}/{yy}", f"{yy}{int(number):04d}"))
    for year, number in _RRO.findall(text):
        refs.add(RegulationRef(f"R.R.O. {year}, Reg. {int(number)}", f"{year[-2:]}{int(number):04d}"))
    return refs


@dataclass
class CitedRegulation:
    ref: RegulationRef
    cited_by: list[str] = field(default_factory=list)  # citations of the citing top cases
    title: str | None = None
    covered: bool = False


def cited_regulations(texts: list[tuple[str, str]], min_citing: int = 2) -> list[CitedRegulation]:
    """Regulations cited by at least `min_citing` of the given (case citation, text) pairs,
    most-cited first."""
    by_ref: dict[RegulationRef, list[str]] = defaultdict(list)
    for citation, text in texts:
        for ref in regulation_refs(text or ""):
            by_ref[ref].append(citation)
    kept = [CitedRegulation(ref, cites) for ref, cites in by_ref.items() if len(cites) >= min_citing]
    return sorted(kept, key=lambda r: (-len(r.cited_by), r.ref.citation))
