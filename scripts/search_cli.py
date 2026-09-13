"""Query the loaded corpus from the command line (lexical, vector, or both).

    python -m scripts.search_cli "right to counsel after arrest"
    python -m scripts.search_cli "hobby losses deductible" --mode vector --court TCC FCA -k 5

Chunk-level results; case-level ranking and fusion arrive with the Phase 2 retriever.
"""

import argparse
import time

from pgvector import HalfVector

from app.db import connect

VECTOR_SQL = """
SELECT c.citation, c.style_of_cause, c.court, ch.para_no, ch.para_end,
       1 - (ch.embedding <=> %(q)s) AS score, ch.text
FROM case_chunks ch
JOIN cases c ON c.id = ch.case_id
WHERE %(courts)s::text[] IS NULL OR c.court = ANY(%(courts)s)
ORDER BY ch.embedding <=> %(q)s
LIMIT %(k)s
"""

LEXICAL_SQL = """
SELECT c.citation, c.style_of_cause, c.court, ch.para_no, ch.para_end,
       ts_rank_cd(ch.tsv, q) AS score, ch.text
FROM case_chunks ch
JOIN cases c ON c.id = ch.case_id,
     websearch_to_tsquery('english', %(q)s) q
WHERE ch.tsv @@ q
  AND (%(courts)s::text[] IS NULL OR c.court = ANY(%(courts)s))
ORDER BY score DESC
LIMIT %(k)s
"""


def show(label: str, rows: list, elapsed: float) -> None:
    print(f"\n=== {label} ({elapsed * 1000:.0f} ms) ===")
    for rank, (citation, name, court, para_no, para_end, score, text) in enumerate(rows, 1):
        paras = "" if para_no is None else f" paras {para_no}" + ("" if para_end == para_no else f"-{para_end}")
        snippet = " ".join(text.split())[:160]
        print(f"{rank:2}. {score:.3f}  {citation} [{court}]{paras}  {name}\n      {snippet}...")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query")
    parser.add_argument("--mode", choices=["vector", "lexical", "both"], default="both")
    parser.add_argument("--court", nargs="+", help="restrict to court codes")
    parser.add_argument("-k", type=int, default=10)
    args = parser.parse_args()
    courts = [c.upper() for c in args.court] if args.court else None

    with connect() as conn:
        # Keep scanning the HNSW graph when a court filter discards candidates.
        conn.execute("SET hnsw.ef_search = 200")
        conn.execute("SET hnsw.iterative_scan = relaxed_order")

        if args.mode in ("lexical", "both"):
            started = time.monotonic()
            rows = conn.execute(LEXICAL_SQL, {"q": args.query, "courts": courts, "k": args.k}).fetchall()
            show("lexical", rows, time.monotonic() - started)

        if args.mode in ("vector", "both"):
            from app.embeddings import embed

            query_vector = HalfVector(embed([args.query])[0])
            started = time.monotonic()
            rows = conn.execute(VECTOR_SQL, {"q": query_vector, "courts": courts, "k": args.k}).fetchall()
            show("vector", rows, time.monotonic() - started)


if __name__ == "__main__":
    main()
