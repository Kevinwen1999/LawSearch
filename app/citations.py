"""Canadian case citation parsing, normalized to the corpus's own citation format."""

import re

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
