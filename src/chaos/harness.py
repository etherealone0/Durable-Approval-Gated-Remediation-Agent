"""Chaos harness (PROJECT_SPEC.md section 12): kills the agent process at
5 defined points, restarts it, and asserts correct resumption with no
duplicate side effects. Requires a live Postgres (for the checkpointer
and the executed_actions/audit/runs tables to survive a kill) and starts
its own out-of-process mock services — plain `uvicorn` subprocesses, no
Docker needed — because a mock service living inside the agent process
would die with it, destroying the evidence a kill point 4/5 needs.

Run directly for the pass-rate table:
    python -m src.chaos.harness --dsn postgresql://... [--trials N]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_PROCESS_CMD = [sys.executable, "-m", "src.chaos.agent_process"]
MOCK_SERVICE_ROLES = {"service_a": "compute", "service_b": "compute", "service_c": "disk"}


def _free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockServices:
    """Real, independent uvicorn subprocesses for service_a/b/c — no
    Docker, but genuinely out-of-process, so they survive the agent
    process being killed. Required for kill points 4 and 5 to prove
    anything; harmless for 1-3."""

    def __init__(self) -> None:
        self.ports = {name: _free_port() for name in MOCK_SERVICE_ROLES}
        self._procs: list[subprocess.Popen] = []

    def start(self) -> None:
        for name, role in MOCK_SERVICE_ROLES.items():
            env = {**os.environ, "SERVICE_NAME": name, "SERVICE_ROLE": role}
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "src.env.mock_service.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.ports[name]),
                ],
                cwd=str(REPO_ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._procs.append(proc)
        self._wait_ready()

    def _wait_ready(self, timeout: float = 20.0) -> None:
        for name, port in self.ports.items():
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError(f"mock service {name} never became ready on port {port}")

    def env_vars(self) -> dict[str, str]:
        return {
            "SERVICE_A_URL": f"http://127.0.0.1:{self.ports['service_a']}",
            "SERVICE_B_URL": f"http://127.0.0.1:{self.ports['service_b']}",
            "SERVICE_C_URL": f"http://127.0.0.1:{self.ports['service_c']}",
        }

    def reset(self) -> None:
        for port in self.ports.values():
            httpx.post(f"http://127.0.0.1:{port}/admin/reset", timeout=5)

    def get_metrics(self, service: str) -> dict:
        return httpx.get(f"http://127.0.0.1:{self.ports[service]}/metrics", timeout=5).json()

    def inject_fault(self, service: str, fault_type: str, rate: str = "high") -> None:
        resp = httpx.post(
            f"http://127.0.0.1:{self.ports[service]}/admin/fault",
            json={"type": fault_type, "rate": rate},
            timeout=5,
        )
        resp.raise_for_status()

    def stop(self) -> None:
        for proc in self._procs:
            proc.terminate()
        for proc in self._procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def _launch(mode_args: list[str], env_extra: dict[str, str]) -> subprocess.Popen:
    env = {**os.environ, **env_extra}
    return subprocess.Popen(
        AGENT_PROCESS_CMD + mode_args,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def _watch_for_marker_and_kill(proc: subprocess.Popen, marker: str, timeout: float = 15.0) -> bool:
    """Reads proc.stdout until the marker line appears, then kills the
    process immediately. Returns False if the process exited on its own
    (or the timeout elapsed) without ever printing it."""
    deadline = time.time() + timeout
    target = f"CHAOS_MARKER:{marker}"
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            return False
        if line.strip() == target:
            proc.kill()
            proc.wait(timeout=5)
            return True
    proc.kill()
    return False


@dataclass
class KillPointResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class HarnessReport:
    results: list[KillPointResult] = field(default_factory=list)

    def pass_rate_table(self) -> str:
        by_point: dict[str, list[bool]] = {}
        for r in self.results:
            by_point.setdefault(r.name, []).append(r.passed)
        lines = [f"{'kill point':<32} {'pass rate':>10}", "-" * 43]
        for name, outcomes in by_point.items():
            rate = 100.0 * sum(outcomes) / len(outcomes)
            lines.append(f"{name:<32} {rate:>9.0f}% ({sum(outcomes)}/{len(outcomes)})")
        return "\n".join(lines)


def trial_during_diagnosing(services: MockServices, dsn: str) -> KillPointResult:
    services.reset()
    run_id = f"chaos-diagnosing-{uuid.uuid4()}"
    env = {**services.env_vars(), "CHAOS_KILL_AT": "DIAGNOSING", "CHAOS_TIER": "low"}
    proc = _launch(["start", dsn, run_id], env)
    if not _watch_for_marker_and_kill(proc, "DIAGNOSING"):
        return KillPointResult("during DIAGNOSING", False, "marker never observed before process exited")

    # Nothing checkpointed yet (diagnose() never returned): resuming here
    # means calling start again on the same thread_id, which restarts
    # cleanly from START (verified empirically — no pending interrupt).
    proc2 = _launch(["start", dsn, run_id], {**services.env_vars(), "CHAOS_TIER": "low"})
    out, err = proc2.communicate(timeout=20)
    if proc2.returncode != 0:
        return KillPointResult("during DIAGNOSING", False, f"resume failed: {err}")
    result = json.loads(out.strip().splitlines()[-1])
    return KillPointResult("during DIAGNOSING", result.get("status") == "COMPLETED", json.dumps(result))


def trial_immediately_after_interrupt(services: MockServices, dsn: str) -> KillPointResult:
    services.reset()
    run_id = f"chaos-after-interrupt-{uuid.uuid4()}"
    env = {**services.env_vars(), "CHAOS_TIER": "medium", "CHAOS_DIE_AFTER_INTERRUPT": "1"}
    proc = _launch(["start", dsn, run_id], env)
    proc.wait(timeout=20)
    # The process self-terminated via os._exit(1) right after suspending;
    # a fresh process must still find it durably AWAITING_APPROVAL.
    proc2 = _launch(
        ["resume", dsn, run_id, json.dumps({"decision": "approved", "approver_id": "chaos-harness"})],
        services.env_vars(),
    )
    out, err = proc2.communicate(timeout=20)
    if proc2.returncode != 0:
        return KillPointResult("immediately after interrupt() fires", False, f"resume failed: {err}")
    result = json.loads(out.strip().splitlines()[-1])
    return KillPointResult(
        "immediately after interrupt() fires", result.get("status") == "COMPLETED", json.dumps(result)
    )


def trial_during_approval_wait(services: MockServices, dsn: str, wait_seconds: float = 1.0) -> KillPointResult:
    services.reset()
    run_id = f"chaos-approval-wait-{uuid.uuid4()}"
    proc = _launch(["start", dsn, run_id], {**services.env_vars(), "CHAOS_TIER": "medium"})
    out, err = proc.communicate(timeout=20)
    if proc.returncode != 0:
        return KillPointResult("during the approval wait", False, f"start failed: {err}")
    started = json.loads(out.strip().splitlines()[-1])
    if not started.get("suspended"):
        return KillPointResult("during the approval wait", False, "did not suspend")

    time.sleep(wait_seconds)  # zero compute elapses here — no process is running at all

    proc2 = _launch(
        ["resume", dsn, run_id, json.dumps({"decision": "approved", "approver_id": "chaos-harness"})],
        services.env_vars(),
    )
    out2, err2 = proc2.communicate(timeout=20)
    if proc2.returncode != 0:
        return KillPointResult("during the approval wait", False, f"resume failed: {err2}")
    result = json.loads(out2.strip().splitlines()[-1])
    return KillPointResult("during the approval wait", result.get("status") == "COMPLETED", json.dumps(result))


def trial_mid_executing(services: MockServices, dsn: str) -> KillPointResult:
    services.reset()
    run_id = f"chaos-mid-executing-{uuid.uuid4()}"
    env = {**services.env_vars(), "CHAOS_KILL_AT": "EXECUTING", "CHAOS_TIER": "low"}
    proc = _launch(["start", dsn, run_id], env)
    if not _watch_for_marker_and_kill(proc, "EXECUTING"):
        return KillPointResult("mid-EXECUTING (idempotency)", False, "marker never observed before process exited")

    restart_count_after_kill = services.get_metrics("service_a")["restart_count"]

    # Risk is low, so the run never suspends — execute() never checkpointed
    # (it was killed right after the tool call, before returning), so this
    # is a restart from START, same as the DIAGNOSING trial. The tool's own
    # idempotency ledger (Postgres, section 8) is what must make the
    # replayed restart_service call a no-op, not the graph's checkpoint.
    proc2 = _launch(["start", dsn, run_id], {**services.env_vars(), "CHAOS_TIER": "low"})
    out, err = proc2.communicate(timeout=20)
    if proc2.returncode != 0:
        return KillPointResult("mid-EXECUTING (idempotency)", False, f"resume failed: {err}")
    result = json.loads(out.strip().splitlines()[-1])

    restart_count_final = services.get_metrics("service_a")["restart_count"]
    no_double_apply = restart_count_final == restart_count_after_kill  # already restarted before the kill
    passed = result.get("status") == "COMPLETED" and no_double_apply
    detail = json.dumps({**result, "restart_count_after_kill": restart_count_after_kill, "restart_count_final": restart_count_final})
    return KillPointResult("mid-EXECUTING (idempotency)", passed, detail)


def trial_during_rolling_back(services: MockServices, dsn: str) -> KillPointResult:
    services.reset()
    run_id = f"chaos-rolling-back-{uuid.uuid4()}"
    # restart_service can never fix a permanently full disk, guaranteeing
    # verification failure and a real ROLLING_BACK to kill mid-way through.
    services.inject_fault("service_c", "disk_full", "high")
    env = {
        **services.env_vars(),
        "CHAOS_KILL_AT": "ROLLING_BACK",
        "CHAOS_TIER": "low",
        "CHAOS_TOOL": "restart_service",
        "CHAOS_TARGET": "service_c",
    }
    proc = _launch(["start", dsn, run_id], env)
    if not _watch_for_marker_and_kill(proc, "ROLLING_BACK"):
        return KillPointResult("during ROLLING_BACK", False, "marker never observed before process exited")

    proc2 = _launch(
        ["start", dsn, run_id],
        {**services.env_vars(), "CHAOS_TIER": "low", "CHAOS_TOOL": "restart_service", "CHAOS_TARGET": "service_c"},
    )
    out, err = proc2.communicate(timeout=20)
    if proc2.returncode != 0:
        return KillPointResult("during ROLLING_BACK", False, f"resume failed: {err}")
    result = json.loads(out.strip().splitlines()[-1])
    # restart_service's compensation is a noop, so the only thing that
    # could double-apply here is nothing — the real assertion is simply
    # that the run reaches a stable terminal state without hanging or
    # erroring on a replayed rolling_back.
    passed = result.get("status") in ("ESCALATED", "COMPLETED")
    return KillPointResult("during ROLLING_BACK", passed, json.dumps(result))


TRIALS = [
    trial_during_diagnosing,
    trial_immediately_after_interrupt,
    trial_during_approval_wait,
    trial_mid_executing,
    trial_during_rolling_back,
]


def run_harness(dsn: str, trials: int = 3) -> HarnessReport:
    services = MockServices()
    services.start()
    report = HarnessReport()
    try:
        for trial_fn in TRIALS:
            for _ in range(trials):
                report.results.append(trial_fn(services, dsn))
    finally:
        services.stop()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or DATABASE_URL is required")

    report = run_harness(args.dsn, trials=args.trials)
    print(report.pass_rate_table())
    if not all(r.passed for r in report.results):
        sys.exit(1)


if __name__ == "__main__":
    main()
