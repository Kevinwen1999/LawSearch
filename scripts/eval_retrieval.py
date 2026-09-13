"""Score case retrieval against eval/scenarios.yaml (implementation-plan.md §6).

    python -m scripts.eval_retrieval                  # lexical, vector, hybrid side by side
    python -m scripts.eval_retrieval --mode hybrid -v # per-scenario ranks
    python -m scripts.eval_retrieval --out eval/runs/phase2.json

Only scenarios whose case citations resolved against the corpus are scored, and only
authorities not flagged in_corpus: false. Gains for nDCG: primary 2, supporting 1.
"""

import argparse
import json
import math
from datetime import datetime
from pathlib import Path

import yaml

from app.db import connect
from app.retrieval import prepare_session, search

SCENARIOS = Path(__file__).resolve().parent.parent / "eval" / "scenarios.yaml"
GAIN = {"primary": 2, "supporting": 1}
DEPTH = 50


def load_scenarios(max_phase: int) -> list[dict]:
    data = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    scored = []
    for s in data["scenarios"]:
        if s["phase"] > max_phase or not s.get("citations_resolved"):
            continue
        gold = [
            a for a in s["expected_authorities"]
            if a["kind"] == "case" and a.get("in_corpus", True)
        ]
        if gold:
            scored.append({**s, "gold": gold})
    return scored


def score_scenario(gold: list[dict], results: list) -> dict:
    ranks = {}
    for authority in gold:
        cit = authority["citation"]
        ranks[cit] = next(
            (i for i, c in enumerate(results, 1) if cit in (c.citation, c.citation2)), None
        )

    def recall(k: int) -> float:
        return sum(1 for r in ranks.values() if r and r <= k) / len(gold)

    first = min((r for r in ranks.values() if r), default=None)
    dcg = sum(
        GAIN[a["relevance"]] / math.log2(ranks[a["citation"]] + 1)
        for a in gold
        if ranks[a["citation"]] and ranks[a["citation"]] <= 10
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", nargs="+", choices=["lexical", "vector", "hybrid"],
                        default=["lexical", "vector", "hybrid"])
    parser.add_argument("--max-phase", type=int, default=1, help="include scenarios up to this phase")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--out", type=Path, help="write per-scenario results as JSON")
    args = parser.parse_args()

    scenarios = load_scenarios(args.max_phase)
    print(f"scoring {len(scenarios)} scenarios, depth {DEPTH}\n")

    report = {"run_at": datetime.now().isoformat(timespec="seconds"), "modes": {}}
    with connect() as conn:
        prepare_session(conn)
        for mode in args.mode:
            rows = []
            for s in scenarios:
                result = search(conn, s["scenario"], k=DEPTH, mode=mode)
                rows.append({"id": s["id"], **score_scenario(s["gold"], result.cases)})
            summary = {
                metric: sum(r[metric] for r in rows) / len(rows)
                for metric in ("recall@10", "recall@50", "mrr", "ndcg@10")
            }
            report["modes"][mode] = {"summary": summary, "scenarios": rows}

    metrics = ("recall@10", "recall@50", "mrr", "ndcg@10")
    print(f"{'mode':8} " + " ".join(f"{m:>10}" for m in metrics))
    for mode, data in report["modes"].items():
        print(f"{mode:8} " + " ".join(f"{data['summary'][m]:10.3f}" for m in metrics))

    if args.verbose:
        for mode, data in report["modes"].items():
            print(f"\n--- {mode}: rank of each gold authority (- = not in top {DEPTH}) ---")
            for row in data["scenarios"]:
                ranks = ", ".join(f"{c} @{r or '-'}" for c, r in row["ranks"].items())
                print(f"  {row['id']:13} {ranks}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
