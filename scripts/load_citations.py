"""Rebuild the case->case citation graph (citation_edges) and cases.cited_by_count.

    python -m scripts.load_citations

Two sources, both resolved to loaded cases:
- A2AJ `cases_cited` lists: neutral citations, with French court codes mapped to English
  (the EN and FR versions of a decision list the same citations in each language).
- Supreme Court Reports citations extracted from decision text. A2AJ's lists only track
  neutral citations, so pre-2000 SCC authorities such as Baker are otherwise invisible.
"""

import time
from collections import Counter
from uuid import UUID

import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

from app.citations import canonical, scr_citations
from app.db import connect
from scripts.ingest_a2aj import FEDERAL_COURTS, REPO


def build_resolver(conn) -> tuple[dict[str, UUID], dict[tuple[str, str], UUID]]:
    """citation -> case id (ambiguous citations dropped), and (court, citation) -> case id."""
    by_court: dict[tuple[str, str], UUID] = {}
    owners: dict[str, set[UUID]] = {}
    for case_id, court, citation, citation2 in conn.execute(
        "SELECT id, court, citation, citation2 FROM cases"
    ):
        by_court[(court, citation)] = case_id
        for c in (citation, citation2):
            if c:
                owners.setdefault(c, set()).add(case_id)
    # A tribunal file number can belong to decisions in two courts; don't guess which.
    by_citation = {c: next(iter(ids)) for c, ids in owners.items() if len(ids) == 1}
    return by_citation, by_court


def main() -> None:
    started = time.monotonic()
    edges: dict[tuple[UUID, UUID], tuple[str, str]] = {}
    stats = Counter()

    with connect() as conn:
        by_citation, by_court = build_resolver(conn)

        for court in FEDERAL_COURTS:
            path = hf_hub_download(REPO, f"{court}/train.parquet", repo_type="dataset")
            columns = ["citation_en", "citation_fr", "cases_cited_en", "cases_cited_fr"]
            for row in pq.read_table(path, columns=columns).to_pylist():
                src_id = by_court.get((court, (row["citation_en"] or row["citation_fr"] or "").strip()))
                if src_id is None:
                    stats["a2aj_source_not_loaded"] += 1
                    continue
                for raw in set(row["cases_cited_en"] or []) | set(row["cases_cited_fr"] or []):
                    dst_id = by_citation.get(canonical(raw))
                    if dst_id is None:
                        stats["a2aj_unresolved"] += 1
                    elif dst_id != src_id:
                        edges.setdefault((src_id, dst_id), ("a2aj", raw))
            print(f"  {court}: {len(edges):,} edges so far", flush=True)

        with conn.cursor(name="scan_full_text") as cur:
            cur.itersize = 500
            cur.execute("SELECT id, full_text FROM cases")
            for src_id, text in cur:
                for citation in set(scr_citations(text or "")):
                    dst_id = by_citation.get(citation)
                    if dst_id is None:
                        stats["scr_unresolved"] += 1
                    elif dst_id != src_id and (src_id, dst_id) not in edges:
                        edges[(src_id, dst_id)] = ("text_scr", citation)
                        stats["scr_added"] += 1
        print(f"  SCR text extraction added {stats['scr_added']:,} edges", flush=True)

        with conn.transaction():
            conn.execute("DELETE FROM citation_edges WHERE edge_kind = 'case_cites_case'")
            with conn.cursor().copy(
                "COPY citation_edges (src_type, src_id, dst_type, dst_id, citation_raw, edge_kind, confidence, source) "
                "FROM STDIN"
            ) as copy:
                for (src_id, dst_id), (source, raw) in edges.items():
                    copy.write_row(("case", src_id, "case", dst_id, raw, "case_cites_case", 1.0, source))
            conn.execute("UPDATE cases SET cited_by_count = 0 WHERE cited_by_count <> 0")
            conn.execute(
                """
                UPDATE cases c SET cited_by_count = x.n
                FROM (SELECT dst_id, count(*) AS n FROM citation_edges
                      WHERE edge_kind = 'case_cites_case' GROUP BY dst_id) x
                WHERE c.id = x.dst_id
                """
            )
        conn.execute("ANALYZE citation_edges")
        conn.execute("ANALYZE cases")

    by_source = Counter(source for source, _ in edges.values())
    print(f"\n{len(edges):,} case->case edges ({dict(by_source)}) in {(time.monotonic() - started) / 60:.1f} min")
    print(f"unresolved: {dict(stats)}")


if __name__ == "__main__":
    main()
