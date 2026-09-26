"""Score case briefs against gold briefs (eval/briefs/*.yaml).

Each gold file records a model answer as the paragraphs each part must (and must not) come from,
so a generated brief is scored without a human reading it.

    python -m scripts.eval_briefs                          # configured backend, cached briefs
    python -m scripts.eval_briefs --force                  # regenerate
    python -m scripts.eval_briefs --backend lmstudio --model qwen/qwen3.8-27b --effort medium
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from app import brief_format, filac
from app.db import connect
from app.llm import LLMError

GOLD_DIR = Path(__file__).resolve().parent.parent / "eval" / "briefs"


def score(gold: dict, record: filac.FilacRecord) -> list[tuple[str, bool, str]]:
    """(check, passed, detail) rows."""
    summary, rows = record.summary, []

    def check(name: str, passed: bool, detail: str = "") -> None:
        rows.append((name, passed, detail))

    def anchors(section: str, where=lambda item: True) -> list[int]:
        return [item["anchor"] for item in summary[section]["items"] if where(item)]

    shown = brief_format.display(record)
    prelim = gold.get("preliminary", {})
    if "name_and_citation" in prelim:
        check("preliminary: name and citation", shown["name_and_citation"] == prelim["name_and_citation"],
              shown["name_and_citation"])
    if "decision_date" in prelim:
        check("preliminary: date", shown["decision_date"] == prelim["decision_date"], shown["decision_date"])
    if "citation_read" in prelim:
        read = summary["preliminary"]["citation"]
        check("preliminary: citation read from header", prelim["citation_read"] in read, read)
    if "parties" in prelim:
        check("preliminary: parties", shown["parties"] == prelim["parties"], "; ".join(shown["parties"]))

    issues = summary["issues"]["items"]
    g = gold.get("issues", {})
    if "count" in g:
        check("issues: count", len(issues) == g["count"], str(len(issues)))
    if "anchors_any" in g:
        got = anchors("issues")
        check("issues: main issue anchor", bool(got) and got[0] in g["anchors_any"], str(got))
    questions = " ".join(i["question"] for i in issues).lower()
    check("issues: begin with 'Whether'", all(i["question"].lower().startswith("whether") for i in issues))
    for term in g.get("sub_issue_terms", []):
        subs = " ".join(s["question"] for i in issues for s in i["sub_issues"]).lower()
        check(f"issues: sub-issue mentions {term!r}", term in subs, subs[:120])
    for term in g.get("exclude_terms", []):
        check(f"issues: no {term!r} issue", term not in questions)

    if "anchors_any" in (u := gold.get("undecided_issues", {})):
        got = anchors("undecided_issues")
        check("undecided issues: listed", any(a in u["anchors_any"] for a in got), str(got))

    f = gold.get("facts", {})
    events = anchors("facts", lambda i: i["kind"] == "event")
    if "event_anchors" in f:
        missing = sorted(set(f["event_anchors"]) - set(events))
        check("facts: required events", not missing, f"events {events}, missing {missing}")
    if "not_event_anchors" in f:
        leaked = sorted(set(f["not_event_anchors"]) & set(events))
        check("facts: no procedural history", not leaked, f"leaked {leaked}")

    r = gold.get("ratio", {})
    got = anchors("ratio")
    if "anchors" in r:
        missing = sorted(set(r["anchors"]) - set(got))
        check("ratio: required paragraphs", not missing, f"ratio {got}, missing {missing}")
    if "first_anchor" in r:
        first = summary["ratio"]["items"][0] if got else None
        check("ratio: starts with the rule", bool(first) and first["anchor"] == r["first_anchor"]
              and first["role"] == "rule", str(first and (first["anchor"], first["role"])))
    if "not_anchors" in r:
        wrong = sorted(set(r["not_anchors"]) & set(got))
        check("ratio: no law/framework paragraphs", not wrong, f"wrong {wrong}")

    d = gold.get("decision", {})
    got = anchors("decision")
    if "anchors" in d:
        missing = sorted(set(d["anchors"]) - set(got))
        check("decision: required paragraphs", not missing, f"decision {got}, missing {missing}")
    answers = " ".join(i["answer"] for i in summary["decision"]["items"]).lower()
    for term in d.get("exclude_terms", []):
        check(f"decision: no {term!r} (disposition)", term not in answers)

    if "anchors_any" in (disp := gold.get("disposition", {})):
        got = anchors("disposition")
        check("disposition: present", any(a in disp["anchors_any"] for a in got), str(got))

    law = " ".join(i["authority"] for i in summary["law"]["items"]).lower()
    for term in gold.get("law", {}).get("terms", []):
        check(f"law: {term}", term.lower() in law)

    verification = record.verification
    check("verification: no problems", verification["problems"] == 0, str(verification["problems"]))
    check("verification: no format warnings", not verification["rule_warnings"],
          "; ".join(f"{w['section']}: {w['message']}" for w in verification["rule_warnings"]))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="gold file stems (default: all)")
    parser.add_argument("--force", action="store_true", help="regenerate even if cached")
    parser.add_argument("--backend", choices=["claude-cli", "api", "lmstudio"])
    parser.add_argument("--model")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--out", type=Path, help="write results as JSON")
    args = parser.parse_args()

    llm = filac.BriefBackend.configured()
    if args.backend or args.model or args.effort:
        backend, model, effort = args.backend or llm.backend, args.model or llm.model, args.effort or llm.effort
        llm = filac.BriefBackend(backend, model, effort, backend, model, effort)

    paths = sorted(GOLD_DIR.glob("*.yaml"))
    if args.names:
        paths = [p for p in paths if p.stem in args.names]
    results, failed = {}, 0
    for path in paths:
        gold = yaml.safe_load(path.read_text(encoding="utf-8"))
        with connect() as conn:
            row = conn.execute(
                "SELECT id FROM cases WHERE citation = %s AND court = %s", (gold["citation"], gold["court"])
            ).fetchone()
        if row is None:
            print(f"{path.stem}: {gold['citation']} not in the corpus")
            failed += 1
            continue
        try:
            record = filac.generate(connect, row[0], force=args.force, llm=llm)
        except LLMError as exc:
            print(f"{path.stem}: generation failed: {exc}")
            failed += 1
            continue

        rows = score(gold, record)
        passed = sum(ok for _, ok, _ in rows)
        print(f"\n== {path.stem} ({gold['citation']}) via {record.backend}/{record.model}: {passed}/{len(rows)} checks ==")
        for name, ok, detail in rows:
            print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))
        failed += len(rows) - passed
        results[path.stem] = {
            "model": record.model, "backend": record.backend, "usage": record.usage,
            "checks": [{"check": n, "passed": ok, "detail": d} for n, ok, d in rows],
            "brief_markdown": brief_format.to_markdown(record, anchors=True),
        }

    if args.out:
        args.out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
