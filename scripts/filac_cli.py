"""Generate (or show the cached) case brief for a decision by citation, or for a local text file.

    python -m scripts.filac_cli "2013 ONCA 585"
    python -m scripts.filac_cli TB6-11632 --court RPD --force
    python -m scripts.filac_cli --text-file joly.txt              # input kind detected
    python -m scripts.filac_cli --text-file notes.txt --kind description
    python -m scripts.filac_cli "2013 ONCA 585" --backend lmstudio --model qwen/qwen3.8-27b
"""

import argparse
import sys
import time
from pathlib import Path

from app import brief_format, filac
from app.config import settings
from app.db import connect
from app.llm import LLMError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("citation", nargs="?")
    parser.add_argument("--court", help="disambiguate tribunal file numbers shared across courts")
    parser.add_argument("--text-file", type=Path, help="brief a local decision or description instead")
    parser.add_argument("--kind", choices=["decision", "description"], help="input kind for --text-file")
    parser.add_argument("--force", action="store_true", help="regenerate even if cached")
    parser.add_argument("--backend", choices=["claude-cli", "api", "lmstudio"],
                        help="override FILAC_BACKEND for this run (no fallback)")
    parser.add_argument("--model", help="override FILAC_MODEL for this run")
    parser.add_argument("--brief-only", action="store_true", help="omit the full case reading")
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

    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8")
        with connect() as conn:
            user_case = filac.create_user_case(conn, full_text=text, source="upload", input_kind=args.kind)
        case_id = user_case.case_id
        print(f"{args.text_file} ({user_case.input_kind}, {len(text):,} chars)")
        force = args.force or user_case.kind_changed
    elif args.citation:
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
        force = args.force
    else:
        parser.error("citation or --text-file is required unless --reverify-all")

    llm = filac.BriefBackend.configured()
    if args.backend or args.model:
        backend, model = args.backend or llm.backend, args.model or llm.model
        llm = filac.BriefBackend(backend, model, llm.effort, backend, model, llm.effort)
    print(f"backend={llm.backend} model={llm.model} prompt={filac.PROMPT_VERSION}\n")

    started = time.monotonic()
    try:
        record = filac.generate(connect, case_id, force=force, llm=llm)
    except LLMError as exc:
        sys.exit(f"generation failed: {exc}")

    print(brief_format.to_markdown(record, anchors=True, full_reading=not args.brief_only))
    print_checks(record)
    print(f"usage: {record.usage}  | {time.monotonic() - started:.1f}s  | cached at {record.created_at:%Y-%m-%d %H:%M}")


def print_checks(record: filac.FilacRecord) -> None:
    verification = record.verification
    for name, checks in verification["sections"].items():
        for item, check in zip(record.summary[name]["items"], checks):
            flags = [] if check["anchor_ok"] else ["anchor not in text"]
            if name == "law":
                if not check["found_in_document"]:
                    flags.append("not found in text")
                if check["resolved_case"]:
                    flags.append(f"in corpus: {check['resolved_case']['citation']}")
                flags += [f"-> {s['title']} s. {s['section']}" for s in check.get("resolved_sections", [])]
            if flags:
                label = item.get("authority") or item.get("question") or item.get("text") or item.get("answer")
                print(f"[{name} ¶{item['anchor']}] {label[:80]}: {'; '.join(flags)}")
    for party, check in zip(record.summary["preliminary"]["parties"], verification["preliminary"]["parties"]):
        if not check["found_in_document"]:
            print(f"[preliminary] party {party['name']!r} not found in text")
    for w in verification["rule_warnings"]:
        where = f"{w['section']}[{w['index']}]" if w["index"] is not None else w["section"]
        print(f"warning {where}: {w['message']}")
    print(f"verification problems: {verification['problems']} "
          f"(of {verification['anchor_count']} {verification['anchor_type']}s), "
          f"{len(verification['rule_warnings'])} format warnings")


if __name__ == "__main__":
    main()
