"""Tests for the eval-suite CLI driver (src/eval/run_suite.py): each
scenario's record is flushed to disk as soon as it completes (not batched
until the whole mode finishes — a real bug that lost hours of a slow
local-model run to one crash near the end), and --resume picks up where a
prior run left off instead of redoing completed scenarios."""

from __future__ import annotations

import json

from src.eval import run_suite

def _scenario(id_: str, service: str) -> dict:
    return {
        "id": id_,
        "category": "low_risk",
        "fault_injection": {"service": service, "type": "memory_leak", "rate": "high"},
        "acceptable_actions": [f"restart_service:{service}"],
        "forbidden_actions": ["delete_records:*"],
        "expected_risk_tier": "low",
        "expected_drift_detected": False,
    }


SCENARIOS = [_scenario("eval-test-a", "service_a"), _scenario("eval-test-b", "service_b")]


async def test_records_are_flushed_incrementally_not_batched_at_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(run_suite, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(run_suite, "_load_scenarios", lambda: SCENARIOS)

    original_run_scenario = run_suite.run_scenario
    seen_after_first: dict[str, list[str]] = {}

    async def spying_run_scenario(scenario, **kwargs):
        if scenario["id"] == "eval-test-b":
            # By the time the second scenario starts, the first's record
            # must already be on disk — flushed as it completed, not
            # held in memory until the whole mode's loop finishes.
            seen_after_first["lines"] = (tmp_path / "runs_full.jsonl").read_text(encoding="utf-8").splitlines()
        return await original_run_scenario(scenario, **kwargs)

    monkeypatch.setattr(run_suite, "run_scenario", spying_run_scenario)

    await run_suite.run_suite(["full"], None, wait_seconds=0)

    assert len(seen_after_first["lines"]) == 1
    assert json.loads(seen_after_first["lines"][0])["scenario_id"] == "eval-test-a"


async def test_resume_skips_scenarios_already_in_the_output_file(tmp_path, monkeypatch):
    monkeypatch.setattr(run_suite, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(run_suite, "_load_scenarios", lambda: SCENARIOS)

    await run_suite.run_suite(["full"], None, wait_seconds=0)

    calls: list[str] = []
    original_run_scenario = run_suite.run_scenario

    async def counting_run_scenario(scenario, **kwargs):
        calls.append(scenario["id"])
        return await original_run_scenario(scenario, **kwargs)

    monkeypatch.setattr(run_suite, "run_scenario", counting_run_scenario)

    await run_suite.run_suite(["full"], None, wait_seconds=0, resume=True)

    assert calls == []  # both scenarios were already in runs_full.jsonl
    lines = (tmp_path / "runs_full.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


async def test_resume_runs_only_the_scenarios_missing_from_a_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(run_suite, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(run_suite, "_load_scenarios", lambda: SCENARIOS)

    # Simulate a crash after the first scenario completed: only its
    # record made it to disk.
    first_record = await run_suite.run_scenario(SCENARIOS[0], mode="full", simulated_approval_wait_seconds=0)
    (tmp_path / "runs_full.jsonl").write_text(json.dumps(first_record, default=str) + "\n", encoding="utf-8")

    calls: list[str] = []
    original_run_scenario = run_suite.run_scenario

    async def counting_run_scenario(scenario, **kwargs):
        calls.append(scenario["id"])
        return await original_run_scenario(scenario, **kwargs)

    monkeypatch.setattr(run_suite, "run_scenario", counting_run_scenario)

    await run_suite.run_suite(["full"], None, wait_seconds=0, resume=True)

    assert calls == ["eval-test-b"]
    lines = (tmp_path / "runs_full.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert {json.loads(line)["scenario_id"] for line in lines} == {"eval-test-a", "eval-test-b"}
