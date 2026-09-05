"""Load test (PROJECT_SPEC.md section 13): start N workflows that all
reach AWAITING_APPROVAL, hold them suspended, then approve them all in a
burst. Measures memory per suspended workflow, whether any state was
lost, burst-resume latency percentiles, and (with a live Postgres) DB
connection behavior during the burst.

    python -m src.eval.load_test --n 100 500 1000
    python -m src.eval.load_test --n 100 --postgres

Uses InMemorySaver by default so it runs anywhere; --postgres switches to
a real AsyncPostgresSaver against DATABASE_URL, which is what makes the
concurrent-suspension number (section 11 #14) a claim about the actual
durable checkpointer rather than a Python dict.

Deliberately does not import test doubles from tests/ (src/ must never
depend on tests/), so the scripted reasoner/classifier below are a small
local duplicate — the same pattern src/chaos/agent_process.py uses.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import time
import tracemalloc
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.graph import build_graph, resume_workflow, start_workflow
from src.agent.runtime import AgentRuntimeContext
from src.audit.store import InMemoryAuditStore
from src.durability.checkpointer import postgres_checkpointer, run_async
from src.eval.runner import build_sandbox

RESULTS_DIR = Path(__file__).parent.parent.parent / "results"


class _LoadTestReasoner:
    async def diagnose(self, observations: dict[str, Any]) -> dict[str, Any]:
        return {"summary": "load-test stub diagnosis", "root_cause_service": "service_a"}

    async def propose_action(
        self, observations: dict[str, Any], diagnosis: dict[str, Any], replan_context: str | None
    ) -> dict[str, Any]:
        return {"tool": "restart_service", "target": "service_a", "rationale": "load-test stub", "parameters": {}}


class _LoadTestRiskClassifier:
    async def classify(
        self,
        proposed_action: str,
        diagnosis: str,
        rationale: str | None,
        situational: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "tier": "medium",
            "reversibility": "stub",
            "blast_radius": "stub",
            "data_destructiveness": "stub",
            "rationale": "load-test stub, always medium so every run suspends",
        }


@dataclass
class LoadTestReport:
    n: int
    concurrency: int
    checkpointer: str
    suspend_wall_seconds: float
    all_suspended: bool
    suspended_count: int
    bytes_per_run: float | None
    burst_wall_seconds: float
    resume_latency_ms: dict[str, float]
    state_intact: bool
    completed_count: int
    peak_db_connections: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _bounded(semaphore: asyncio.Semaphore, coro):
    async with semaphore:
        return await coro


async def _sample_pg_connections(dsn: str, stop: asyncio.Event, peak: list[int]) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        while not stop.is_set():
            count = await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
            peak[0] = max(peak[0], count)
            await asyncio.sleep(0.05)
    finally:
        await conn.close()


async def run_load_test(n: int, *, dsn: str | None = None, concurrency: int = 50) -> LoadTestReport:
    clients, tool_ctx = build_sandbox(None)
    try:
        context = AgentRuntimeContext(
            tool_ctx=tool_ctx,
            reasoner=_LoadTestReasoner(),
            risk_classifier=_LoadTestRiskClassifier(),
            audit_store=InMemoryAuditStore(),
        )

        if dsn:
            async with postgres_checkpointer(dsn) as checkpointer:
                return await _run(n, concurrency, checkpointer, context, dsn)
        return await _run(n, concurrency, InMemorySaver(), context, None)
    finally:
        for client in clients.values():
            await client.aclose()


async def _run(n: int, concurrency: int, checkpointer: Any, context: AgentRuntimeContext, dsn: str | None) -> LoadTestReport:
    graph = build_graph(checkpointer)
    run_ids = [f"load-{i}" for i in range(n)]
    semaphore = asyncio.Semaphore(concurrency)

    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()

    t0 = time.perf_counter()
    results = await asyncio.gather(
        *(_bounded(semaphore, start_workflow(graph, run_id, {}, context)) for run_id in run_ids)
    )
    suspend_wall_seconds = time.perf_counter() - t0

    suspended_count = sum(1 for r in results if "__interrupt__" in r)
    all_suspended = suspended_count == n

    gc.collect()
    after, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    bytes_per_run = (after - before) / n if n else None

    stop_sampling = asyncio.Event()
    peak_connections = [0]
    sampler = None
    if dsn:
        sampler = asyncio.create_task(_sample_pg_connections(dsn, stop_sampling, peak_connections))

    latencies_ms: list[float] = []

    async def _resume_one(run_id: str) -> dict[str, Any]:
        t = time.perf_counter()
        result = await resume_workflow(graph, run_id, {"decision": "approved", "approver_id": "load-test"}, context)
        latencies_ms.append((time.perf_counter() - t) * 1000)
        return result

    t_burst = time.perf_counter()
    resumed = await asyncio.gather(*(_bounded(semaphore, _resume_one(run_id)) for run_id in run_ids))
    burst_wall_seconds = time.perf_counter() - t_burst

    stop_sampling.set()
    if sampler:
        await sampler

    completed_count = sum(1 for r in resumed if r.get("final_state") == "COMPLETED")
    state_intact = completed_count == n

    arr = np.array(latencies_ms, dtype=float) if latencies_ms else np.array([0.0])
    resume_latency_ms = {
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
    }

    return LoadTestReport(
        n=n,
        concurrency=concurrency,
        checkpointer="postgres" if dsn else "in-memory",
        suspend_wall_seconds=suspend_wall_seconds,
        all_suspended=all_suspended,
        suspended_count=suspended_count,
        bytes_per_run=bytes_per_run,
        burst_wall_seconds=burst_wall_seconds,
        resume_latency_ms=resume_latency_ms,
        state_intact=state_intact,
        completed_count=completed_count,
        peak_db_connections=peak_connections[0] if dsn else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", nargs="+", type=int, default=[100, 500, 1000])
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--postgres", action="store_true", help="Use AsyncPostgresSaver against DATABASE_URL.")
    args = parser.parse_args()

    import os

    dsn = os.environ["DATABASE_URL"] if args.postgres else None

    reports = []
    for n in args.n:
        report = run_async(run_load_test(n, dsn=dsn, concurrency=args.concurrency))
        reports.append(report.as_dict())
        print(
            f"n={report.n:5d}  checkpointer={report.checkpointer:10s}  "
            f"suspend={report.suspend_wall_seconds:6.2f}s  "
            f"bytes/run={report.bytes_per_run or 0:8.0f}  "
            f"resume p50/p95/p99={report.resume_latency_ms['p50']:.1f}/"
            f"{report.resume_latency_ms['p95']:.1f}/{report.resume_latency_ms['p99']:.1f}ms  "
            f"state_intact={report.state_intact}"
        )

    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "load_test_summary.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
