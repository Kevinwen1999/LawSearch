"""Canadian case citation parsing, normalized to the corpus's own citation format."""

import re
from collections import defaultdict
from uuid import UUID

_NEUTRAL = re.compile(r"\b(\d{4})\s+([A-Z][A-Za-z]{1,9})\s+(\d{1,5})\b")
_SCR = re.compile(r"\[(\d{4})\]\s*(\d)\s*S\.?\s*C\.?\s*R\.?\s*(\d{1,4})")

# French neutral-citation court codes for courts whose decisions are stored under English codes.
FRENCH_COURT_CODES = {
    "CSC": "SCC", "CF": "FC", "CAF": "FCA", "CFPI": "FCT", "CCI": "TCC", "CACM": "CMAC",
    "TCDP": "CHRT", "CCRI": "CIRB", "TCCE": "CITT", "CRTFP": "FPSLREB", "TSS": "SST",
}


def neutral_citations(text: str) -> list[str]:
    return [f"{year} {court} {number}" for year, court, number in _NEUTRAL.findall(text)]


def scr_citations(text: str) -> list[str]:
    """Supreme Court Reports citations, e.g. '[1999] 2 S.C.R. 817' -> '[1999] 2 SCR 817'."""
    return [f"[{year}] {volume} SCR {page}" for year, volume, page in _SCR.findall(text)]


def case_citations(text: str) -> list[str]:
    return neutral_citations(text) + scr_citations(text)


def canonical(citation: str) -> str:
    """Map French neutral citations to English: '2019 CSC 65' -> '2019 SCC 65'."""
    parts = citation.split()
    if len(parts) == 3 and parts[1] in FRENCH_COURT_CODES:
        return f"{parts[0]} {FRENCH_COURT_CODES[parts[1]]} {parts[2]}"
    return citation


# Pre-2007 Ontario Court of Appeal decisions have no neutral citation (A2AJ stores them under
# their docket number), so later decisions cite them by name, year and report:
#   Hobbs v. TDI Canada Ltd. (2004), 246 D.L.R. (4th) 43 (Ont. C.A.)
#   Hobbs v. TDI Canada Ltd., [2004] 192 O.A.C. 141
#   Hobbs v. TDI Canada Ltd., 2004 CanLII 44783 (ON CA)
# Matched on (last word before "v.", first word after it, year), and only when an Ontario Court
# of Appeal marker follows within the same citation, so "R. v. Smith (2004)" from another court
# doesn't match.
_ONCA_NAMED = re.compile(
    r"([A-Za-zÀ-ÿ0-9'’&.\-)]+)\s+v\.?\s+((?:the\s+)?[A-Za-zÀ-ÿ0-9'’&.\-(]+)"  # left and right party words
    r"((?:(?!\sv\.?\s|\n\s*\n)[^;]){0,160}?)"                                # the rest, not crossing another "v." or a blank line
    r"(\(\s*(?:Ont\.?\s*)?C\.\s*A\.\s*\)|O\.A\.C\.|\(\s*ON\s*CA\s*\))",      # the Court of Appeal marker, PDF line breaks allowed
    re.IGNORECASE,
)
_CITED_YEAR = re.compile(r"\((\d{4})\)|\[(\d{4})\]|\b(\d{4})\s+CanLII\b")


def _party_word(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def onca_party_key(style_of_cause: str, year: int) -> tuple[str, str, int] | None:
    """Key for a decision's style of cause: ('hobbs', 'tdi', 2004). None if it has no 'v.'."""
    m = re.search(r"(\S+)\s+v\.?\s+(?:the\s+)?(\S+)", style_of_cause, re.IGNORECASE)
    if not m:
        return None
    left, right = _party_word(m.group(1)), _party_word(m.group(2))
    return (left, right, year) if left and right else None


def onca_named_citations(text: str) -> list[tuple[str, str, int]]:
    """(left party word, right party word, year) for each name-and-year citation to the Ontario
    Court of Appeal in `text`. The year is the first one in the citation: the decision year in
    '(2004), 246 D.L.R.', the report year in '[2004] 192 O.A.C.' or the CanLII year."""
    keys = []
    for left, right, rest, _ in _ONCA_NAMED.findall(text):
        year = _CITED_YEAR.search(rest)
        if not year:
            continue
        right = re.sub(r"^the\s+", "", right, flags=re.IGNORECASE)
        key = (_party_word(left), _party_word(right), int(next(g for g in year.groups() if g)))
        if key[0] and key[1]:
            keys.append(key)
    return keys


def build_onca_name_resolver(conn) -> dict[tuple[str, str, int], UUID]:
    """(left party word, right party word, year) -> ONCA case without a neutral citation.
    Keys shared by two decisions (e.g. two "R. v. Smith" appeals in one year) are dropped."""
    owners: dict[tuple[str, str, int], set[UUID]] = defaultdict(set)
    for case_id, style, decided in conn.execute(
        r"SELECT id, style_of_cause, decision_date FROM cases "
        r"WHERE court = 'ONCA' AND citation !~ '^\d{4} ONCA \d+$' AND decision_date IS NOT NULL"
    ):
        if key := onca_party_key(style or "", decided.year):
            owners[key].add(case_id)
    return {key: next(iter(ids)) for key, ids in owners.items() if len(ids) == 1}


def resolve_onca_named(by_name: dict[tuple[str, str, int], UUID], key: tuple[str, str, int]) -> UUID | None:
    # A bracketed report year ("[2005] O.J.") can run a year behind a late-December decision.
    return by_name.get(key) or by_name.get((key[0], key[1], key[2] - 1))
