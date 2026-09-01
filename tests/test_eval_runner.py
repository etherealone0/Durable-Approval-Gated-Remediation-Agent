"""Exercises src/eval/runner.py's run_scenario against small in-process
scenarios (scripted reasoner/classifier, no API key or Docker needed) to
prove the harness produces correctly-shaped runs.jsonl records and that
the --no-revalidation ablation actually behaves differently from the
default: it executes a stale approved action blindly where the default
config detects the drift and replans instead (PROJECT_SPEC.md section 11's
required ablation — "the strongest single result in the project").
"""

from __future__ import annotations

from src.eval.runner import run_scenario
from tests.helpers import ScriptedReasoner, ScriptedRiskClassifier

LOW_RISK_SCENARIO = {
    "id": "eval-test-low",
    "category": "low_risk",
    "fault_injection": {"service": "service_a", "type": "memory_leak", "rate": "high"},
}

MEDIUM_RISK_SCENARIO = {
    "id": "eval-test-medium",
    "category": "medium_high_risk",
    "fault_injection": {"service": "service_a", "type": "memory_leak", "rate": "high"},
}

STALENESS_SCENARIO = {
    "id": "eval-test-staleness",
    "category": "staleness",
    "fault_injection": {"service": "service_b", "type": "memory_leak", "rate": "high"},
    "staleness_injection": {
        "timing": "during_approval_wait",
        "changes": [{"action": "reset", "service": "service_b"}],
    },
    "expected_drift_detected": True,
}


async def test_low_risk_run_executes_without_suspension():
    record = await run_scenario(
        LOW_RISK_SCENARIO,
        mode="full",
        reasoner=ScriptedReasoner(tool="restart_service", target="service_a"),
        risk_classifier=ScriptedRiskClassifier(tier="low"),
        simulated_approval_wait_seconds=0,
    )

    assert record["final_state"] == "COMPLETED"
    assert record["approval_wait_seconds"] is None
    assert record["executed_actions"] == ["restart_service:service_a"]
    assert record["verification_passed"] is True
    assert record["drift_detected"] is None
    assert record["proposed_actions"] == ["restart_service:service_a"]


async def test_medium_risk_run_suspends_then_resumes_and_executes():
    record = await run_scenario(
        MEDIUM_RISK_SCENARIO,
        mode="full",
        reasoner=ScriptedReasoner(tool="restart_service", target="service_a"),
        risk_classifier=ScriptedRiskClassifier(tier="medium"),
        simulated_approval_wait_seconds=0.05,
    )

    assert record["approval_wait_seconds"] == 0.05
    assert record["time_to_resume_ms"] is not None
    assert record["revalidation_triggered"] is True
    assert record["drift_detected"] is False  # nothing changed while suspended
    assert record["final_state"] == "COMPLETED"
    assert record["executed_actions"] == ["restart_service:service_a"]


async def test_audit_records_embedded_and_form_a_complete_chain():
    from src.audit.replay import is_complete_chain

    record = await run_scenario(
        LOW_RISK_SCENARIO,
        mode="full",
        reasoner=ScriptedReasoner(tool="restart_service", target="service_a"),
        risk_classifier=ScriptedRiskClassifier(tier="low"),
        simulated_approval_wait_seconds=0,
    )

    assert record["audit_records"]
    assert is_complete_chain(record["audit_records"])


async def test_full_mode_detects_staleness_drift_and_replans():
    record = await run_scenario(
        STALENESS_SCENARIO,
        mode="full",
        reasoner=ScriptedReasoner(tool="restart_service", target="service_b"),
        risk_classifier=ScriptedRiskClassifier(tier="medium"),
        simulated_approval_wait_seconds=0,
    )

    assert record["staleness_injection_applied"] is True
    assert record["drift_detected"] is True
    assert record["replan_cycles"] >= 1


async def test_no_revalidation_ablation_executes_the_stale_action_blindly():
    record = await run_scenario(
        STALENESS_SCENARIO,
        mode="no_revalidation",
        reasoner=ScriptedReasoner(tool="restart_service", target="service_b"),
        risk_classifier=ScriptedRiskClassifier(tier="medium"),
        simulated_approval_wait_seconds=0,
    )

    assert record["staleness_injection_applied"] is True
    assert record["drift_detected"] is None  # revalidate never ran at all
    assert record["replan_cycles"] == 0
    assert record["executed_actions"] == ["restart_service:service_b"]  # executed anyway, despite the reset


async def test_no_durability_mode_still_completes_a_run():
    record = await run_scenario(
        LOW_RISK_SCENARIO,
        mode="no_durability",
        reasoner=ScriptedReasoner(tool="restart_service", target="service_a"),
        risk_classifier=ScriptedRiskClassifier(tier="low"),
        simulated_approval_wait_seconds=0,
    )

    assert record["final_state"] == "COMPLETED"


async def test_llm_calls_and_tokens_are_recorded():
    record = await run_scenario(
        LOW_RISK_SCENARIO,
        mode="full",
        reasoner=ScriptedReasoner(tool="restart_service", target="service_a"),
        risk_classifier=ScriptedRiskClassifier(tier="low"),
        simulated_approval_wait_seconds=0,
    )

    assert record["llm_calls"] == 3  # diagnose + propose_action + classify
    assert record["total_tokens"] > 0
