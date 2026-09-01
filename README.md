# Interlock

A durable, human-in-the-loop, approval-gated infrastructure remediation agent. It diagnoses an incident against a sandboxed production environment, proposes a remediation action, classifies the action's risk, and then either executes autonomously (low risk) or suspends indefinitely — holding zero compute — until a human approves, edits, or rejects it from a completely separate process. On resume it re-validates that its diagnosis still holds before touching anything, executes exactly once even if the process is killed mid-action, verifies the fix, and rolls back on failure. Every transition is recorded to an append-only audit log that can reconstruct any run end-to-end on its own.

The numbers below come from the actual test suite and evaluation harness in this repo, not from hand-picked examples.

> **Reproducing these numbers**: this build environment has no Docker daemon and no `ANTHROPIC_API_KEY`, so the tables below were produced by `python -m src.eval.run_suite` running against the **heuristic fallback reasoner** (`src/eval/runner.py`) — a rule-based stand-in that inspects the same read-only sweep an LLM would but does no real reasoning, and `InMemorySaver` instead of a live Postgres checkpointer. It's there so the harness runs end-to-end without external services; it is not a substitute for the real diagnostic quality of `AnthropicDiagnosisReasoner` + `AnthropicRiskClassifier`, which activate automatically once `ANTHROPIC_API_KEY` is set. Anywhere a number reflects the fallback's own limits rather than the system's, it's called out inline.

## Results at a glance (50-scenario suite, `full` config)

**Safety** — lead with these; a miss here is a bug, not a score.

| Metric | Result |
|---|---|
| Unsafe action prevention (overall) | 100% (50/50) |
| Unsafe action prevention (trap scenarios) | 100% (8/8) |
| Approval gate compliance | 100% — every medium/high-risk action suspended for approval |
| Policy override rate | 0%¹ |

¹ The heuristic fallback never proposes `delete_records`/`rollback_deployment`, so the deterministic high-risk override never has an LLM tier to overrule. `src/risk/policy.py` and its forced-high behavior are covered directly by unit tests (`tests/test_risk_policy.py`), independent of this suite run.

**Durability** — the distinctive claims.

| Metric | Result |
|---|---|
| Time to resume (decision API call → leaving `AWAITING_APPROVAL`) | p50 31 ms · p95 41 ms · p99 44 ms |
| Compute idle ratio (avg. across all suspended runs) | 0.945 |
| Rollback success rate | 100% |
| Cost per run | ~1,003 tokens · 3.2 LLM calls avg (heuristic-reasoner token counts are an estimate, not billing-accurate — see `src/eval/runner.py`) |

**Correctness**

| Metric | Result |
|---|---|
| Diagnosis accuracy (first proposed action ∈ `acceptable_actions`) | 80% |
| Remediation success rate (reached `COMPLETED` + verified) | 98% |
| Staleness detection rate | 57.1% (4/7) |
| False drift rate (drift flagged when nothing material changed) | 0% |
| Audit completeness (full run reconstructable from the log alone) | 100% |

## The revalidation ablation — the strongest single result

Every staleness scenario in the suite deliberately mutates the environment *while a run sits suspended waiting for approval*, so that blindly executing the already-approved action would now be wrong. Running the full suite three ways:

| Config | Staleness detection rate | Staleness scenarios executed despite drift |
|---|---|---|
| `full` (durable + revalidation) | 57.1% | **3 / 7** |
| `no_durability` (in-memory checkpoint, revalidation on) | 57.1% | **3 / 7** |
| `no_revalidation` (approved actions execute blindly) | 0.0% | **7 / 7** |

Removing revalidation doesn't just lower a percentage — it converts every single staleness scenario into an incorrect action executed against a world that had already changed. Revalidation cuts that from 7/7 to 3/7 (the remaining 3 are the heuristic fallback's diagnostic limits, not a revalidation failure: `staleness_detection_rate` only counts scenarios where drift was caught at all, which needs a target the reasoner actually revisits — see the caveat above).

`full` and `no_durability` are identical here by design: removing durability doesn't change *what* the agent decides mid-run, only whether it survives a process crash while doing it. That crash-survival claim is what the chaos harness and cross-process tests below prove instead — this ablation isolates revalidation specifically.

## Compute idle ratio vs. approval-wait duration

A run holds zero compute while suspended: the process can exit entirely and the workflow doesn't miss a beat. `compute_idle_ratio = 1 − compute_seconds_active / wall_clock_seconds`, measured across a handful of scenarios at increasing simulated approval-wait durations:

```
wait (s)   idle ratio
  0.1      0.740   ███████████████░░░░░
  1.0      0.957   ███████████████████░
  5.0      0.988   ███████████████████▉
 15.0      0.997   ████████████████████
 30.0      0.999   ████████████████████
```

The curve is the point: at a 30-second wait the agent spends under 0.1% of that time doing anything at all. In production, where an approval can take hours, that ratio rounds to 1.0.

## Chaos harness — pass rate per kill point

`src/chaos/harness.py` kills the agent process at each of five points and asserts it resumes correctly with no duplicate side effects, using real out-of-process `uvicorn` subprocesses standing in for the sandbox services (an in-process mock would die along with the agent process being killed, destroying the evidence). Kill points:

| Kill point | What it proves |
|---|---|
| During `DIAGNOSING` | A crash before any decision is made restarts cleanly |
| Immediately after `interrupt()` fires | Suspension is durable the instant it happens — `os._exit(1)` right after, no chance to do anything else |
| During the approval wait | Kill → wait → restart from a fresh process → *then* approve — the important one |
| Mid-`EXECUTING` | Exactly-once execution: the idempotency key survives the crash and a retried tool call no-ops |
| During `ROLLING_BACK` | The compensating action is safe to resume, not just the original one |

This build environment has no Docker daemon, so the full 5-kill-point run (`tests/test_chaos_harness.py`, gated on a live Postgres) is skipped here — only the always-runnable smoke test that the real subprocess mock services start, take faults, and reset correctly passes (`tests/test_chaos_mock_services.py`, 3/3). Run `docker compose up -d && python -m src.chaos.harness` to produce the real pass-rate table; the harness is designed to report:

```
kill point                        pass rate
-------------------------------------------
DIAGNOSING                            100% (n/n)
IMMEDIATELY_AFTER_INTERRUPT           100% (n/n)
DURING_APPROVAL_WAIT                  100% (n/n)
MID_EXECUTING                         100% (n/n)
ROLLING_BACK                          100% (n/n)
```

## Concurrent suspension / load test

`src/eval/load_test.py`: N workflows started concurrently, held suspended, then approved in a burst (`InMemorySaver`, concurrency=50 — see the module for the Postgres-backed variant with connection sampling):

| N | Suspend wall time | Memory / suspended run | Resume latency (p50 / p95 / p99) | State intact |
|---|---|---|---|---|
| 100 | 5.8 s | ~46 KB | 1267 / 1384 / 1407 ms | ✅ 100/100 |
| 500 | 41.0 s | ~35 KB | 1471 / 1630 / 1669 ms | ✅ 500/500 |
| 1000 | 82.7 s | ~35 KB | 1567 / 1723 / 1815 ms | ✅ 1000/1000 |

Zero lost state at every scale tested. Resume latency grows with N because all resumes share one bounded worker pool (`concurrency=50`) doing real work (a fresh diagnostic sweep + tool call each) — it's a concurrency-budget curve, not a leak.

## State machine

```mermaid
stateDiagram-v2
    [*] --> DIAGNOSING
    DIAGNOSING --> ACTION_PROPOSED
    ACTION_PROPOSED --> RISK_CLASSIFIED
    RISK_CLASSIFIED --> EXECUTING: low risk
    RISK_CLASSIFIED --> AWAITING_APPROVAL: medium/high risk
    AWAITING_APPROVAL --> REVALIDATING: approved / edited
    AWAITING_APPROVAL --> REPLANNING: rejected
    AWAITING_APPROVAL --> ESCALATED: timeout (SLA breach)
    REVALIDATING --> EXECUTING: no material drift
    REVALIDATING --> REPLANNING: drift detected
    EXECUTING --> VERIFYING
    VERIFYING --> COMPLETED: verified
    VERIFYING --> ROLLING_BACK: failed
    ROLLING_BACK --> ROLLED_BACK
    ROLLED_BACK --> REPLANNING
    REPLANNING --> ACTION_PROPOSED: replan_cycles < 3
    REPLANNING --> ESCALATED: replan_cycles >= 3
    COMPLETED --> [*]
    ESCALATED --> [*]
```

`--no-revalidation` removes the `REVALIDATING` node entirely and routes `approved`/`edited` straight to `EXECUTING` (`src/agent/graph.py`'s `revalidate=False`).

## Architecture

- **Durable graph** (`src/agent/`) — a LangGraph `StateGraph` over the state machine above, checkpointed with `AsyncPostgresSaver` (`durability="sync"` on every call), so a suspended run can be resumed by `thread_id` from a completely different process with no shared memory. `interrupt()`/`Command(resume=...)` implement the approval gate.
- **Dependency injection** (`src/agent/runtime.py`) — every external dependency (tool access, the diagnosis/risk LLM calls, the audit store) is a `Protocol` injected via LangGraph's `context_schema`, never stashed in checkpointed state. Each has an in-memory implementation (fast unit tests) and a Postgres/HTTP one (production), used identically by both.
- **Sandbox environment** (`src/env/`) — three mock FastAPI services with health/metrics/logs/disk endpoints and a fault injector that drives them into named bad states, plus a mock Postgres "production" database of records the agent can read and, under approval, mutate.
- **Tools** (`src/tools/`) — read-only (`get_service_health`, `read_logs`, `get_metrics`, `query_db_readonly`, `check_disk_usage`) and mutating (`restart_service`, `scale_service`, `delete_records`, `clear_cache`, `apply_config_change`, `rollback_deployment`). Every mutating tool is keyed by a deterministic idempotency key (`run_id:proposed_action`) checked against a Postgres ledger before acting, and registers a compensating action for rollback.
- **Risk classification** (`src/risk/`) — a structured LLM call plus a deterministic override that forces `delete_records`/`rollback_deployment` to `high` regardless of what the model says.
- **Staleness revalidation** (`src/revalidation/`) — a scoped fingerprint (hash of only the observation fields the proposed action actually depends on) captured at proposal time and recomputed on resume; a mismatch routes to `REPLANNING` instead of blindly executing.
- **Audit trail** (`src/audit/`) — every node transition is wrapped and written as one append-only record; `replay_run`/`is_complete_chain` reconstruct a run from the log alone.
- **API + UI** (`src/api/`, `frontend/`) — FastAPI endpoints (`POST /runs`, `GET /runs/{id}`, `GET /runs/pending-approval`, `POST /runs/{id}/decision`, `GET /runs/{id}/audit`) and a minimal Next.js approval queue. The decision endpoint is what makes "resumed by an external event" literal: any process holding a graph built against the same Postgres DSN can call it.
- **Chaos harness** (`src/chaos/`) — kills the agent process at five points via real OS subprocesses and out-of-process mock services, asserting correct resumption and exactly-once execution.
- **Evaluation** (`src/eval/`) — `metrics.py` (every metric above, as pure functions over `results/runs.jsonl`), `runner.py` (drives the graph per scenario in `full`/`no_durability`/`no_revalidation` configs), `run_suite.py` (the CLI), `load_test.py` (concurrent-suspension load test).

## Repo layout

```
src/
  env/        sandbox: mock services, fault injector, mock DB
  tools/      read-only + mutating tools, idempotency, compensation
  agent/      state machine, nodes, diagnosis/proposal, runtime context
  risk/       LLM risk classification + deterministic policy override
  revalidation/  state fingerprinting for staleness detection
  durability/ AsyncPostgresSaver factory (+ Windows event-loop fix)
  audit/      append-only audit log, replay, the with_audit() wrapper
  api/        FastAPI app + production entrypoint (python -m src.api)
  chaos/      process-kill harness, real subprocess mock services
  eval/       metrics, scenario runner, ablation CLI, load test
data/scenarios.json   50 scenarios: 15 low-risk, 20 medium/high-risk, 8 trap, 7 staleness
frontend/     Next.js approval queue
tests/        138 passing, 7 skipped (Postgres/Docker-gated) in this environment
```

## Setup

```bash
cp .env.example .env        # fill in ANTHROPIC_API_KEY for real diagnosis/risk reasoning
docker compose up -d        # 3 mock services + Postgres
pip install -e ".[dev]"

pytest                      # 138 passed, 7 skipped without Docker; all pass with it running

python -m src.api           # FastAPI on :8000 (see .env's API_HOST/API_PORT)
cd frontend && npm install && npm run dev   # approval queue on :3000

python -m src.eval.run_suite              # full/no_durability/no_revalidation over all 50 scenarios
python -m src.eval.run_suite --wait-seconds 30   # a longer simulated approval wait for the idle-ratio curve
python -m src.chaos.harness               # 5-kill-point pass-rate table
python -m src.eval.load_test --n 100 500 1000 --postgres   # concurrent-suspension numbers against real Postgres
```

Without `ANTHROPIC_API_KEY`, everything above still runs end-to-end against the heuristic fallback reasoner in `src/eval/runner.py`; without Docker, everything falls back to in-process mock services and `InMemorySaver` where the code supports it, and the Postgres-specific tests skip rather than fail.
