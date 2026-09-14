"""Draft grounded eval scenarios whose gold answers include lower-court and tribunal decisions.

    python -m scripts.draft_eval_scenarios --out eval/drafts/lower-courts.yaml

Each scenario is written from a real decision in the corpus: the model reads the decision,
describes the client's situation without names, citations or the outcome, and lists the
authorities the decision applies for the governing test. Gold = the decision itself
(primary) plus those authorities that resolve to loaded cases (supporting), so no citation
comes from model recall. Drafts still need human review before `verified: true`.

Known-item scenarios are pessimistic for recall: an equally relevant sibling decision
(there are hundreds of EI misconduct cases) counts as a miss. Compare methods, not
absolute numbers.
"""

import argparse
import random
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from app.citations import canonical, case_citations
from app.config import settings
from app.db import connect
from app.filac import load_document
from app.llm import LLMError, get_backend

PLAN = {"FC": 4, "FCA": 3, "TCC": 3, "RAD": 2, "SST": 2, "FPSLREB": 1, "CHRT": 1, "CIRB": 1, "CITT": 1}
MIN_CITED_BY = {"FC": 10, "FCA": 10, "TCC": 5}  # tribunals are rarely cited; any count qualifies

SYSTEM_PROMPT = """\
You build evaluation scenarios for a Canadian legal research search engine. Given one decision, you describe the situation a lawyer's client might bring in, such that this decision would be a genuinely useful authority to find.

- Base the scenario on the decision's legally material facts, told in plain language as a client or lawyer would describe them, in 3-6 sentences.
- Leave out anything that would let a keyword search find this exact document: party names, file numbers, citations, quotations, the tribunal member's name, and distinctive incidental details (exact dates, small towns, company names). Change incidental details while keeping the facts that matter legally.
- Do not reveal how the decision came out. End with the question the client wants answered.
- List up to 3 cases the decision relies on for the governing legal test, with the citation copied exactly as it appears in the text and the paragraph where it is applied. Only cases, not statutes.
- Mark the decision unsuitable if it turns only on procedure (an extension of time, costs, a stay) or on facts too idiosyncratic to recur, and say why."""

INSTRUCTION = "Draft the evaluation scenario for the decision provided."

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["suitable", "unsuitable_reason", "area", "scenario", "legal_question", "answer_anchor", "authorities_applied"],
    "properties": {
        "suitable": {"type": "boolean"},
        "unsuitable_reason": {"type": "string"},
        "area": {"type": "string", "description": "e.g. 'employment insurance / misconduct'"},
        "scenario": {"type": "string"},
        "legal_question": {"type": "string"},
        "answer_anchor": {"type": "integer"},
        "authorities_applied": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["citation", "anchor"],
                "properties": {"citation": {"type": "string"}, "anchor": {"type": "integer"}},
            },
        },
    },
}


def pick_decisions(conn, seed: int) -> list[dict]:
    rng = random.Random(seed)
    picked = []
    for court, n in PLAN.items():
        rows = conn.execute(
            """
            SELECT c.id, c.citation, c.style_of_cause, c.court, c.cited_by_count
            FROM cases c
            WHERE c.court = %s AND c.language = 'en' AND c.decision_date >= '2008-01-01'
              AND length(c.full_text) BETWEEN 15000 AND 60000
              AND c.cited_by_count >= %s
              AND EXISTS (SELECT 1 FROM case_chunks ch WHERE ch.case_id = c.id AND ch.para_no IS NOT NULL)
            """,
            (court, MIN_CITED_BY.get(court, 0)),
        ).fetchall()
        # Draw spares: some decisions will be judged unsuitable.
        for row in rng.sample(rows, min(len(rows), n * 2)):
            picked.append({"id": row[0], "citation": row[1], "name": row[2], "court": row[3],
                           "cited_by": row[4], "want": n})
    return picked


def leaks(scenario: str, decision: dict) -> list[str]:
    """Surnames/party names or citations that would make the scenario a keyword giveaway."""
    found = [c for c in case_citations(scenario)]
    generic = {"canada", "attorney", "general", "minister", "citizenship", "immigration", "queen", "king",
               "the", "and", "inc", "ltd", "employment", "social", "development", "commission", "insurance",
               "revenue", "national", "public", "safety", "emergency", "preparedness", "treasury", "board",
               "refugees", "canadian", "agency", "service", "services", "union", "council", "government"}
    for token in re.findall(r"[A-Za-z][A-Za-z'\-]{3,}", decision["name"] or ""):
        if token.lower() not in generic and re.search(rf"\b{re.escape(token)}\b", scenario, re.IGNORECASE):
            found.append(token)
    return found


def draft(decision: dict) -> dict:
    with connect() as conn:
        doc = load_document(conn, decision["id"])
    try:
        result = get_backend(settings.filac_backend).extract(
            system=SYSTEM_PROMPT, instruction=INSTRUCTION, document=doc.render(), schema=SCHEMA,
            model=settings.filac_model, effort=settings.filac_effort,
        )
    except LLMError as exc:
        return {**decision, "error": str(exc)}
    return {**decision, "draft": result.data, "anchors": set(doc.anchors)}


SCENARIOS_FILE = Path(__file__).resolve().parent.parent / "eval" / "scenarios.yaml"
MIN_STATUTE_MENTIONS = 2
MAX_STATUTE_GOLD = 3


def add_statute_gold() -> None:
    """Give drafted scenarios statute gold: the sections their source decision cites most.

    Edits eval/scenarios.yaml as text so its comments survive. Skips scenarios that already
    have statute gold.
    """
    from collections import Counter

    from app.statute_refs import StatuteIndex

    data = yaml.safe_load(SCENARIOS_FILE.read_text(encoding="utf-8"))
    lines = SCENARIOS_FILE.read_text(encoding="utf-8").split("\n")
    with connect() as conn:
        index = StatuteIndex.from_db(conn)
        titles = dict(conn.execute("SELECT code, title FROM legislation").fetchall())
        for scenario in data["scenarios"]:
            if scenario.get("source") != "drafted-from-decision":
                continue
            if any(a["kind"] in ("statute", "regulation") for a in scenario["expected_authorities"]):
                continue
            court = scenario["expected_authorities"][0].get("court")
            row = conn.execute(
                "SELECT full_text FROM cases WHERE citation = %s AND court = %s", (scenario["drafted_from"], court)
            ).fetchone()
            counts = Counter((r.code, r.section_no) for r in index.extract(row[0] or "")) if row else Counter()
            chosen = [key for key, n in counts.most_common(MAX_STATUTE_GOLD) if n >= MIN_STATUTE_MENTIONS]
            if not chosen:
                print(f"  {scenario['id']}: no section cited {MIN_STATUTE_MENTIONS}+ times")
                continue

            start = lines.index(f"  - id: {scenario['id']}")
            insert_at = next(i for i in range(start, len(lines)) if lines[i].strip().startswith("citations_resolved:"))
            entries = []
            for code, section in chosen:
                entries += [
                    f"    - citation: \"{titles[code]}, s {section}\"",
                    "      kind: statute",
                    f"      code: {code}",
                    f"      section: '{section}'",
                    "      relevance: supporting",
                    "      source: extracted-from-decision",
                ]
            lines[insert_at:insert_at] = entries
            print(f"  {scenario['id']}: {', '.join(f'{titles[c]} s {s} ({counts[(c, s)]}x)' for c, s in chosen)}")
    SCENARIOS_FILE.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="write drafted scenarios here")
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--add-statute-gold", action="store_true",
                        help="add statute gold to drafted scenarios in eval/scenarios.yaml instead of drafting")
    args = parser.parse_args()

    if args.add_statute_gold:
        add_statute_gold()
        return
    if not args.out:
        parser.error("--out is required when drafting")

    with connect() as conn:
        candidates = pick_decisions(conn, args.seed)
        resolver = {}
        for case_id, citation, citation2, court in conn.execute("SELECT id, citation, citation2, court FROM cases"):
            for c in (citation, citation2):
                if c:
                    resolver.setdefault(c, []).append((citation, court))

    kept: dict[str, list[dict]] = {}
    remaining = list(candidates)
    while remaining:
        batch = []
        for decision in remaining:
            if len(kept.get(decision["court"], [])) + sum(b["court"] == decision["court"] for b in batch) < decision["want"]:
                batch.append(decision)
        remaining = [d for d in remaining if d not in batch]
        if not batch:
            break
        with ThreadPoolExecutor(args.workers) as pool:
            for outcome in pool.map(draft, batch):
                label = f"{outcome['citation']} [{outcome['court']}]"
                if "error" in outcome:
                    print(f"  failed    {label}: {outcome['error'][:120]}")
                    continue
                d = outcome["draft"]
                problems = leaks(d["scenario"], outcome)
                if not d["suitable"] or problems:
                    reason = d["unsuitable_reason"] if not d["suitable"] else f"leaks {problems}"
                    print(f"  rejected  {label}: {reason[:120]}")
                    continue
                kept.setdefault(outcome["court"], []).append(outcome)
                print(f"  kept      {label}: {d['legal_question'][:100]}")

    scenarios = []
    for court, outcomes in kept.items():
        for i, outcome in enumerate(outcomes, 1):
            d = outcome["draft"]
            gold = [{"citation": outcome["citation"], "name": outcome["name"], "kind": "case",
                     "relevance": "primary", "court": court}]
            seen = {outcome["citation"]}
            for authority in d["authorities_applied"]:
                for citation in case_citations(authority["citation"]):
                    matches = resolver.get(canonical(citation), [])
                    if len(matches) == 1 and matches[0][0] not in seen:
                        seen.add(matches[0][0])
                        gold.append({"citation": matches[0][0], "kind": "case", "relevance": "supporting",
                                     "court": matches[0][1]})
            scenarios.append({
                "id": f"lc-{court.lower()}-{i:02d}",
                "phase": 1,
                "jurisdiction": "federal",
                "area": d["area"],
                "source": "drafted-from-decision",
                "drafted_from": outcome["citation"],
                "scenario": d["scenario"],
                "legal_question": d["legal_question"],
                "expected_authorities": gold,
                "citations_resolved": True,
                "verified": False,
            })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml.safe_dump({"scenarios": scenarios}, sort_keys=False, allow_unicode=True, width=100),
                        encoding="utf-8")
    print(f"\nwrote {len(scenarios)} scenarios to {args.out}")


if __name__ == "__main__":
    main()
