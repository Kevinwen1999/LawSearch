"""Load A2AJ court decisions into cases + case_chunks, embedding chunks on the GPU.

    python -m scripts.ingest_a2aj SCC FCA              # specific courts
    python -m scripts.ingest_a2aj --federal            # every federal court and tribunal
    python -m scripts.ingest_a2aj SCC --limit 200      # quick iteration
    python -m scripts.ingest_a2aj --federal --dry-run  # chunk/storage stats, no GPU or DB writes

Re-runnable: decisions already loaded (matched by citation) are skipped, so an
interrupted run resumes where it stopped. The search indexes (HNSW vectors, BM25
keywords) are dropped for the load and rebuilt at the end, which is much faster than
maintaining them row by row — but it also means even a small top-up rebuilds both.

Using several GPUs: run one process per GPU on disjoint courts with --no-index, then
build the index once afterwards:

    EMBEDDING_DEVICE=cuda:0 python -m scripts.ingest_a2aj FC TCC --no-index
    EMBEDDING_DEVICE=cuda:1 python -m scripts.ingest_a2aj RAD SST --no-index
    python -m scripts.ingest_a2aj --build-index
"""

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date

import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download
from pgvector import HalfVector
from psycopg.types.json import Jsonb

from app.chunking import Chunk, chunk_judgment, clean
from app.db import connect

REPO = "a2aj/canadian-case-law"

FEDERAL_COURTS = [
    "SCC", "FCA", "FC", "TCC", "CMAC",
    "CHRT", "CIRB", "CITT", "CT", "FPSLREB", "OHSTC", "OIC", "PSDPT",
    "RAD", "RPD", "RLLR", "SST", "TATC", "CART", "SCT",
]

LANG_COLUMNS = [
    "citation", "citation2", "name", "document_date", "url", "unofficial_text",
]
COLUMNS = [f"{c}_{lang}" for lang in ("en", "fr") for c in LANG_COLUMNS] + ["upstream_license"]

# Scraped decisions start with a metadata block that ends at this line.
HEADER_MARKERS = ("Decision Content", "Contenu de la décision")
HEADER_SEARCH_CHARS = 6000

FLUSH_CHUNKS = 4096
READ_BATCH_ROWS = 256

INDEX_BUILD_MEMORY = "12GB"
SEARCH_INDEXES = {
    "case_chunks_embedding_idx": (
        "CREATE INDEX IF NOT EXISTS case_chunks_embedding_idx ON case_chunks "
        "USING hnsw (embedding halfvec_cosine_ops)"
    ),
    # Name is referenced by app/retrieval.py (to_bm25query).
    "case_chunks_bm25_idx": (
        "CREATE INDEX IF NOT EXISTS case_chunks_bm25_idx ON case_chunks "
        "USING bm25 (text) WITH (text_config = 'english')"
    ),
}


@dataclass
class Decision:
    citation: str
    citation2: str | None
    name: str | None
    court: str
    decision_date: date | None
    language: str
    url: str | None
    license: str | None
    text: str
    chunks: list[Chunk]


def strip_header(text: str) -> str:
    head = text[:HEADER_SEARCH_CHARS]
    for marker in HEADER_MARKERS:
        idx = head.find(marker)
        if idx != -1:
            return text[idx + len(marker):].lstrip()
    return text


def normalize(row: dict, court: str) -> Decision | None:
    """English text when present, otherwise French (a small share of decisions are FR-only)."""
    for lang in ("en", "fr"):
        raw = row[f"unofficial_text_{lang}"]
        citation = row[f"citation_{lang}"]
        if not raw or not citation:
            continue
        text = clean(strip_header(clean(raw)))
        chunks = chunk_judgment(text)
        if not chunks:
            return None
        decided = row[f"document_date_{lang}"]
        return Decision(
            citation=citation.strip(),
            citation2=row[f"citation2_{lang}"],
            name=row[f"name_{lang}"],
            court=court,
            decision_date=decided.date() if decided else None,
            language=lang,
            url=row[f"url_{lang}"],
            license=row["upstream_license"],
            text=text,
            chunks=chunks,
        )
    return None


def iter_decisions(court: str, limit: int | None):
    path = hf_hub_download(REPO, f"{court}/train.parquet", repo_type="dataset")
    parquet = pq.ParquetFile(path)
    total = parquet.metadata.num_rows if limit is None else min(limit, parquet.metadata.num_rows)
    seen = 0
    for batch in parquet.iter_batches(batch_size=READ_BATCH_ROWS, columns=COLUMNS):
        for row in batch.to_pylist():
            if seen >= total:
                return
            seen += 1
            yield total, normalize(row, court)


def dry_run(courts: list[str], limit: int | None) -> None:
    grand_chunks = grand_chars = 0
    print(f"{'court':8} {'rows':>7} {'loadable':>8} {'no text':>7} {'dup cit':>7} "
          f"{'numbered':>8} {'chunks':>9} {'avg chars':>9}")
    for court in courts:
        rows = loadable = empty = dups = numbered = chunks = chars = 0
        seen: set[str] = set()
        for _, decision in iter_decisions(court, limit):
            rows += 1
            if decision is None:
                empty += 1
                continue
            if decision.citation in seen:
                dups += 1
                continue
            seen.add(decision.citation)
            loadable += 1
            numbered += any(c.para_no is not None for c in decision.chunks)
            chunks += len(decision.chunks)
            chars += sum(len(c.text) for c in decision.chunks)
        grand_chunks += chunks
        grand_chars += chars
        print(f"{court:8} {rows:7d} {loadable:8d} {empty:7d} {dups:7d} "
              f"{100 * numbered / max(1, loadable):7.0f}% {chunks:9d} {chars / max(1, chunks):9.0f}")
    # ~7 KB per chunk at halfvec(1024): vector + HNSW entry + text + tsvector + GIN + btrees.
    print(f"\nTOTAL chunks {grand_chunks:,}  (~{grand_chunks * 7 / 1e6:.1f} GB in Postgres incl. indexes, "
          f"plus ~{grand_chars / 2 / 1e9:.1f} GB compressed full text)")


def ingest(courts: list[str], limit: int | None, build: bool) -> None:
    from app.embeddings import embed, get_model

    model = get_model()
    print(f"embedding with {model.model_card_data.base_model or 'model'} on {model.device}, "
          f"dtype={next(model.parameters()).dtype}", flush=True)

    with connect(autocommit=True) as conn:
        job_id = conn.execute(
            "INSERT INTO jobs (kind, status, params, started_at) "
            "VALUES ('ingest_a2aj', 'running', %s, now()) RETURNING id",
            (Jsonb({"courts": courts, "limit": limit, "build_index": build}),),
        ).fetchone()[0]

        try:
            for name in SEARCH_INDEXES:
                conn.execute(f"DROP INDEX IF EXISTS {name}")
            progress = {}
            for court in courts:
                progress[court] = load_court(conn, court, limit, embed)
                conn.execute("UPDATE jobs SET cursor = %s WHERE id = %s", (Jsonb(progress), job_id))
            if build:
                build_index(conn)
            conn.execute(
                "UPDATE jobs SET status = 'done', finished_at = now() WHERE id = %s", (job_id,)
            )
        except BaseException as exc:
            conn.execute(
                "UPDATE jobs SET status = 'failed', error = %s, finished_at = now() WHERE id = %s",
                (repr(exc), job_id),
            )
            raise


def load_court(conn, court: str, limit: int | None, embed) -> dict:
    existing = {
        row[0] for row in conn.execute("SELECT citation FROM cases WHERE court = %s", (court,))
    }
    pending: list[Decision] = []
    pending_chunks = 0
    stats = {"loaded": 0, "skipped": 0, "no_text": 0, "chunks": 0}
    started = time.monotonic()
    total = 0

    def flush():
        nonlocal pending, pending_chunks
        if pending:
            write_batch(conn, pending, embed)
            stats["loaded"] += len(pending)
            stats["chunks"] += pending_chunks
            pending, pending_chunks = [], 0
            elapsed = time.monotonic() - started
            done = stats["loaded"] + stats["skipped"] + stats["no_text"]
            rate = stats["chunks"] / elapsed
            eta = (total - done) * (elapsed / max(1, done))
            print(f"  {court}: {done:,}/{total:,} decisions, {stats['chunks']:,} chunks, "
                  f"{rate:,.0f} chunks/s, eta {eta / 60:,.0f} min", flush=True)

    for total, decision in iter_decisions(court, limit):
        if decision is None:
            stats["no_text"] += 1
            continue
        if decision.citation in existing:
            stats["skipped"] += 1
            continue
        existing.add(decision.citation)
        pending.append(decision)
        pending_chunks += len(decision.chunks)
        if pending_chunks >= FLUSH_CHUNKS:
            flush()
    flush()

    print(f"{court} done: {stats} in {(time.monotonic() - started) / 60:.1f} min", flush=True)
    return stats


def write_batch(conn, decisions: list[Decision], embed) -> None:
    vectors = embed([c.text for d in decisions for c in d.chunks])

    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO cases (citation, citation2, style_of_cause, court, jurisdiction,
                               decision_date, language, source, url_official,
                               upstream_license, full_text)
            VALUES (%s, %s, %s, %s, 'federal', %s, %s, 'a2aj', %s, %s, %s)
            RETURNING id
            """,
            [(d.citation, d.citation2, d.name, d.court, d.decision_date, d.language,
              d.url, d.license, d.text) for d in decisions],
            returning=True,
        )
        case_ids = []
        while True:
            case_ids.append(cur.fetchone()[0])
            if not cur.nextset():
                break

        with cur.copy(
            "COPY case_chunks (case_id, chunk_no, para_no, para_end, text, embedding) "
            "FROM STDIN WITH (FORMAT BINARY)"
        ) as copy:
            copy.set_types(["uuid", "int4", "int4", "int4", "text", "halfvec"])
            i = 0
            for case_id, decision in zip(case_ids, decisions):
                for chunk_no, chunk in enumerate(decision.chunks):
                    copy.write_row((case_id, chunk_no, chunk.para_no, chunk.para_end,
                                    chunk.text, HalfVector(vectors[i])))
                    i += 1


def build_index(conn) -> None:
    conn.execute(f"SET maintenance_work_mem = '{INDEX_BUILD_MEMORY}'")
    for name, ddl in SEARCH_INDEXES.items():
        started = time.monotonic()
        print(f"building {name}...", flush=True)
        conn.execute(ddl)
        print(f"{name} built in {(time.monotonic() - started) / 60:.1f} min", flush=True)
    conn.execute("ANALYZE cases")
    conn.execute("ANALYZE case_chunks")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("courts", nargs="*", help="A2AJ court codes, e.g. SCC FCA")
    parser.add_argument("--federal", action="store_true", help="all federal courts and tribunals")
    parser.add_argument("--limit", type=int, help="max decisions per court")
    parser.add_argument("--dry-run", action="store_true", help="chunking stats only")
    parser.add_argument("--no-index", action="store_true", help="skip the search index rebuild (parallel loads)")
    parser.add_argument("--build-index", action="store_true", help="only build missing search indexes (HNSW, BM25)")
    args = parser.parse_args()

    if args.build_index:
        with connect(autocommit=True) as conn:
            build_index(conn)
        return

    courts = FEDERAL_COURTS if args.federal else [c.upper() for c in args.courts]
    unknown = [c for c in courts if c not in FEDERAL_COURTS]
    if not courts or unknown:
        parser.error(f"choose courts from {FEDERAL_COURTS}" + (f"; unknown: {unknown}" if unknown else ""))

    if args.dry_run:
        dry_run(courts, args.limit)
    else:
        ingest(courts, args.limit, build=not args.no_index)


if __name__ == "__main__":
    sys.exit(main())
