"""Link decisions to the federal statute sections they cite (citation_edges) and count citations.

    python -m scripts.link_statutes
    python -m scripts.link_statutes --sample 40    # print sample links for a precision check

Rebuilds all case->statute edges. Run after scripts/ingest_legislation.py, which clears them.
A reference resolves to the section chunk covering its subsection pinpoint, else the section's
first chunk. confidence: 1.0 when the law is named in the phrase, 0.8 when reached through
'the Act' or a defined alias.
"""

import argparse
import random
import time
from collections import Counter, defaultdict
from uuid import UUID

from app.db import connect
from app.statute_refs import StatuteIndex, StatuteRef, pick_chunk


class SectionResolver:
    def __init__(self, conn):
        self.chunks: dict[tuple[str, str], list[tuple[UUID, str, int]]] = defaultdict(list)
        for chunk_id, code, section_no, label, chunk_no in conn.execute(
            "SELECT s.id, l.code, s.section_no, s.section_label, s.chunk_no "
            "FROM legislation_sections s JOIN legislation l ON l.id = s.legislation_id"
        ):
            self.chunks[(code, section_no)].append((chunk_id, label, chunk_no))
        for rows in self.chunks.values():
            rows.sort(key=lambda r: r[2])

    def resolve(self, ref: StatuteRef) -> UUID | None:
        row = pick_chunk(self.chunks.get((ref.code, ref.section_no), []), ref)
        return row[0] if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=int, default=0, help="print N random links with context")
    args = parser.parse_args()

    started = time.monotonic()
    edges: dict[tuple[UUID, UUID], tuple[str, float]] = {}
    stats = Counter()
    samples = []

    with connect() as conn:
        index = StatuteIndex.from_db(conn)
        resolver = SectionResolver(conn)
        with conn.cursor(name="scan_decisions") as cur:
            cur.itersize = 500
            cur.execute("SELECT id, full_text FROM cases")
            for n, (case_id, text) in enumerate(cur, 1):
                text = text or ""
                found = index.extract(text)
                stats["decisions_with_refs"] += bool(found)
                for ref in found:
                    chunk_id = resolver.resolve(ref)
                    if chunk_id is None:
                        stats["unresolved_refs"] += 1
                        continue
                    stats["resolved_refs"] += 1
                    confidence = 1.0 if ref.explicit else 0.8
                    key = (case_id, chunk_id)
                    if key not in edges or edges[key][1] < confidence:
                        edges[key] = (f"{ref.code} s. {ref.section_no}{ref.pinpoint}", confidence)
                    if args.sample and random.random() < 0.002:
                        samples.append((ref, text[max(0, ref.start - 140): ref.start + 90]))
                if n % 20000 == 0:
                    print(f"  {n:,} decisions, {len(edges):,} edges", flush=True)

        with conn.transaction():
            conn.execute("DELETE FROM citation_edges WHERE edge_kind = 'case_cites_statute'")
            with conn.cursor().copy(
                "COPY citation_edges (src_type, src_id, dst_type, dst_id, citation_raw, edge_kind, confidence, source) "
                "FROM STDIN"
            ) as copy:
                for (case_id, chunk_id), (raw, confidence) in edges.items():
                    copy.write_row(("case", case_id, "legislation_section", chunk_id, raw,
                                    "case_cites_statute", confidence, "text_statute"))
            # Section-level count: distinct decisions citing any chunk of the section.
            conn.execute("UPDATE legislation_sections SET cited_by_count = 0 WHERE cited_by_count <> 0")
            conn.execute(
                """
                UPDATE legislation_sections s SET cited_by_count = x.n
                FROM (
                    SELECT t.legislation_id, t.section_no, count(DISTINCT e.src_id) AS n
                    FROM citation_edges e JOIN legislation_sections t ON t.id = e.dst_id
                    WHERE e.edge_kind = 'case_cites_statute'
                    GROUP BY t.legislation_id, t.section_no
                ) x
                WHERE s.legislation_id = x.legislation_id AND s.section_no = x.section_no
                """
            )
        conn.execute("ANALYZE citation_edges")

    print(f"\n{len(edges):,} case->section edges in {(time.monotonic() - started) / 60:.1f} min; {dict(stats)}")
    for ref, context in random.sample(samples, min(args.sample, len(samples))):
        print(f"\n[{ref.code} s. {ref.section_no}{ref.pinpoint} | {'explicit' if ref.explicit else 'via the Act'}]")
        print("   ..." + " ".join(context.split()) + "...")


if __name__ == "__main__":
    main()
