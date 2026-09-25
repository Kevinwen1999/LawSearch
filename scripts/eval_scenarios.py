"""Score the /scenarios pipeline (fingerprint -> per-issue searches -> merge) on eval/scenarios.yaml.

    python -m scripts.eval_scenarios                  # fingerprint (cached), search, compare merges
    python -m scripts.eval_scenarios --reuse          # re-score cached search results only
    python -m scripts.eval_scenarios -v --out eval/runs/scenarios.json
    python -m scripts.eval_scenarios --fingerprints-only             # just fill eval/fingerprints/
    python -m scripts.eval_scenarios --ignore-gate --canlii          # score gated scenarios too; check
                                                                      # not-in-corpus gold gets flagged

scripts/eval_retrieval.py scores one retrieval.search() over the raw scenario text. This scores
what the /scenarios endpoint does: fingerprint the scenario, search the scenario text, the combined
query and one query per issue, then merge. Every merge strategy is scored on the same search results.

Fingerprints are cached in eval/fingerprints/<id>.json, keyed by prompt version and scenario
text, so reruns are reproducible and need no LLM. Scenarios the jurisdiction gate stops are
reported and skipped (unless --ignore-gate). Search results are pickled under data/ for --reuse.

Metrics are over the flat, de-duplicated case list the endpoint returns. `issues` is the share
of a scenario's issues (from gold `issues` tags) with at least one gold case shown. Legislation is
scored per strategy too, with `uncited` = sections shown that no decision cites.
"""

import argparse
import dataclasses
import hashlib
import json
import math
import pickle
import re
import time
from datetime import datetime
from pathlib import Path

from app import fingerprint, retrieval
from app.config import settings
from app.db import connect
from app.retrieval import group_issue_results, merge_issue_results, prepare_session, run_scenario_queries
from app.statute_refs import statute_index
from scripts.eval_retrieval import GAIN, load_scenarios, score_sections, statute_gold, _matches

FINGERPRINTS = Path(__file__).resolve().parent.parent / "eval" / "fingerprints"
DEPTH = 30          # per-query search depth, enough for every strategy below
K_SECTIONS = 8


def _with_constants(overrides: dict, fn):
    """Run fn() with app.retrieval module constants temporarily overridden."""
    saved = {name: getattr(retrieval, name) for name in overrides}
    try:
        for name, value in overrides.items():
            setattr(retrieval, name, value)
        return fn()
    finally:
        for name, value in saved.items():
            setattr(retrieval, name, value)


def grouped(lead: int, per: int, **overrides):
    """group_issue_results over the fingerprint's queries only (no scenario-text search)."""
    def run(r, issues, named):
        return _with_constants(overrides, lambda: group_issue_results(r, issues, lead, per, K_SECTIONS, named)[1:])
    return run


def interleaved(k: int, **overrides):
    """merge_issue_results: the endpoint before grouping."""
    def run(r, issues, named):
        return _with_constants(overrides, lambda: merge_issue_results(r, k, K_SECTIONS, named))
    return run


# The settings before the 2026-09-24/25 changes, to measure against.
BEFORE = {"MIN_ISSUE_CASE_RERANK": 0.0, "DROP_UNCITED_SECTIONS": False}

# Each strategy returns (flat cases, flat sections), as the endpoint would.
STRATEGIES = {
    "interleave k=10 (before)": interleaved(10, **BEFORE),
    "grouped 10+3, old gates": grouped(10, 3, **BEFORE),
    "grouped 10+3, no raw text": grouped(10, 3),
}
# Need the raw-text search (gathered["raw"]); take (results, issues, named, raw). "current" is what
# search_scenario(scenario_text=...) does: the scenario text leads, the fingerprint query follows.
RAW_STRATEGIES = {
    "raw text only": lambda r, issues, named, raw: (raw.cases, raw.sections),
    "raw leads 3, 10+3": lambda r, issues, named, raw: group_issue_results(
        [raw, *r], [None, *issues], 10, 3, K_SECTIONS, named, lead=3)[1:],
    "current (raw leads 7, 10+3)": lambda r, issues, named, raw: group_issue_results(
        [raw, *r], [None, *issues], 10, 3, K_SECTIONS, named, lead=retrieval.SCENARIO_TEXT_LEAD)[1:],
}
METRICS = ("shown", "recall@10", "recall@25", "recall", "ndcg@10", "issues")
SECTION_METRICS = ("shown", "recall@5", "recall@10", "recall", "uncited")


def cached_fingerprint(scenario: dict) -> fingerprint.Fingerprint:
    text = scenario["scenario"].strip()
    key = {"prompt_version": fingerprint.PROMPT_VERSION, "scenario_sha1": hashlib.sha1(text.encode()).hexdigest()}
    path = FINGERPRINTS / f"{scenario['id']}.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if all(saved.get(k) == v for k, v in key.items()):
            return fingerprint.Fingerprint(**saved["fingerprint"])
    fp = fingerprint.generate(text)
    FINGERPRINTS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**key, "fingerprint": dataclasses.asdict(fp)}, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return fp


def score(scenario: dict, cases: list) -> dict:
    gold = scenario["gold"]
    ranks = {a["citation"]: next((i for i, c in enumerate(cases, 1) if _matches(a, c)), None) for a in gold}

    def recall(k: int | None) -> float:
        return sum(1 for r in ranks.values() if r and (k is None or r <= k)) / len(gold)

    dcg = sum(GAIN[a["relevance"]] / math.log2(ranks[a["citation"]] + 1)
              for a in gold if ranks[a["citation"]] and ranks[a["citation"]] <= 10)
    ideal = sorted((GAIN[a["relevance"]] for a in gold), reverse=True)[:10]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    tagged = {i for a in gold for i in a.get("issues", [])}
    covered = {i for a in gold if ranks[a["citation"]] for i in a.get("issues", [])}
    return {
        "ranks": ranks,
        "shown": len(cases),
        "recall@10": recall(10),
        "recall@25": recall(25),
        "recall": recall(None),
        "ndcg@10": dcg / idcg if idcg else 0.0,
        "issues": len(covered & tagged) / len(tagged) if tagged else None,
    }


def gather(scenarios: list[dict], ignore_gate: bool = False) -> dict:
    gathered = {}
    with connect() as conn:
        prepare_session(conn)
        index = statute_index(conn)
        for n, s in enumerate(scenarios, 1):
            started = time.monotonic()
            fp = cached_fingerprint(s)
            gate = fingerprint.check_jurisdiction(fp)
            if gate.status != "ok" and not ignore_gate:
                gathered[s["id"]] = {"skipped": gate.status}
                print(f"  [{n}/{len(scenarios)}] {s['id']}: skipped ({gate.status})", flush=True)
                continue
            scope = fingerprint.section_scope(fp, index)
            issues = fingerprint.issue_queries(fp)
            results = run_scenario_queries(
                conn, fingerprint.search_query(fp), issues, k=DEPTH, k_sections=K_SECTIONS,
                courts=fingerprint.case_courts(fp), section_scope=scope,
            )
            # The scenario's own words as one more query, as search_scenario(scenario_text=...) runs it.
            raw = retrieval.search(
                conn, " ".join(s["scenario"].split())[:retrieval.SCENARIO_QUERY_CHARS], k=DEPTH,
                k_sections=K_SECTIONS, courts=fingerprint.case_courts(fp), section_scope=scope, rerank_sections=True,
            )
            gathered[s["id"]] = {
                "issues": issues, "results": results, "raw": raw,
                "named": set(scope.named_codes) if scope else set(),
                "query": fingerprint.search_query(fp), "jurisdiction": fp.jurisdiction,
            }
            print(f"  [{n}/{len(scenarios)}] {s['id']}: {len(issues)} issues, {time.monotonic() - started:.0f}s", flush=True)
    return gathered


def _norm_citation(citation: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\(CanLII\)", "", citation)).strip().lower()


def _first_party(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower().split(" v")[0].split()[0]) if name.strip() else ""


def flagged_gold(scenario: dict, g: dict) -> dict | None:
    """For gold authorities not in the corpus (Bardal, ONSC decisions): does the scenario's
    CanLII layer surface them, via "frequently cited by the top cases" or, for Ontario scenarios,
    the ONSC/tribunal candidates? Seeds are chosen as the UI does: round-robin over issue groups."""
    from app import canlii, canlii_cited, canlii_detect
    from app.reranker import score_pairs

    gold = [a for a in scenario["expected_authorities"] if a["kind"] == "case" and not a.get("in_corpus", True)]
    if not gold:
        return None
    raw_results = [g["raw"], *g["results"]] if "raw" in g else g["results"]
    labels = [None, *g["issues"]] if "raw" in g else g["issues"]
    lead = retrieval.SCENARIO_TEXT_LEAD if "raw" in g else None
    groups, cases, _ = group_issue_results(raw_results, labels, 10, 3, K_SECTIONS, g["named"], lead)
    ordered: dict = {}
    for rank in range(max(len(x.cases) for x in groups)):
        for x in groups:
            if rank < len(x.cases):
                ordered.setdefault(x.cases[rank].case_id, x.cases[rank])
    seeds = [s for c in ordered.values() if (s := canlii_detect.to_seed(c.citation, c.court, c.style_of_cause))]
    client = canlii.default_client()
    cited = canlii_cited.find_cited(
        client, seeds, lambda a: canlii_cited.resolve_in_corpus(connect, a), exclude={c.case_id for c in cases}, k=20,
    )
    found = [(a.citation or "", a.title or "") for a in cited.authorities]
    if g["jurisdiction"] == "ontario":
        found += [(c.citation or "", c.title or "") for c in
                  canlii_detect.detect(client, g["query"], seeds, score_pairs, k=20).candidates]
    # CanLII may give only its own citation ("1991 CanLII 13517 (FCTAD)" for [1992] 1 FC 706), so a
    # gold authority also matches on its first party's name.
    hits = {
        a["citation"]: any(
            _norm_citation(a["citation"]) in _norm_citation(cit) or _first_party(a["name"]) == _first_party(title)
            for cit, title in found
        )
        for a in gold
    }
    return {"id": scenario["id"], "flagged": hits, "rate": sum(hits.values()) / len(hits), "queries": client.queries_sent}


def summarize(rows: list[dict]) -> dict:
    out = {"n": len(rows)}
    for m in METRICS:
        values = [r[m] for r in rows if r[m] is not None]
        out[m] = sum(values) / len(values) if values else None
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reuse", action="store_true", help="re-score the last pickled search results")
    parser.add_argument("--only", nargs="*", help="scenario ids to run")
    parser.add_argument("--fingerprints-only", action="store_true", help="fill eval/fingerprints/ and stop")
    parser.add_argument("--ignore-gate", action="store_true",
                        help="search scenarios the jurisdiction gate would stop, so coverage doesn't depend on it")
    parser.add_argument("--canlii", action="store_true",
                        help="also check not-in-corpus gold is flagged via CanLII (uses cached CanLII queries where it can)")
    parser.add_argument("-v", "--verbose", action="store_true", help="per-scenario gold ranks")
    parser.add_argument("--out", type=Path, help="write per-scenario results as JSON")
    args = parser.parse_args()

    scenarios = [s for s in load_scenarios(max_phase=99) if not args.only or s["id"] in args.only]
    cache = Path(settings.data_dir) / "eval_scenarios_results.pkl"
    if args.reuse:
        gathered = pickle.loads(cache.read_bytes())
        scenarios = [s for s in scenarios if s["id"] in gathered]
    elif args.fingerprints_only:
        for n, s in enumerate(scenarios, 1):
            started = time.monotonic()
            fp = cached_fingerprint(s)
            gate = fingerprint.check_jurisdiction(fp)
            print(f"  [{n}/{len(scenarios)}] {s['id']}: {fp.backend}/{fp.model}, {fp.jurisdiction}, "
                  f"{len(fp.issues)} issues, gate {gate.status}, {time.monotonic() - started:.0f}s", flush=True)
        return
    else:
        print(f"searching {len(scenarios)} scenarios...")
        gathered = gather(scenarios, args.ignore_gate)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(gathered))

    has_raw = all("raw" in g for g in gathered.values() if "skipped" not in g)
    strategies = {
        **{name: (lambda f: lambda g: f(g["results"], g["issues"], g["named"]))(f) for name, f in STRATEGIES.items()},
        **({name: (lambda f: lambda g: f(g["results"], g["issues"], g["named"], g["raw"]))(f)
            for name, f in RAW_STRATEGIES.items()} if has_raw else {}),
    }
    rows: dict[str, list[dict]] = {name: [] for name in strategies}
    section_rows: dict[str, list[dict]] = {name: [] for name in strategies}
    for s in scenarios:
        g = gathered[s["id"]]
        if "skipped" in g:
            continue
        for name, strategy in strategies.items():
            cases, sections = strategy(g)
            rows[name].append({"id": s["id"], "split": s["split"], **score(s, cases)})
            if gold := statute_gold(s):
                found = score_sections(gold, sections)
                found["recall"] = sum(1 for r in found["ranks"].values() if r) / len(gold)
                section_rows[name].append({
                    "id": s["id"], "split": s["split"], **found, "shown": len(sections),
                    "uncited": sum(1 for x in sections if not x.cited_by_count),
                })

    skipped = [f"{sid} ({g['skipped']})" for sid, g in gathered.items() if "skipped" in g]
    if skipped:
        print(f"\nskipped by the jurisdiction gate: {', '.join(skipped)}")
    for split in ("tune", "test"):
        print(f"\n== cases, {split} split ==")
        print(f"  {'merge':26} {'n':>3} " + " ".join(f"{m:>9}" for m in METRICS))
        for name, strategy_rows in rows.items():
            subset = summarize([r for r in strategy_rows if r["split"] == split])
            if subset["n"]:
                print(f"  {name:26} {subset['n']:3d} " + " ".join(
                    f"{subset[m]:9.3f}" if subset[m] is not None else f"{'-':>9}" for m in METRICS))
    for split in ("tune", "test"):
        print(f"\n== legislation, {split} split ==")
        print(f"  {'merge':26} {'n':>3} " + " ".join(f"{m:>9}" for m in SECTION_METRICS))
        for name, srows in section_rows.items():
            subset = [r for r in srows if r["split"] == split]
            if subset:
                print(f"  {name:26} {len(subset):3d} " + " ".join(
                    f"{sum(r[m] for r in subset) / len(subset):9.3f}" for m in SECTION_METRICS))

    flag_rows = []
    if args.canlii:
        for s in scenarios:
            g = gathered[s["id"]]
            if "skipped" not in g and (row := flagged_gold(s, g)):
                flag_rows.append(row)
                print(f"\nnot-in-corpus gold flagged, {s['id']}: {row['rate']:.2f} {row['flagged']} "
                      f"({row['queries']} CanLII queries)")

    if args.verbose:
        for name in ("interleave k=10 (before)", "current (raw leads 7, 10+3)"):
            if name not in rows:
                continue
            print(f"\n-- {name}: gold ranks --")
            for r in rows[name]:
                ranks = " ".join(f"{c}={v or '-'}" for c, v in r["ranks"].items())
                print(f"  {r['id']:10} {ranks}")

    if args.out:
        args.out.write_text(json.dumps({
            "generated": datetime.now().isoformat(timespec="seconds"),
            "fingerprint_prompt": fingerprint.PROMPT_VERSION,
            "strategies": {name: {"tune": summarize([r for r in rs if r["split"] == "tune"]),
                                  "test": summarize([r for r in rs if r["split"] == "test"]),
                                  "scenarios": rs} for name, rs in rows.items()},
            "sections": {name: {"tune": [r for r in rs if r["split"] == "tune"],
                                "test": [r for r in rs if r["split"] == "test"]} for name, rs in section_rows.items()},
            "skipped": skipped,
            "canlii_flagged": flag_rows,
        }, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
