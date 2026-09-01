"""CLI entrypoint (PROJECT_SPEC.md section 11): runs the full scenario
suite across the three required ablation configurations, writes
results/runs.jsonl (the "full" config, per the section-11 schema) plus
results/runs_<mode>.jsonl for each config, computes results/metrics_summary.json,
and prints the ablation comparison table.

    python -m src.eval.run_suite
    python -m src.eval.run_suite --modes full no_revalidation
    python -m src.eval.run_suite --scenarios s001 s002 --wait-seconds 0.1

Uses a real Postgres checkpointer for "full"/"no_revalidation" when
DATABASE_URL is set (docker-compose up), and falls back to InMemorySaver
otherwise so the suite still runs end-to-end without it — durability
itself isn't being tested in that fallback, only what the ablation flags
change about graph behavior. Uses AnthropicDiagnosisReasoner/
AnthropicRiskClassifier when ANTHROPIC_API_KEY is set, else the heuristic
fallback in src/eval/runner.py; the summary records which was used.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from langgraph.checkpoint.memory import InMemorySaver

from src.durability.checkpointer import postgres_checkpointer, run_async
from src.eval import metrics as metrics_mod
from src.eval.runner import AblationMode, run_scenario

RESULTS_DIR = Path(__file__).parent.parent.parent / "results"
SCENARIOS_PATH = Path(__file__).parent.parent.parent / "data" / "scenarios.json"

ALL_MODES: tuple[AblationMode, ...] = ("full", "no_durability", "no_revalidation")


def _load_scenarios() -> list[dict]:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


@asynccontextmanager
async def _checkpointer_for(mode: AblationMode) -> AsyncIterator[object]:
    dsn = os.environ.get("DATABASE_URL")
    if mode != "no_durability" and dsn:
        async with postgres_checkpointer(dsn) as checkpointer:
            yield checkpointer
    else:
        yield InMemorySaver()


async def _run_mode(mode: AblationMode, scenarios: list[dict], wait_seconds: float) -> list[dict]:
    records = []
    for scenario in scenarios:
        async with _checkpointer_for(mode) as checkpointer:
            record = await run_scenario(
                scenario, mode=mode, checkpointer=checkpointer, simulated_approval_wait_seconds=wait_seconds
            )
        records.append(record)
    return records


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, default=str) + "\n")


async def run_suite(
    modes: list[AblationMode], scenario_ids: list[str] | None, wait_seconds: float
) -> dict[str, object]:
    scenarios = _load_scenarios()
    if scenario_ids:
        scenarios = [s for s in scenarios if s["id"] in scenario_ids]

    RESULTS_DIR.mkdir(exist_ok=True)
    results_by_mode: dict[str, list[dict]] = {}
    for mode in modes:
        records = await _run_mode(mode, scenarios, wait_seconds)
        results_by_mode[mode] = records
        _write_jsonl(RESULTS_DIR / f"runs_{mode}.jsonl", records)
        if mode == "full":
            _write_jsonl(RESULTS_DIR / "runs.jsonl", records)

    summary: dict[str, object] = {
        "generated_with": {
            "reasoner": "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "heuristic-fallback",
            "checkpointer": "postgres" if os.environ.get("DATABASE_URL") else "in-memory-fallback",
            "scenario_count": len(scenarios),
            "modes_run": modes,
        },
        "by_mode": {mode: metrics_mod.compute_all_metrics(records, scenarios) for mode, records in results_by_mode.items()},
        "staleness_incorrect_execution_count": {
            mode: metrics_mod.staleness_incorrect_execution_count(records, scenarios)
            for mode, records in results_by_mode.items()
        },
    }
    (RESULTS_DIR / "metrics_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    if len(results_by_mode) > 1:
        print(metrics_mod.ablation_comparison_table(results_by_mode, scenarios))

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modes", nargs="+", choices=ALL_MODES, default=list(ALL_MODES))
    parser.add_argument("--scenarios", nargs="+", default=None, help="Scenario ids to run; default is all 50.")
    parser.add_argument(
        "--wait-seconds", type=float, default=2.0, help="Simulated approval-wait sleep per suspension."
    )
    args = parser.parse_args()
    run_async(run_suite(args.modes, args.scenarios, args.wait_seconds))


if __name__ == "__main__":
    main()
