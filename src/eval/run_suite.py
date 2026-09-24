"""CLI entrypoint: runs the full scenario
suite across the three required ablation configurations, writes
results/runs.jsonl (the "full" config) plus
results/runs_<mode>.jsonl for each config, computes results/metrics_summary.json,
and prints the ablation comparison table.

    python -m src.eval.run_suite
    python -m src.eval.run_suite --modes full no_revalidation
    python -m src.eval.run_suite --scenarios s001 s002 --wait-seconds 0.1
    python -m src.eval.run_suite --resume   # after a crash: skip scenarios
                                             # already written per mode

Uses a real Postgres checkpointer for "full"/"no_revalidation" when
DATABASE_URL is set (docker-compose up), and falls back to InMemorySaver
otherwise so the suite still runs end-to-end without it — durability
itself isn't being tested in that fallback, only what the ablation flags
change about graph behavior. Uses AnthropicDiagnosisReasoner/
AnthropicRiskClassifier when ANTHROPIC_API_KEY is set, else OpenAI when
OPEN_AI_API_KEY is set, else Ollama when OLLAMA_MODEL is set, else the
heuristic fallback in src/eval/runner.py;
the summary records which was used.

Each scenario's record is flushed to results/runs_<mode>.jsonl (and
results/runs.jsonl for "full") as soon as it completes, with a one-line
progress print — not batched until the mode finishes. A single LLM call
against a local, CPU-bound model can take minutes, so a full suite run can
take hours; without incremental writes, one crash near the end loses
every record from that mode, which is what happened before this was
added. --resume reads what's already on disk per mode and only runs the
remaining scenarios, appending rather than overwriting.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from langgraph.checkpoint.memory import InMemorySaver

from src.durability.checkpointer import postgres_checkpointer, run_async
from src.eval import metrics as metrics_mod
from src.eval.runner import DEFAULT_OPENAI_MODEL, AblationMode, run_scenario

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


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _mode_output_paths(mode: AblationMode) -> list[Path]:
    paths = [RESULTS_DIR / f"runs_{mode}.jsonl"]
    if mode == "full":
        paths.append(RESULTS_DIR / "runs.jsonl")
    return paths


async def _run_mode(
    mode: AblationMode, scenarios: list[dict], wait_seconds: float, resume: bool
) -> list[dict]:
    out_paths = _mode_output_paths(mode)
    primary = out_paths[0]

    already_done = _read_jsonl(primary) if resume else []
    done_ids = {r["scenario_id"] for r in already_done}
    todo = [s for s in scenarios if s["id"] not in done_ids] if resume else scenarios

    records = list(already_done)
    write_mode = "a" if resume and already_done else "w"
    files = [p.open(write_mode, encoding="utf-8") for p in out_paths]
    try:
        for scenario in todo:
            t0 = time.perf_counter()
            async with _checkpointer_for(mode) as checkpointer:
                record = await run_scenario(
                    scenario, mode=mode, checkpointer=checkpointer, simulated_approval_wait_seconds=wait_seconds
                )
            records.append(record)
            line = json.dumps(record, default=str) + "\n"
            for f in files:
                f.write(line)
                f.flush()
            print(
                f"[{mode}] {len(records)}/{len(scenarios)} {scenario['id']} done in "
                f"{time.perf_counter() - t0:.1f}s",
                flush=True,
            )
    finally:
        for f in files:
            f.close()
    return records


async def run_suite(
    modes: list[AblationMode], scenario_ids: list[str] | None, wait_seconds: float, resume: bool = False
) -> dict[str, object]:
    scenarios = _load_scenarios()
    if scenario_ids:
        scenarios = [s for s in scenarios if s["id"] in scenario_ids]

    RESULTS_DIR.mkdir(exist_ok=True)
    results_by_mode: dict[str, list[dict]] = {}
    for mode in modes:
        results_by_mode[mode] = await _run_mode(mode, scenarios, wait_seconds, resume)

    if os.environ.get("ANTHROPIC_API_KEY"):
        reasoner_used = "anthropic"
    elif os.environ.get("OPEN_AI_API_KEY"):
        reasoner_used = f"openai:{os.environ.get('OPENAI_MODEL', DEFAULT_OPENAI_MODEL)}"
    elif os.environ.get("OLLAMA_MODEL"):
        reasoner_used = f"ollama:{os.environ['OLLAMA_MODEL']}"
    else:
        reasoner_used = "heuristic-fallback"

    summary: dict[str, object] = {
        "generated_with": {
            "reasoner": reasoner_used,
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
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip scenarios already present in results/runs_<mode>.jsonl per mode and append the rest, "
        "instead of overwriting from scratch.",
    )
    args = parser.parse_args()
    run_async(run_suite(args.modes, args.scenarios, args.wait_seconds, args.resume))


if __name__ == "__main__":
    main()
