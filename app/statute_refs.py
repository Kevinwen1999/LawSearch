"""Find references to federal statute sections in free text (decisions, FILAC authorities).

Recognizes a law by its title, a common acronym, an alias the document defines for itself
('("IRPA" or the "Act")'), or 'the Act' / 'the Regulations' / 'the Code' / 'the Charter'
resolved to the most recently named law of that kind. A section number counts only when it
is tied to a law in the same phrase — 'section 96 of IRPA', 'Income Tax Act, s. 18(1)(a)' —
so a bare 's. 96' is not guessed at.

Runs over every decision in the corpus, so matching is linear: the text is normalized and
tokenized once, titles are looked up as token tuples, and look-backs use bounded windows.
"""

import bisect
import re
from dataclasses import dataclass

# Acronyms and informal names used across federal decisions, mapped to official titles.
ALIASES = {
    "IRPA": "Immigration and Refugee Protection Act",
    "IRPR": "Immigration and Refugee Protection Regulations",
    "ITA": "Income Tax Act",
    "EIA": "Employment Insurance Act",
    "EI Act": "Employment Insurance Act",
    "CPP": "Canada Pension Plan",
    "CHRA": "Canadian Human Rights Act",
    "ETA": "Excise Tax Act",
    "PSEA": "Public Service Employment Act",
    "FPSLRA": "Federal Public Sector Labour Relations Act",
    "PSLRA": "Public Service Labour Relations Act",
    "DESDA": "Department of Employment and Social Development Act",
    "CDSA": "Controlled Drugs and Substances Act",
    "YCJA": "Youth Criminal Justice Act",
    "CEA": "Canada Evidence Act",
    "CLPA": "Crown Liability and Proceedings Act",
    "PIPEDA": "Personal Information Protection and Electronic Documents Act",
    "Charter": "Canadian Charter of Rights and Freedoms",
}
CHARTER_TITLE = "Canadian Charter of Rights and Freedoms"
CHARTER_CODE = "CONST-1982"
GENERIC_KIND = {"act": "act", "regulations": "regulation", "code": "act", "charter": "constitution"}
GENERIC_LOOKBACK = 3000
BEFORE_WINDOW = 160

# One-to-one character replacements, so offsets in the normalized text match the original.
_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2011": "-", "\u00a0": " "})
_TOKEN = re.compile(r"[\w'\-]+|,")
_KEYWORD = r"(?:ss?|secs?|sections?|subsections?|subss?|paras?|paragraphs?|subparagraphs?|clauses?|arts?|articles?)"
_NUMBER = r"\d+(?:\.\d+)?(?:\s?\((?:\d+(?:\.\d+)?|[a-z]{1,4}(?:\.\d+)?)\))*"
_NUMBER_LIST = rf"{_NUMBER}(?:\s*(?:,|and|or|to|&)\s*(?:{_KEYWORD}\.?\s*)?{_NUMBER})*"
_REF_LIST = re.compile(rf"\b{_KEYWORD}\.?\s*({_NUMBER_LIST})", re.IGNORECASE)
_SINGLE = re.compile(_NUMBER)
# A statute citation between the name and the section: ', SC 2001, c 27', ', RSC 1985, c 1 (5th Supp)'.
_CITATION_TAIL = re.compile(
    r",?\s*(?:(?:R\.?\s?S\.?\s?C\.?|S\.?\s?C\.?)\s*\d{4}(?:-\d{2})?,?\s*c\.?\s*[\w.\-]+(?:\s*\([^)]{1,20}\))?"
    r"|SOR/\d{2,4}-\d+|C\.?\s?R\.?\s?C\.?,?\s*c\.?\s*\d+)\s*$"
)
# '(“IRPA” or the “Act”)' right after a title, optionally past its citation. Nothing else may sit
# between, or '... Privacy Act and the Freedom of Information ... Act (FIPPA)' hands FIPPA to the
# wrong law.
_DEFINED_ALIAS = re.compile(
    r'(?:,?\s*(?:(?:R\.?\s?S\.?\s?C\.?|S\.?\s?C\.?)\s*\d{4}(?:-\d{2})?,?\s*c\.?\s*[\w.\-]+(?:\s*\([^)]{1,20}\))?'
    r'|SOR/\d{2,4}-\d+))?'
    r'\s*[(\[]\s*(?:the\s+)?"?([A-Z][A-Za-z.]{1,11}|Act|Regulations|Code|Charter)"?'
    r'(?:\s*(?:or|,)\s*(?:the\s+)?"?(Act|Regulations|Code|Charter)"?)?\s*[)\]]'
)
_GENERIC_AFTER = re.compile(r"(Act|Regulations|Code|Charter)\b")
_GENERIC_BEFORE = re.compile(r"\b(?:the\s+)?(Act|Regulations|Code|Charter)$")
_WORD_AFTER = re.compile(r"[A-Za-z][A-Za-z.]{1,11}")
_WORD_BEFORE = re.compile(r"\b([A-Za-z][A-Za-z.]{1,11})$")
_OF = re.compile(r"\s*(?:of|under|in)\s+(?:the\s+)?", re.IGNORECASE)


@dataclass(frozen=True)
class StatuteRef:
    code: str
    section_no: str
    pinpoint: str      # '(1)(b)' or ''
    raw: str
    start: int
    explicit: bool     # law named in the phrase itself, not via 'the Act'


@dataclass(frozen=True)
class _Mention:
    start: int
    end: int
    code: str
    kind: str


_LEAD_INS = {"the", "this", "that", "under", "in", "see", "and", "or", "of", "by", "pursuant", "to",
             "section", "subsection", "paragraph", "part", "s", "ss", "federal", "canada's"}
_CONNECTORS = {"of", "and", "on", "for", "to", "respecting"}


def _ends_longer_name(text: str, tokens: list[tuple[str, int, int]], first: int) -> bool:
    """True when a matched title is the tail of a longer proper name — 'Ontario Human Rights Code',
    '... Protection of Privacy Act' — so a provincial law is not mistaken for a federal one."""

    def capitalized_neighbour(j: int, k: int) -> bool:
        # token j directly precedes token k (only spaces between) and starts with a capital
        if j < 0 or text[tokens[j][2]:tokens[k][1]].strip():
            return False
        original = text[tokens[j][1]:tokens[j][2]]
        return original[:1].isupper() and tokens[j][0] not in _LEAD_INS

    prev = first - 1
    if prev < 0 or text[tokens[prev][2]:tokens[first][1]].strip():
        return False
    if tokens[prev][0] in _CONNECTORS:
        return capitalized_neighbour(prev - 1, prev)
    return capitalized_neighbour(prev, first)


def _title_key(title: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(title.translate(_QUOTES).lower()))


class StatuteIndex:
    def __init__(self, laws: dict[str, tuple[str, str]]):
        """laws: title -> (code, kind)."""
        self.titles: dict[tuple[str, ...], tuple[str, str]] = {}
        for title, target in laws.items():
            self._add(title, target)
        if CHARTER_CODE in {code for code, _ in laws.values()}:
            self._add(CHARTER_TITLE, (CHARTER_CODE, "constitution"))
        for alias, title in ALIASES.items():
            target = self.titles.get(_title_key(title))
            if target:
                self._add(alias, target)
        lengths: dict[str, set[int]] = {}
        for key in self.titles:
            lengths.setdefault(key[-1], set()).add(len(key))
        self.lengths_by_last = {last: sorted(sizes, reverse=True) for last, sizes in lengths.items()}

    def _add(self, title: str, target: tuple[str, str]) -> None:
        key = _title_key(title)
        if key:
            self.titles.setdefault(key, target)

    @classmethod
    def from_db(cls, conn) -> "StatuteIndex":
        rows = conn.execute("SELECT title, code, kind FROM legislation").fetchall()
        return cls({title: (code, kind) for title, code, kind in rows})

    def extract(self, text: str) -> list[StatuteRef]:
        text = text.translate(_QUOTES)
        mentions = self._mentions(text)
        by_start = {m.start: m for m in mentions}
        by_end = {m.end: m for m in mentions}
        ends = [m.end for m in mentions]

        defined: dict[str, _Mention] = {}
        for mention in mentions:
            match = _DEFINED_ALIAS.match(text, mention.end, mention.end + 160)
            if match:
                for alias in filter(None, match.groups()):
                    defined[alias.lower()] = mention

        refs: list[StatuteRef] = []
        for m in _REF_LIST.finditer(text):
            law = (self._law_after(text, m.end(), by_start, mentions, ends, defined)
                   or self._law_before(text, m.start(), by_end, mentions, ends, defined))
            if law is None:
                continue
            code, explicit = law
            for number in _SINGLE.findall(m.group(1)):
                number = number.replace(" ", "")
                section_no = re.match(r"\d+(?:\.\d+)?", number).group(0)
                refs.append(StatuteRef(code, section_no, number[len(section_no):], m.group(0).strip(), m.start(), explicit))
        return refs

    def _mentions(self, text: str) -> list[_Mention]:
        """Laws named in the text, longest title first, without overlaps. Ordered by position."""
        tokens = [(m.group(0).lower(), m.start(), m.end()) for m in _TOKEN.finditer(text)]
        words = [t[0] for t in tokens]
        found: list[_Mention] = []
        taken_until = -1
        for i, word in enumerate(words):
            sizes = self.lengths_by_last.get(word)
            if not sizes:
                continue
            for size in sizes:
                first = i - size + 1
                if first <= taken_until or first < 0:
                    continue
                hit = self.titles.get(tuple(words[first:i + 1]))
                if hit:
                    if not _ends_longer_name(text, tokens, first):
                        found.append(_Mention(tokens[first][1], tokens[i][2], *hit))
                    taken_until = i
                    break
        return found

    def _law_after(self, text, pos, by_start, mentions, ends, defined):
        """'section 96 of the Immigration and Refugee Protection Act' / 'of IRPA' / 'of the Act'."""
        match = _OF.match(text, pos, pos + 16)
        if not match:
            return None
        at = match.end()
        if at in by_start:
            return by_start[at].code, True
        generic = _GENERIC_AFTER.match(text, at, at + 12)
        if generic:
            return self._resolve_generic(generic.group(1), at, mentions, ends, defined)
        word = _WORD_AFTER.match(text, at, at + 12)
        if word and word.group(0).lower() in defined:
            return defined[word.group(0).lower()].code, True
        return None

    def _law_before(self, text, pos, by_end, mentions, ends, defined):
        """'Income Tax Act, s. 18(1)(a)' / 'IRPA s 96' / 'the Charter, s. 8'."""
        offset = max(0, pos - BEFORE_WINDOW)
        # Only commas, spaces and a statute citation may separate the law from the section keyword.
        window = text[offset:pos].rstrip(", ").rstrip()
        window = _CITATION_TAIL.sub("", window).rstrip(", ").rstrip()
        mention = by_end.get(offset + len(window))
        if mention:
            return mention.code, True
        generic = _GENERIC_BEFORE.search(window)
        if generic:
            return self._resolve_generic(generic.group(1), offset + generic.start(1), mentions, ends, defined)
        word = _WORD_BEFORE.search(window)
        if word and word.group(1).lower() in defined:
            return defined[word.group(1).lower()].code, True
        return None

    @staticmethod
    def _resolve_generic(word: str, at: int, mentions, ends, defined):
        if word.lower() in defined:
            return defined[word.lower()].code, False
        wanted = GENERIC_KIND[word.lower()]
        for i in range(bisect.bisect_right(ends, at) - 1, -1, -1):
            mention = mentions[i]
            if at - mention.end > GENERIC_LOOKBACK:
                break
            if mention.kind == wanted or (word == "Charter" and mention.kind == "constitution"):
                return mention.code, False
        return None


def pick_chunk(rows: list[tuple], ref: StatuteRef):
    """Choose the stored chunk for a reference.

    rows: (chunk_id, section_label, chunk_no, ...) for one section, ordered by chunk_no. The chunk
    whose subsection range covers the pinpoint wins ('97(1)-(2)' covers '(1)(b)'); else the first.
    """
    if not rows:
        return None
    sub = re.match(r"\((\d+(?:\.\d+)?)\)", ref.pinpoint)
    if sub:
        wanted = float(sub.group(1))
        for row in rows:
            bounds = [float(x) for x in re.findall(r"\((\d+(?:\.\d+)?)\)", row[1][len(ref.section_no):])]
            if bounds and bounds[0] <= wanted <= bounds[-1]:
                return row
    return rows[0]


_index: StatuteIndex | None = None


def statute_index(conn) -> StatuteIndex:
    """Process-wide index of loaded law titles (rebuilt only on restart)."""
    global _index
    if _index is None:
        _index = StatuteIndex.from_db(conn)
    return _index
