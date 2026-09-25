"""Score the /scenarios pipeline (fingerprint -> per-issue searches -> merge) on eval/scenarios.yaml.

    python -m scripts.eval_scenarios                  # fingerprint (cached), search, compare merges
    python -m scripts.eval_scenarios --reuse          # re-score cached search results only
    python -m scripts.eval_scenarios -v --out eval/runs/scenarios.json

scripts/eval_retrieval.py scores one retrieval.search() over the raw scenario text. This scores
what the /scenarios endpoint does: fingerprint the scenario, search the combined query plus one
query per issue, then merge. Every merge strategy is scored on the same search results.

Fingerprints are cached in eval/fingerprints/<id>.json, keyed by prompt version and scenario
text, so reruns are reproducible and need no LLM. Scenarios the jurisdiction gate stops are
reported and skipped. Search results are pickled under data/ for --reuse.

Metrics are over the flat, de-duplicated case list the endpoint returns. `issues` is the share
of a scenario's issues (from gold `issues` tags) with at least one gold case shown.
"""

import argparse
import dataclasses
import hashlib
import json
import math
import pickle
import time
from datetime import datetime
from pathlib import Path

from app import fingerprint
from app.config import settings
from app.db import connect
from app.retrieval import group_issue_results, merge_issue_results, prepare_session, run_scenario_queries
from app.statute_refs import statute_index
from scripts.eval_retrieval import GAIN, load_scenarios, score_sections, statute_gold, _matches

FINGERPRINTS = Path(__file__).resolve().parent.parent / "eval" / "fingerprints"
DEPTH = 30          # per-query search depth, enough for every strategy below
K_SECTIONS = 8

STRATEGIES = {
    "interleave k=10 (before)": lambda r, issues, named: merge_issue_results(r, 10, K_SECTIONS, named)[0],
    "interleave k=25": lambda r, issues, named: merge_issue_results(r, 25, K_SECTIONS, named)[0],
    **{
        f"grouped {lead}+{per}/issue": (
            lambda r, issues, named, lead=lead, per=per: group_issue_results(r, issues, lead, per, K_SECTIONS, named)[1]
        )
        for lead, per in [(8, 3), (10, 2), (10, 3), (10, 5)]
    },
}
METRICS = ("shown", "recall@10", "recall@25", "recall", "ndcg@10", "issues")


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


def gather(scenarios: list[dict]) -> dict:
    gathered = {}
    with connect() as conn:
        prepare_session(conn)
        index = statute_index(conn)
        for n, s in enumerate(scenarios, 1):
            started = time.monotonic()
            fp = cached_fingerprint(s)
            gate = fingerprint.check_jurisdiction(fp)
            if gate.status != "ok":
                gathered[s["id"]] = {"skipped": gate.status}
                print(f"  [{n}/{len(scenarios)}] {s['id']}: skipped ({gate.status})", flush=True)
                continue
            scope = fingerprint.section_scope(fp, index)
            issues = fingerprint.issue_queries(fp)
            results = run_scenario_queries(
                conn, fingerprint.search_query(fp), issues, k=DEPTH, k_sections=K_SECTIONS,
                courts=fingerprint.case_courts(fp), section_scope=scope,
            )
            gathered[s["id"]] = {
                "issues": issues, "results": results,
                "named": set(scope.named_codes) if scope else set(),
            }
            print(f"  [{n}/{len(scenarios)}] {s['id']}: {len(issues)} issues, {time.monotonic() - started:.0f}s", flush=True)
    return gathered


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
    parser.add_argument("-v", "--verbose", action="store_true", help="per-scenario gold ranks")
    parser.add_argument("--out", type=Path, help="write per-scenario results as JSON")
    args = parser.parse_args()

    scenarios = [s for s in load_scenarios(max_phase=99) if not args.only or s["id"] in args.only]
    cache = Path(settings.data_dir) / "eval_scenarios_results.pkl"
    if args.reuse:
        gathered = pickle.loads(cache.read_bytes())
        scenarios = [s for s in scenarios if s["id"] in gathered]
    else:
        print(f"searching {len(scenarios)} scenarios...")
        gathered = gather(scenarios)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(gathered))

    rows: dict[str, list[dict]] = {name: [] for name in STRATEGIES}
    section_rows = []
    for s in scenarios:
        g = gathered[s["id"]]
        if "skipped" in g:
            continue
        for name, strategy in STRATEGIES.items():
            rows[name].append({"id": s["id"], "split": s["split"], **score(s, strategy(g["results"], g["issues"], g["named"]))})
        if gold := statute_gold(s):
            sections = group_issue_results(g["results"], g["issues"], 1, 1, K_SECTIONS, g["named"])[2]
            section_rows.append({"id": s["id"], "split": s["split"], **score_sections(gold, sections)})

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
        subset = [r for r in section_rows if r["split"] == split]
        if subset:
            print(f"\n== sections (unchanged by case merge), {split}: n={len(subset)} "
                  f"recall@5 {sum(r['recall@5'] for r in subset) / len(subset):.3f} "
                  f"recall@10 {sum(r['recall@10'] for r in subset) / len(subset):.3f}")

    if args.verbose:
        for name in ("interleave k=10 (before)", "grouped 10+3/issue"):
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
            "sections": section_rows,
            "skipped": skipped,
        }, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
