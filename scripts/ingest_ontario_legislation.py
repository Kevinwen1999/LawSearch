"""Load Ontario legislation (Acts) into legislation + legislation_sections, section-grained and
embedded.

    python -m scripts.ingest_ontario_legislation --dry-run    # parse and count only
    python -m scripts.ingest_ontario_legislation              # load

Source: `a2aj/canadian-laws`' `LEGISLATION-ON` parquet on HuggingFace — see stack-and-setup.md §2
for how this was confirmed as the King's-Printer-for-Ontario-licensed replacement for the
originally-planned e-Laws scraper (that site has no confirmed bulk API). Each row is one Act with
a JSON dict of section number -> text; there are no regulations in this dataset as of 2026-09-14
and no per-section in-force dates (only one document_date per Act), so `in_force_start` is left
null — Phase 5's "wording changed after the decision" FILAC flag only applies to federal statutes.

Only touches jurisdiction='ontario' rows; scripts/ingest_legislation.py (federal) is untouched.
Re-runnable: deletes and reloads just the Ontario rows. Re-run scripts/link_statutes.py afterward
to pick up the new titles — case->statute linking (app/statute_refs.py) is already
jurisdiction-agnostic, so ONCA decisions citing these Acts resolve with no code changes.
"""

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date

import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download
from pgvector import HalfVector

from app.chunking import MAX_CHARS, windows
from app.db import connect

REPO = "a2aj/canadian-laws"
FILE = "LEGISLATION-ON/train.parquet"
SOURCE = "a2aj-canadian-laws"
FLUSH_CHUNKS = 4096
INDEX_BUILD_MEMORY = "8GB"
SECTION_INDEXES = {
    "legislation_sections_embedding_idx": (
        "CREATE INDEX IF NOT EXISTS legislation_sections_embedding_idx ON legislation_sections "
        "USING hnsw (embedding halfvec_cosine_ops)"
    ),
    "legislation_sections_bm25_idx": (
        "CREATE INDEX IF NOT EXISTS legislation_sections_bm25_idx ON legislation_sections "
        "USING bm25 (text) WITH (text_config = 'english')"
    ),
}


@dataclass
class SectionChunk:
    section_no: str
    section_label: str
    chunk_no: int
    text: str
    url: str


@dataclass
class LegislationDoc:
    code: str
    title: str
    citation: str
    consolidation_date: date | None
    url: str
    chunks: list[SectionChunk] = field(default_factory=list)


def _code(citation: str) -> str:
    """'SO 2000, c 3' -> 'SO2000c3'; the dataset's citations are already unique, and stripping
    punctuation is unlikely to collide two distinct ones."""
    return re.sub(r"[^A-Za-z0-9]", "", citation)


def load_docs() -> list[LegislationDoc]:
    path = hf_hub_download(REPO, FILE, repo_type="dataset")
    table = pq.read_table(path, columns=[
        "citation_en", "name_en", "document_date_en", "source_url_en", "unofficial_sections_en",
    ])
    docs = []
    for row in table.to_pylist():
        citation, title = row["citation_en"], row["name_en"]
        sections = json.loads(row["unofficial_sections_en"] or "{}")
        if not citation or not title or not sections:
            continue
        consolidation = row["document_date_en"].date() if row["document_date_en"] else None
        doc = LegislationDoc(_code(citation), title, citation, consolidation, row["source_url_en"])
        for section_no, text in sections.items():
            text = (text or "").strip()
            if not text:
                continue
            body = f"{title}, s. {section_no}\n{text}"
            parts = [body] if len(body) <= MAX_CHARS else windows(body)
            doc.chunks.extend(
                SectionChunk(section_no, section_no, chunk_no, part, doc.url)
                for chunk_no, part in enumerate(parts)
            )
        if doc.chunks:
            docs.append(doc)
    return docs


def write_batch(conn, batch: list[tuple], embed) -> None:
    vectors = embed([chunk.text for _, chunk in batch])
    with conn.transaction(), conn.cursor() as cur:
        with cur.copy(
            "COPY legislation_sections (legislation_id, section_no, section_label, chunk_no, "
            "text, url_official, embedding) FROM STDIN WITH (FORMAT BINARY)"
        ) as copy:
            copy.set_types(["uuid", "text", "text", "int4", "text", "text", "halfvec"])
            for (legislation_id, chunk), vector in zip(batch, vectors):
                copy.write_row((legislation_id, chunk.section_no, chunk.section_label, chunk.chunk_no,
                                chunk.text, chunk.url, HalfVector(vector)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="parse and report counts only")
    args = parser.parse_args()

    started = time.monotonic()
    docs = load_docs()
    chunks = sum(len(d.chunks) for d in docs)
    chars = sum(len(c.text) for d in docs for c in d.chunks)
    print(f"parsed {len(docs)} Ontario Acts: {chunks:,} chunks, {chars / 1e6:.1f}M chars "
          f"in {time.monotonic() - started:.0f}s", flush=True)
    if args.dry_run:
        return

    from app.embeddings import embed

    with connect(autocommit=True) as conn:
        # Acts only: Ontario regulations come from scripts/ingest_ontario_regulations.py.
        conn.execute("DELETE FROM legislation WHERE jurisdiction = 'ontario' AND kind = 'act'")
        for name in SECTION_INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {name}")

        batch: list[tuple] = []
        done = 0
        embed_started = time.monotonic()
        for doc in docs:
            legislation_id = conn.execute(
                "INSERT INTO legislation (code, kind, citation, title, jurisdiction, "
                "consolidation_date, language, source, url_official) "
                "VALUES (%s, 'act', %s, %s, 'ontario', %s, 'en', %s, %s) RETURNING id",
                (doc.code, doc.citation, doc.title, doc.consolidation_date, SOURCE, doc.url),
            ).fetchone()[0]
            batch.extend((legislation_id, chunk) for chunk in doc.chunks)
            if len(batch) >= FLUSH_CHUNKS:
                write_batch(conn, batch, embed)
                done += len(batch)
                batch = []
                rate = done / (time.monotonic() - embed_started)
                print(f"  {done:,}/{chunks:,} chunks, {rate:,.0f}/s, eta {(chunks - done) / rate / 60:.0f} min", flush=True)
        if batch:
            write_batch(conn, batch, embed)

        conn.execute(f"SET maintenance_work_mem = '{INDEX_BUILD_MEMORY}'")
        for name, ddl in SECTION_INDEXES.items():
            index_started = time.monotonic()
            conn.execute(ddl)
            print(f"built {name} in {(time.monotonic() - index_started) / 60:.1f} min", flush=True)
        conn.execute("ANALYZE legislation")
        conn.execute("ANALYZE legislation_sections")

    print(f"done in {(time.monotonic() - started) / 60:.1f} min — now run "
          f"`python -m scripts.link_statutes` to link ONCA decisions to these sections")


if __name__ == "__main__":
    main()
