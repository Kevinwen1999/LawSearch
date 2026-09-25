"""Load the Ontario regulations the corpus cites into legislation + legislation_sections.

    python -m scripts.ingest_ontario_regulations --dry-run     # count citations, fetch nothing
    python -m scripts.ingest_ontario_regulations               # load (default: cited by >= 2 decisions)

A2AJ's `canadian-laws` has Ontario Acts only, so regulations like O. Reg. 288/01 (ESA termination
and severance; "wilful misconduct") were a gap. Rather than mirror all of e-Laws, this loads the
regulations Ontario decisions in the corpus actually cite: it scans every decision for
"O. Reg. N/YY" / "R.R.O. 1990, Reg. N" citations, keeps those cited by at least --min-citing
decisions, and fetches each current consolidation from e-Laws' JSON API
(ontario.ca/laws/api/v2/legislation/en/doc-search/regulation/<alias>), one request every
--delay seconds. Unofficial text, King's Printer for Ontario, same terms as the Acts.

Stored with code = citation ("O. Reg. 288/01"), kind 'regulation', jurisdiction 'ontario'.
Re-runnable: replaces only Ontario regulation rows. Then run scripts/link_statutes.py, which
indexes Ontario regulations by citation (their titles are often just "GENERAL").
"""

import argparse
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import httpx
from lxml import html as lxml_html
from pgvector import HalfVector

from app.chunking import MAX_CHARS, windows
from app.db import connect
from app.ontario_regs import RegulationRef, regulation_refs

API = "https://www.ontario.ca/laws/api/v2/legislation/en/doc-search/regulation/{alias}"
SOURCE = "elaws"
FLUSH_CHUNKS = 2048


@dataclass
class RegSection:
    section_no: str
    marginal_note: str | None
    heading: str | None
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class Regulation:
    ref: RegulationRef
    title: str
    act_name: str | None
    version_date: date | None
    sections: list[RegSection]


def cited_counts(min_citing: int) -> list[tuple[RegulationRef, int]]:
    counts: Counter[RegulationRef] = Counter()
    with connect() as conn, conn.cursor(name="scan_regulations") as cur:
        cur.itersize = 500
        cur.execute("SELECT full_text FROM cases WHERE full_text ~ 'Reg\\.'")
        for (text,) in cur:
            counts.update(regulation_refs(text or ""))
    return [(ref, n) for ref, n in counts.most_common() if n >= min_citing]


_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "under", "with"}


def _text(el) -> str:
    text = re.sub(r"\s+", " ", el.text_content()).strip()
    # Separators left behind by the dropped amendment-history spans: "... the Act. ; ;"
    return re.sub(r"(?:\s*;)+\s*$", "", text)


def _title_case(title: str) -> str:
    """'TERMINATION AND SEVERANCE OF EMPLOYMENT' -> 'Termination and Severance of Employment'."""
    words = title.lower().split()
    return " ".join(w if i and w in _SMALL_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words))


def parse_sections(content: str) -> list[RegSection]:
    """e-Laws regulation HTML: <p class="section"><b>2.</b> (1) ...</p> opens a section, the
    headnote-e paragraph before it is its marginal note, heading1-e paragraphs are Part headings,
    and every other paragraph belongs to the open section. Amendment-history spans are dropped."""
    root = lxml_html.fragment_fromstring(content, create_parent="div")
    for span in root.xpath('.//span[@class="citation"]'):
        span.drop_tree()
    sections: list[RegSection] = []
    note = heading = None
    for p in root.iter("p"):
        cls = p.get("class") or ""
        text = _text(p)
        if not text:
            continue
        if cls.startswith("heading1"):
            heading = text
        elif cls.startswith("headnote"):
            note = text
        elif cls.startswith("section"):
            bold = p.find(".//b")
            number = (bold.text_content().strip().rstrip(".") if bold is not None else "")
            if not re.fullmatch(r"\d+(?:\.\d+)*", number):
                if sections:
                    sections[-1].paragraphs.append(text)
                continue
            sections.append(RegSection(number, note, heading, [text]))
            note = None
        elif sections:
            sections[-1].paragraphs.append(text)
    return sections


def fetch(client: httpx.Client, ref: RegulationRef) -> Regulation | None:
    r = client.get(API.format(alias=ref.alias))
    if r.status_code != 200:
        return None
    body = r.json()
    if not body.get("content"):
        return None
    title = _title_case((body.get("title") or body.get("description") or "").strip()) or ref.citation
    version = body.get("dateFrom")
    return Regulation(
        ref, title, (body.get("actName") or {}).get("en"),
        date.fromisoformat(version[:10]) if version else None,
        parse_sections(body["content"]),
    )


def chunks(reg: Regulation) -> list[tuple[str, str, str | None, str | None, int, str]]:
    """(section_no, label, marginal_note, hierarchy_path, chunk_no, text) per chunk."""
    out = []
    for s in reg.sections:
        body = f"{reg.ref.citation} ({reg.title}), s. {s.section_no}\n" + "\n".join(s.paragraphs)
        parts = [body] if len(body) <= MAX_CHARS else windows(body)
        out.extend((s.section_no, s.section_no, s.marginal_note, s.heading, i, part) for i, part in enumerate(parts))
    return out


def write(conn, rows: list[tuple], embed) -> None:
    vectors = embed([chunk[-1] for _, _, chunk in rows])
    with conn.transaction(), conn.cursor() as cur:
        with cur.copy(
            "COPY legislation_sections (legislation_id, section_no, section_label, marginal_note, "
            "hierarchy_path, chunk_no, text, url_official, embedding) FROM STDIN WITH (FORMAT BINARY)"
        ) as copy:
            copy.set_types(["uuid", "text", "text", "text", "text", "int4", "text", "text", "halfvec"])
            for (legislation_id, url, (section_no, label, note, heading, chunk_no, text)), vector in zip(rows, vectors):
                copy.write_row((legislation_id, section_no, label, note, heading, chunk_no, text, url, HalfVector(vector)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # 2 keeps the long tail (132 regulations as of 2026-09-24, incl. O. Reg. 288/01 at 10) while
    # skipping one-off mentions (437 cited at all).
    parser.add_argument("--min-citing", type=int, default=2, help="load regulations cited by at least this many decisions")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between e-Laws requests")
    parser.add_argument("--dry-run", action="store_true", help="count citations only")
    args = parser.parse_args()

    started = time.monotonic()
    selected = cited_counts(args.min_citing)
    print(f"{len(selected)} Ontario regulations cited by >= {args.min_citing} decisions "
          f"({time.monotonic() - started:.0f}s); top: "
          + ", ".join(f"{ref.citation} ({n})" for ref, n in selected[:10]), flush=True)
    if args.dry_run:
        return

    regs: list[Regulation] = []
    missing: list[str] = []
    with httpx.Client(timeout=60, headers={"User-Agent": "LawSearch research tool"}) as client:
        for n, (ref, _) in enumerate(selected, 1):
            reg = fetch(client, ref)
            if reg and reg.sections:
                regs.append(reg)
            else:
                missing.append(ref.citation)
            if n % 25 == 0:
                print(f"  fetched {n}/{len(selected)}", flush=True)
            time.sleep(args.delay)
    print(f"fetched {len(regs)} regulations with sections; not available on e-Laws "
          f"(revoked, renumbered or empty): {len(missing)}", flush=True)

    from app.embeddings import embed

    with connect(autocommit=True) as conn:
        conn.execute("DELETE FROM legislation WHERE jurisdiction = 'ontario' AND kind = 'regulation'")
        batch: list[tuple] = []
        for reg in regs:
            legislation_id = conn.execute(
                "INSERT INTO legislation (code, kind, citation, title, jurisdiction, consolidation_date, "
                "language, source, url_official) VALUES (%s, 'regulation', %s, %s, 'ontario', %s, 'en', %s, %s) "
                "RETURNING id",
                (reg.ref.citation, reg.ref.citation, reg.title, reg.version_date, SOURCE, reg.ref.url),
            ).fetchone()[0]
            batch.extend((legislation_id, reg.ref.url, c) for c in chunks(reg))
            if len(batch) >= FLUSH_CHUNKS:
                write(conn, batch, embed)
                batch = []
        if batch:
            write(conn, batch, embed)
        conn.execute("ANALYZE legislation")
        conn.execute("ANALYZE legislation_sections")
        total = conn.execute(
            "SELECT count(*) FROM legislation_sections s JOIN legislation l ON l.id = s.legislation_id "
            "WHERE l.jurisdiction = 'ontario' AND l.kind = 'regulation'"
        ).fetchone()[0]
    print(f"loaded {len(regs)} regulations, {total:,} section chunks in {(time.monotonic() - started) / 60:.1f} min "
          "— now run `python -m scripts.link_statutes`")


if __name__ == "__main__":
    main()
