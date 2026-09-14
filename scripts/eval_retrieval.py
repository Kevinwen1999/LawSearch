"""Score case retrieval against eval/scenarios.yaml (implementation-plan.md §6).

    python -m scripts.eval_retrieval                 # compare pipelines on tune and test splits
    python -m scripts.eval_retrieval --tune          # grid-search RankingConfig on the tune split
    python -m scripts.eval_retrieval -v --out eval/runs/phase4.json

Candidates are gathered once per scenario (database + GPU); every ranking config is then
scored on the same candidates, so comparisons are exact and tuning is cheap.

Scenarios are split into `tune` and `test`. Choose settings on tune; report test. Results
are also broken out by `group`: scenarios whose gold answers are Supreme Court cases vs
those drafted from lower-court and tribunal decisions, so a change that helps one by
burying the other is visible. Gains for nDCG: primary 2, supporting 1.
"""

import argparse
import itertools
import json
import math
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

import yaml

from app.db import connect
from app.retrieval import (
    DEFAULT_CONFIG,
    PHASE2_CONFIG,
    SECTION_SEED_CASES,
    RankingConfig,
    gather,
    prepare_session,
    rank,
)
from app.section_search import SectionConfig, gather_sections, rank_sections

SCENARIOS = Path(__file__).resolve().parent.parent / "eval" / "scenarios.yaml"
GAIN = {"primary": 2, "supporting": 1}
DEPTH = 50
METRICS = ("recall@10", "recall@50", "mrr", "ndcg@10")

PIPELINES = {
    "hybrid (phase 2)": PHASE2_CONFIG,
    "+ graph": replace(PHASE2_CONFIG, graph_weight=1.0),
    "+ priors": replace(PHASE2_CONFIG, court_weight=0.004, citation_weight=0.004),
    "+ rerank": replace(PHASE2_CONFIG, rerank_weight=1.0),
    "default (phase 4)": DEFAULT_CONFIG,
}

SECTION_PIPELINES = {
    "text only": SectionConfig(graph_weight=0.0, citation_weight=0.0),
    "+ cited by top cases": SectionConfig(citation_weight=0.0),
    "default": SectionConfig(),
}
SECTION_METRICS = ("recall@5", "recall@10", "mrr")

GRID = {
    "graph_weight": [0.0, 0.5, 1.0, 2.0],
    "seed_cases": [10, 20, 30],
    "court_weight": [0.0, 0.002, 0.004, 0.008],
    "citation_weight": [0.0, 0.002, 0.004, 0.008],
    "rerank_weight": [0.0, 0.5, 1.0, 2.0, 4.0],
    "rerank_depth": [20, 40],
}


def load_scenarios(max_phase: int) -> list[dict]:
    data = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    scored = []
    for s in data["scenarios"]:
        if s["phase"] > max_phase or not s.get("citations_resolved"):
            continue
        gold = [a for a in s["expected_authorities"] if a["kind"] == "case" and a.get("in_corpus", True)]
        if gold:
            group = "lower-court" if s.get("source") == "drafted-from-decision" else "scc-gold"
            scored.append({**s, "gold": gold, "group": group, "split": s.get("split", "tune")})
    return scored


def _matches(authority: dict, result) -> bool:
    if authority.get("court") and authority["court"] != result.court:
        return False
    return authority["citation"] in (result.citation, result.citation2)


def score_scenario(gold: list[dict], results: list) -> dict:
    ranks = {
        a["citation"]: next((i for i, r in enumerate(results, 1) if _matches(a, r)), None) for a in gold
    }

    def recall(k: int) -> float:
        return sum(1 for r in ranks.values() if r and r <= k) / len(gold)

    first = min((r for r in ranks.values() if r), default=None)
    dcg = sum(
        GAIN[a["relevance"]] / math.log2(ranks[a["citation"]] + 1)
        for a in gold if ranks[a["citation"]] and ranks[a["citation"]] <= 10
    )
    ideal = sorted((GAIN[a["relevance"]] for a in gold), reverse=True)[:10]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return {
        "ranks": ranks,
        "recall@10": recall(10),
        "recall@50": recall(DEPTH),
        "mrr": 1 / first if first else 0.0,
        "ndcg@10": dcg / idcg if idcg else 0.0,
    }


def statute_gold(scenario: dict) -> list[dict]:
    return [a for a in scenario["expected_authorities"] if a["kind"] in ("statute", "regulation") and a.get("code")]


def score_sections(gold: list[dict], results: list) -> dict:
    ranks = {
        f"{a['code']} s. {a['section']}": next(
            (i for i, r in enumerate(results, 1) if r.code == a["code"] and r.section_no == str(a["section"])), None
        )
        for a in gold
    }
    first = min((r for r in ranks.values() if r), default=None)
    return {
        "ranks": ranks,
        "recall@5": sum(1 for r in ranks.values() if r and r <= 5) / len(gold),
        "recall@10": sum(1 for r in ranks.values() if r and r <= 10) / len(gold),
        "mrr": 1 / first if first else 0.0,
    }


def print_section_table(named_rows: dict[str, list[dict]], split: str) -> None:
    print(f"\n== statute sections, {split} split ==")
    print(f"  {'pipeline':22} {'n':>3} " + " ".join(f"{m:>10}" for m in SECTION_METRICS))
    for name, rows in named_rows.items():
        subset = [r for r in rows if r["split"] == split]
        if subset:
            print(f"  {name:22} {len(subset):3d} "
                  + " ".join(f"{sum(r[m] for r in subset) / len(subset):10.3f}" for m in SECTION_METRICS))


def evaluate(scenarios: list[dict], gathered: dict, config: RankingConfig) -> list[dict]:
    rows = []
    for s in scenarios:
        candidates = gathered[s["id"]]
        results = [candidates.meta[h.case_id] for h in rank(candidates, config)[:DEPTH]]
        rows.append({"id": s["id"], "split": s["split"], "group": s["group"], **score_scenario(s["gold"], results)})
    return rows


def summarize(rows: list[dict], **where) -> dict:
    subset = [r for r in rows if all(r[k] == v for k, v in where.items())]
    if not subset:
        return {}
    return {"n": len(subset), **{m: sum(r[m] for r in subset) / len(subset) for m in METRICS}}


def print_table(title: str, named_rows: dict[str, list[dict]], **where) -> None:
    print(f"\n{title}")
    print(f"  {'pipeline':22} {'n':>3} " + " ".join(f"{m:>10}" for m in METRICS))
    for name, rows in named_rows.items():
        s = summarize(rows, **where)
        if s:
            print(f"  {name:22} {s['n']:3d} " + " ".join(f"{s[m]:10.3f}" for m in METRICS))


NEAR_TIE = 0.01


def _weight_size(config: RankingConfig) -> float:
    # Priors live on the RRF score scale (~1/60), so rescale them before comparing.
    return config.graph_weight + config.rerank_weight + 100 * (config.court_weight + config.citation_weight)


def tune(scenarios: list[dict], gathered: dict) -> RankingConfig:
    """Pick weights on the tune split only, conservatively.

    With ~17 tune scenarios the single best config overfits, so the rule is:
    1. never lower either group's tune nDCG@10 below the phase 2 pipeline;
    2. among configs within NEAR_TIE of the best eligible nDCG@10, take the smallest weights.
    """
    groups = sorted({s["group"] for s in scenarios})
    baseline = evaluate(scenarios, gathered, PHASE2_CONFIG)
    floor = {g: summarize(baseline, split="tune", group=g)["ndcg@10"] for g in groups}

    keys = list(GRID)
    configs = [RankingConfig(**dict(zip(keys, values))) for values in itertools.product(*GRID.values())]
    started = time.monotonic()
    eligible = []
    for config in configs:
        rows = evaluate(scenarios, gathered, config)
        if all(summarize(rows, split="tune", group=g)["ndcg@10"] >= floor[g] for g in groups):
            eligible.append((summarize(rows, split="tune")["ndcg@10"], config))
    print(f"\ngrid: {len(configs)} configs, {len(eligible)} keep both groups at or above phase 2 on tune "
          f"({time.monotonic() - started:.0f}s)")

    best = max(ndcg for ndcg, _ in eligible)
    near = sorted((c for ndcg, c in eligible if ndcg >= best - NEAR_TIE), key=_weight_size)
    chosen = near[0]
    print(f"best eligible tune nDCG@10 {best:.3f}; {len(near)} configs within {NEAR_TIE}; "
          f"choosing the smallest weights: {asdict(chosen)}")
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-phase", type=int, default=1, help="include scenarios up to this phase")
    parser.add_argument("--tune", action="store_true", help="grid-search ranking weights on the tune split")
    parser.add_argument("-v", "--verbose", action="store_true", help="per-scenario gold ranks")
    parser.add_argument("--out", type=Path, help="write per-scenario results as JSON")
    args = parser.parse_args()

    scenarios = load_scenarios(args.max_phase)
    splits = {sp: sum(s["split"] == sp for s in scenarios) for sp in ("tune", "test")}
    print(f"scoring {len(scenarios)} scenarios ({splits}), depth {DEPTH}")

    started = time.monotonic()
    gathered, lexical_only, vector_only, section_candidates = {}, {}, {}, {}
    with connect() as conn:
        prepare_session(conn)
        for s in scenarios:
            gathered[s["id"]] = gather(conn, s["scenario"], mode="hybrid")
            lexical_only[s["id"]] = gather(conn, s["scenario"], mode="lexical")
            vector_only[s["id"]] = gather(conn, s["scenario"], mode="vector")
            if statute_gold(s):
                seeds = [h.case_id for h in rank(gathered[s["id"]], DEFAULT_CONFIG)[:SECTION_SEED_CASES]]
                section_candidates[s["id"]] = gather_sections(
                    conn, s["scenario"], gathered[s["id"]].query_vector, seeds
                )
    print(f"gathered candidates in {time.monotonic() - started:.0f}s")

    section_rows = {
        name: [
            {"id": s["id"], "split": s["split"],
             **score_sections(statute_gold(s), rank_sections(section_candidates[s["id"]], config)[:DEPTH])}
            for s in scenarios if s["id"] in section_candidates
        ]
        for name, config in SECTION_PIPELINES.items()
    }

    named = {
        "lexical only": evaluate(scenarios, lexical_only, PHASE2_CONFIG),
        "vector only": evaluate(scenarios, vector_only, PHASE2_CONFIG),
        **{name: evaluate(scenarios, gathered, config) for name, config in PIPELINES.items()},
    }

    if args.tune:
        best = tune(scenarios, gathered)
        named["tuned (best on tune)"] = evaluate(scenarios, gathered, best)
        print(f"\nbest config: {best}")

    for split in ("tune", "test"):
        print_table(f"== {split} split ==", named, split=split)
    for group in ("scc-gold", "lower-court"):
        print_table(f"== all scenarios, group {group} ==", named, group=group)
    if section_candidates:
        for split in ("tune", "test"):
            print_section_table(section_rows, split)

    if args.verbose:
        for name in ("hybrid (phase 2)", "default (phase 4)"):
            print(f"\n--- {name}: rank of each gold authority (- = not in top {DEPTH}) ---")
            for row in named[name]:
                ranks = ", ".join(f"{c} @{r or '-'}" for c, r in row["ranks"].items())
                print(f"  {row['id']:14} [{row['split']}] {ranks}")
        if section_candidates:
            print("\n--- statute sections (default): rank of each gold section ---")
            for row in section_rows["default"]:
                ranks = ", ".join(f"{c} @{r or '-'}" for c, r in row["ranks"].items())
                print(f"  {row['id']:14} [{row['split']}] {ranks}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "run_at": datetime.now().isoformat(timespec="seconds"),
            "pipelines": {name: {"tune": summarize(rows, split="tune"), "test": summarize(rows, split="test"),
                                 "scenarios": rows} for name, rows in named.items()},
            "section_pipelines": section_rows,
        }
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
