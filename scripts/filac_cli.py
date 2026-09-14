"""Generate (or show the cached) FILAC brief for a decision by citation.

    python -m scripts.filac_cli "2018 SCC 19"
    python -m scripts.filac_cli TB6-11632 --court RPD --force
"""

import argparse
import sys
import time

from app import filac
from app.config import settings
from app.db import connect
from app.llm import LLMError

LABELS = {
    "facts": "Facts", "issues": "Issues", "law": "Law", "analysis": "Analysis", "conclusion": "Conclusion",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("citation", nargs="?")
    parser.add_argument("--court", help="disambiguate tribunal file numbers shared across courts")
    parser.add_argument("--force", action="store_true", help="regenerate even if cached")
    parser.add_argument("--reverify-all", action="store_true",
                        help="re-run verification on every cached brief (no model calls)")
    args = parser.parse_args()

    if args.reverify_all:
        with connect() as conn:
            case_ids = [r[0] for r in conn.execute(
                "SELECT case_id FROM filac_summaries WHERE prompt_version = %s AND model = %s",
                (filac.PROMPT_VERSION, settings.filac_model),
            )]
        for case_id in case_ids:
            record = filac.reverify(connect, case_id)
            resolved = sum(len(c.get("resolved_sections", [])) for c in record.verification["sections"]["law"])
            print(f"reverified {case_id}: {record.verification['problems']} problems, {resolved} statute sections resolved")
        return
    if not args.citation:
        parser.error("citation is required unless --reverify-all")

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, citation, style_of_cause, court FROM cases "
            "WHERE (citation = %(c)s OR citation2 = %(c)s) AND (%(court)s::text IS NULL OR court = %(court)s)",
            {"c": args.citation, "court": args.court.upper() if args.court else None},
        ).fetchall()
    if len(rows) != 1:
        sys.exit(f"expected one case for {args.citation!r}, found {[(r[1], r[3]) for r in rows]}")
    case_id, citation, name, court = rows[0]

    print(f"{citation} [{court}] {name}")
    print(f"backend={settings.filac_backend} model={settings.filac_model} prompt={filac.PROMPT_VERSION}\n")
    started = time.monotonic()
    try:
        record = filac.generate(connect, case_id, force=args.force)
    except LLMError as exc:
        sys.exit(f"generation failed: {exc}")

    verification = record.verification
    mark = "¶" if verification["anchor_type"] == "paragraph" else "passage "
    for section, label in LABELS.items():
        block = record.summary[section]
        print(f"== {label} ({block['status']}) ==")
        for item, check in zip(block["items"], verification["sections"][section]):
            flags = "" if check["anchor_ok"] else "  [ANCHOR NOT IN DECISION]"
            if section == "law":
                if not check["found_in_document"]:
                    flags += "  [NOT FOUND IN TEXT]"
                if check["resolved_case"]:
                    flags += f"  [in corpus: {check['resolved_case']['citation']}]"
                print(f"- {item['authority']} ({item['kind']}, relied on by {item['relied_on_by']}) "
                      f"{mark}{item['anchor']}{flags}")
            elif section == "analysis":
                who = "" if item["attribution"] == "court" else f"[{item['attribution']}] "
                print(f"- {who}{item['text']} {mark}{item['anchor']}{flags}")
            else:
                print(f"- {item['text']} {mark}{item['anchor']}{flags}")
        print()

    print(f"verification problems: {verification['problems']} "
          f"(of {verification['anchor_count']} {verification['anchor_type']}s)")
    print(f"usage: {record.usage}  | {time.monotonic() - started:.1f}s  | cached at {record.created_at:%Y-%m-%d %H:%M}")


if __name__ == "__main__":
    main()
