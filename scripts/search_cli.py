"""Search the corpus from the command line, with the same retriever as POST /search.

    python -m scripts.search_cli "right to counsel breath sample"
    python -m scripts.search_cli "hobby losses deductible" --mode lexical --court TCC FCA -k 5
"""

import argparse

from app.db import connect
from app.retrieval import prepare_session, search


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query")
    parser.add_argument("--mode", choices=["hybrid", "lexical", "vector"], default="hybrid")
    parser.add_argument("--court", nargs="+", help="restrict to court codes")
    parser.add_argument("-k", type=int, default=10)
    parser.add_argument("--passages", type=int, default=1, help="passages to print per case")
    args = parser.parse_args()
    courts = [c.upper() for c in args.court] if args.court else None

    with connect() as conn:
        prepare_session(conn)
        result = search(conn, args.query, k=args.k, mode=args.mode, courts=courts)

    timings = ", ".join(f"{name} {ms:.0f} ms" for name, ms in result.timings_ms.items())
    print(f"=== {args.mode}: {len(result.cases)} cases ({timings}) ===")
    for rank, case in enumerate(result.cases, 1):
        ranks = (f"lex {case.lexical_rank or '-'} / vec {case.vector_rank or '-'} / "
                 f"graph {case.graph_rank or '-'} / cited by {case.cited_by_count}")
        print(f"{rank:2}. {case.citation} [{case.court}] {case.style_of_cause}  ({ranks})")
        for passage in case.passages[: args.passages]:
            paras = "" if passage.para_no is None else (
                f"para {passage.para_no}" if passage.para_end == passage.para_no
                else f"paras {passage.para_no}-{passage.para_end}"
            )
            snippet = " ".join(passage.text.split())[:150]
            print(f"      {paras} [{'+'.join(passage.matched_by)}] {snippet}...")


if __name__ == "__main__":
    main()
