"""Federal legislation parsing: Justice Laws XML (acts, regulations) and the Constitution Acts
HTML page, into section-grained chunks ready to embed."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import lxml.html
from lxml import etree

from app.chunking import MAX_CHARS, windows

LIMS = "{http://justice.gc.ca/lims}"
LAWS_BASE = "https://laws-lois.justice.gc.ca/eng"
CONSTITUTION_URL = f"{LAWS_BASE}/Const/FullText.html"

PROVISIONS = {
    "Subsection", "Paragraph", "Subparagraph", "Clause", "Subclause", "Subsubclause",
    "Definition", "Provision",
}
SKIP = {"Label", "MarginalNote", "HistoricalNote", "Footnote", "ReaderNote", "Note"}
# Section elements under these are quoted amendments or schedules, not the law's own sections.
NOT_OWN_SECTIONS = {"AmendedText", "Schedule", "Section", "BillPiece"}
_SPACES = re.compile(r"\s+")


@dataclass
class SectionChunk:
    section_no: str
    section_label: str
    chunk_no: int
    marginal_note: str | None
    hierarchy_path: str
    text: str              # includes a heading line naming the law and section, for search
    in_force_start: date | None
    url: str


@dataclass
class LegislationDoc:
    code: str
    kind: str
    title: str
    citation: str | None
    consolidation_date: date | None
    url: str
    chunks: list[SectionChunk] = field(default_factory=list)


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _provision_lines(elem, depth: int = 0) -> list[str]:
    """Render a provision and its nested provisions, one labelled line per provision."""
    label = elem.findtext("Label")
    lines: list[str] = []
    buffer: list[str] = [label] if label else []
    for child in elem:
        if not isinstance(child.tag, str) or child.tag in SKIP:
            continue
        if child.tag in PROVISIONS or child.tag.startswith("Continued"):
            if buffer:
                lines.append(_clean(" ".join(buffer)))
                buffer = []
            note = child.findtext("MarginalNote")
            if note and child.tag == "Subsection":
                lines.append(_clean(note))
            lines.extend(_provision_lines(child, depth + 1))
        else:
            buffer.append("".join(child.itertext()))
    if buffer and _clean(" ".join(buffer)) != (label or ""):
        lines.append(_clean(" ".join(buffer)))
    return [line for line in lines if line]


def _pack(units: list[tuple[str | None, str | None, str]], section_no: str) -> list[tuple[str, str | None, str]]:
    """Group (subsection label, marginal note, text) units into chunks <= MAX_CHARS.

    Returns (section_label, marginal note, text). A marginal note covers the provisions after it until
    the next note, so each chunk takes the note in force at its first unit.
    """
    chunks: list[tuple[str, str | None, str]] = []
    labels: list[str] = []
    parts: list[str] = []
    note: str | None = None
    chunk_note: str | None = None

    def flush() -> None:
        if not parts:
            return
        subs = [l for l in labels if l]
        if not subs:
            label = section_no
        elif len(subs) == 1:
            label = f"{section_no}{subs[0]}"
        else:
            label = f"{section_no}{subs[0]}-{subs[-1]}"
        chunks.append((label, chunk_note, "\n".join(parts)))
        labels.clear()
        parts.clear()

    for sub_label, unit_note, text in units:
        note = unit_note or note
        if len(text) > MAX_CHARS:
            flush()
            label = f"{section_no}{sub_label}" if sub_label else section_no
            chunks.extend((label, note, w) for w in windows(text))
            continue
        if parts and sum(len(p) for p in parts) + len(text) > MAX_CHARS:
            flush()
        if not parts:
            chunk_note = note
        labels.append(sub_label)
        parts.append(text)
    flush()
    return chunks


def _section_units(section) -> list[tuple[str | None, str | None, str]]:
    """Split a section into (label, marginal note, text) units at subsection boundaries.

    The text before the first subsection is one unit. The marginal note before the section label
    belongs to the first unit: in Justice Laws XML it heads subsection (1), not the whole section.
    """
    units: list[tuple[str | None, str | None, str]] = []
    lead: list[str] = []
    for child in section:
        if not isinstance(child.tag, str) or child.tag in SKIP:
            continue
        if child.tag == "Subsection":
            note = _clean(child.findtext("MarginalNote") or "") or None
            lines = ([note] if note else []) + _provision_lines(child)
            units.append((child.findtext("Label"), note, "\n".join(lines)))
        elif child.tag in PROVISIONS or child.tag.startswith("Continued"):
            lead.extend(_provision_lines(child))
        else:
            lead.append(_clean("".join(child.itertext())))
    lead_text = "\n".join(line for line in lead if line)
    units = ([(None, None, lead_text)] if lead_text else []) + units
    if units and not units[0][1]:
        units[0] = (units[0][0], _clean(section.findtext("MarginalNote") or "") or None, units[0][2])
    return units


def _is_repealed(section) -> bool:
    text = section.find("Text")
    return text is not None and text.find("Repealed") is not None and len(section) <= 3


def parse_xml(path: Path) -> LegislationDoc | None:
    root = etree.parse(str(path)).getroot()
    ident = root.find("Identification")
    body = root.find("Body")
    if ident is None or body is None:
        return None

    title = _clean(ident.findtext("ShortTitle") or ident.findtext("LongTitle") or "")
    if root.tag == "Statute":
        kind = "act"
        code = _clean(ident.findtext("Chapter/ConsolidatedNumber") or path.stem)
        annual = ident.find("Chapter/AnnualStatuteId")
        if annual is not None and annual.get("revised-statute") == "no":
            citation = f"SC {annual.findtext('YYYY')}, c {annual.findtext('AnnualStatuteNumber')}"
        else:
            citation = f"RSC 1985, c {code}"
        url = f"{LAWS_BASE}/acts/{code}/"
    elif root.tag == "Regulation":
        kind = "regulation"
        code = _clean(ident.findtext("InstrumentNumber") or path.stem)
        citation = code
        url = f"{LAWS_BASE}/regulations/{path.stem}/"
    else:
        return None
    if not title:
        return None

    doc = LegislationDoc(code, kind, title, citation, _date(root.get(f"{LIMS}current-date")), url)
    headings: dict[int, str] = {}
    for elem in body.iter("Heading", "Section"):
        if elem.tag == "Heading":
            level = int(elem.get("level") or 1)
            headings = {k: v for k, v in headings.items() if k < level}
            text = _clean("".join(elem.findtext("TitleText") or ""))
            if text:
                headings[level] = text
            continue
        if any(a.tag in NOT_OWN_SECTIONS for a in elem.iterancestors()) or _is_repealed(elem):
            continue
        section_no = _clean(elem.findtext("Label") or "")
        if not section_no:
            continue
        path_text = " > ".join(headings[k] for k in sorted(headings))
        in_force = _date(elem.get(f"{LIMS}inforce-start-date"))
        section_url = f"{url}section-{section_no}.html"
        for chunk_no, (label, note, text) in enumerate(_pack(_section_units(elem), section_no)):
            header = f"{title}, s. {label}" + (f" — {note}" if note else "")
            doc.chunks.append(SectionChunk(section_no, label, chunk_no, note, path_text,
                                           f"{header}\n{text}", in_force, section_url))
    return doc


def parse_constitution_html(html: bytes, fetched: date) -> list[LegislationDoc]:
    """The Constitution Acts page, which Justice Laws does not publish as XML."""
    root = lxml.html.fromstring(html)
    for hidden in root.xpath("//*[contains(@class, 'wb-invisible')] | //sup"):
        hidden.drop_tree()
    container = root.get_element_by_id("docCont")

    docs: list[LegislationDoc] = []
    doc: LegislationDoc | None = None
    heading, pending_note = "", None
    # (section_no, marginal note, heading, text parts) of the section being collected.
    current: tuple[str, str | None, str, list[str]] | None = None

    def flush() -> None:
        nonlocal current
        if doc is not None and current is not None:
            section_no, note, section_heading, parts = current
            text = _clean(" ".join(parts))
            if text and "Repealed" not in text[:40]:
                header = f"{doc.title}, s. {section_no}" + (f" — {note}" if note else "")
                for chunk_no, chunk in enumerate(windows(text)):
                    doc.chunks.append(SectionChunk(section_no, section_no, chunk_no, note, section_heading,
                                                   f"{header}\n{chunk}", None, CONSTITUTION_URL))
        current = None

    for elem in container.iter("h2", "h3", "p", "ul"):
        classes = set((elem.get("class") or "").split())
        # Content nested in a section or provision list is read with its container.
        if any(set((a.get("class") or "").split()) & {"Section", "ProvisionList"} for a in elem.iterancestors()):
            continue
        text = _clean(" ".join(elem.itertext()))
        # Act titles are h2s with no class (1982) or class Title-of-Act (1867); other unclassed
        # h2s (the Canada Act 1982) and schedule labels end the current act.
        if elem.tag == "h2" and (not classes or classes & {"Title-of-Act", "scheduleLabel"}):
            flush()
            match = re.match(r"CONSTITUTION ACT, (\d{4})$", text.upper()) if "scheduleLabel" not in classes else None
            doc = None
            if match:
                year = match.group(1)
                doc = LegislationDoc(f"CONST-{year}", "constitution", f"Constitution Act, {year}",
                                     f"Constitution Act, {year}", fetched, CONSTITUTION_URL)
                docs.append(doc)
            heading, pending_note = "", None
        elif doc is None:
            continue
        elif classes & {"Part", "Subheading"}:
            flush()
            heading = text
        elif "MarginalNote" in classes:
            flush()
            pending_note = text
        elif "Section" in classes:
            flush()
            label_el = elem.find(".//span[@class='sectionLabel']")
            section_no = _clean(label_el.text_content()) if label_el is not None else ""
            if section_no:
                current = (section_no, pending_note, heading, [text])
            pending_note = None
        elif "ProvisionList" in classes and current is not None:
            # Paragraphs such as Charter s. 11(a)-(i) follow their section as a sibling list.
            current[3].append(text)
    flush()
    return [d for d in docs if d.chunks]


def iter_xml_docs(xml_root: Path) -> Iterator[LegislationDoc]:
    for folder in ("acts", "regulations"):
        for path in sorted((xml_root / folder).glob("*.xml")):
            doc = parse_xml(path)
            if doc and doc.chunks:
                yield doc
