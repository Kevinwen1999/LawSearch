"""Load federal legislation into legislation + legislation_sections, section-grained and embedded.

    python -m scripts.ingest_legislation --dry-run    # parse and count only
    python -m scripts.ingest_legislation              # rebuild the legislation tables

Sources:
- Acts and regulations: the official Justice Laws XML repository (justicecanada/laws-lois-xml),
  cloned shallow into DATA_DIR on first run. Pull it to update. Current consolidation only;
  each section keeps its in-force start date.
- Constitution Acts, 1867 and 1982 (incl. the Charter): the Justice Laws HTML page, since they
  are not published as XML.

Rebuilds from scratch and clears case->statute links, which point at section ids; re-run
scripts/link_statutes.py afterwards.
"""

import argparse
import subprocess
import time
from datetime import date
from pathlib import Path

import httpx
from pgvector import HalfVector

from app.config import settings
from app.db import connect
from app.statutes import CONSTITUTION_URL, LegislationDoc, iter_xml_docs, parse_constitution_html

REPO_URL = "https://github.com/justicecanada/laws-lois-xml.git"
FLUSH_CHUNKS = 4096
INDEX_BUILD_MEMORY = "8GB"
SECTION_INDEXES = {
    "legislation_sections_embedding_idx": (
        "CREATE INDEX IF NOT EXISTS legislation_sections_embedding_idx ON legislation_sections "
        "USING hnsw (embedding halfvec_cosine_ops)"
    ),
    # Name is referenced by app/retrieval.py (to_bm25query).
    "legislation_sections_bm25_idx": (
        "CREATE INDEX IF NOT EXISTS legislation_sections_bm25_idx ON legislation_sections "
        "USING bm25 (text) WITH (text_config = 'english')"
    ),
}


def ensure_xml_repo(data_dir: Path) -> tuple[Path, str]:
    repo = data_dir / "laws-lois-xml"
    if not (repo / ".git").exists():
        print(f"cloning {REPO_URL} (English only, shallow) into {repo}", flush=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", REPO_URL, str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "sparse-checkout", "set", "eng"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%h %cs"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo / "eng", commit


def load_docs(xml_root: Path, data_dir: Path) -> list[LegislationDoc]:
    docs = list(iter_xml_docs(xml_root))
    html = httpx.get(CONSTITUTION_URL, timeout=60, follow_redirects=True)
    html.raise_for_status()
    (data_dir / "constitution-fulltext.html").write_bytes(html.content)
    docs.extend(parse_constitution_html(html.content, date.today()))
    return docs


def write_batch(conn, batch: list[tuple], embed) -> None:
    vectors = embed([chunk.text for _, chunk in batch])
    with conn.transaction(), conn.cursor() as cur:
        with cur.copy(
            "COPY legislation_sections (legislation_id, section_no, section_label, chunk_no, marginal_note, "
            "hierarchy_path, text, in_force_start, url_official, embedding) FROM STDIN WITH (FORMAT BINARY)"
        ) as copy:
            copy.set_types(["uuid", "text", "text", "int4", "text", "text", "text", "date", "text", "halfvec"])
            for (legislation_id, chunk), vector in zip(batch, vectors):
                copy.write_row((legislation_id, chunk.section_no, chunk.section_label, chunk.chunk_no,
                                chunk.marginal_note, chunk.hierarchy_path, chunk.text, chunk.in_force_start,
                                chunk.url, HalfVector(vector)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="parse and report counts only")
    args = parser.parse_args()

    started = time.monotonic()
    data_dir = Path(settings.data_dir)
    xml_root, commit = ensure_xml_repo(data_dir)
    docs = load_docs(xml_root, data_dir)
    by_kind = {k: sum(d.kind == k for d in docs) for k in ("act", "regulation", "constitution")}
    chunks = sum(len(d.chunks) for d in docs)
    chars = sum(len(c.text) for d in docs for c in d.chunks)
    print(f"parsed {len(docs)} laws {by_kind} at {commit}: {chunks:,} chunks, "
          f"{chars / 1e6:.0f}M chars in {time.monotonic() - started:.0f}s", flush=True)
    if args.dry_run:
        return

    from app.embeddings import embed

    with connect(autocommit=True) as conn:
        conn.execute("DELETE FROM citation_edges WHERE dst_type = 'legislation_section'")
        for name in SECTION_INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {name}")
        conn.execute("TRUNCATE legislation CASCADE")

        batch: list[tuple] = []
        done = 0
        embed_started = time.monotonic()
        for doc in docs:
            source_version = commit if doc.kind != "constitution" else f"html {date.today()}"
            legislation_id = conn.execute(
                "INSERT INTO legislation (code, kind, citation, title, jurisdiction, consolidation_date, "
                "language, source, url_official, source_version) "
                "VALUES (%s, %s, %s, %s, 'federal', %s, 'en', 'justice-laws', %s, %s) RETURNING id",
                (doc.code, doc.kind, doc.citation, doc.title, doc.consolidation_date, doc.url, source_version),
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

    print(f"done in {(time.monotonic() - started) / 60:.1f} min")


if __name__ == "__main__":
    main()
