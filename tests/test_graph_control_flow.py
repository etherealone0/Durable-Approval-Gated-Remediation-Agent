"""Exercises every named state and transition in PROJECT_SPEC.md section
4's state machine diagram, using InMemorySaver (unit-test only checkpointer
per section 5; durability itself is proven separately in
test_graph_cross_process.py against a real Postgres)."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.graph import build_graph, resume_workflow, start_workflow
from src.agent.state import State


@pytest.fixture
def graph():
    return build_graph(InMemorySaver())


async def test_low_risk_executes_autonomously_without_suspending(graph):
    result = await start_workflow(graph, "run-low", {"test_risk_tier": "low"})

    assert "__interrupt__" not in result
    assert result["status"] == State.COMPLETED.value
    assert result["final_state"] == State.COMPLETED.value


async def test_every_named_state_is_persisted_in_order_for_the_happy_path(graph):
    await start_workflow(graph, "run-history", {"test_risk_tier": "low"})

    history = [s async for s in graph.aget_state_history({"configurable": {"thread_id": "run-history"}})]
    statuses = [h.values.get("status") for h in reversed(history) if h.values.get("status")]

    assert statuses == [
        State.DIAGNOSING.value,
        State.ACTION_PROPOSED.value,
        State.RISK_CLASSIFIED.value,
        State.EXECUTING.value,
        State.VERIFYING.value,
        State.COMPLETED.value,
    ]


async def test_medium_risk_suspends_then_resumes_on_approval(graph):
    suspended = await start_workflow(graph, "run-medium", {"test_risk_tier": "medium"})

    assert "__interrupt__" in suspended
    assert suspended["status"] == State.AWAITING_APPROVAL.value

    result = await resume_workflow(graph, "run-medium", {"decision": "approved", "approver_id": "alice"})

    assert result["status"] == State.COMPLETED.value
    assert result["approval_decision"] == "approved"
    assert result["approver_id"] == "alice"


async def test_edited_decision_updates_the_proposed_action(graph):
    await start_workflow(graph, "run-edit", {"test_risk_tier": "medium"})

    result = await resume_workflow(
        graph, "run-edit", {"decision": "edited", "edited_action": "clear_cache:service_c"}
    )

    assert result["proposed_action"] == "clear_cache:service_c"
    assert result["status"] == State.COMPLETED.value


async def test_rejected_decision_replans_and_suspends_again(graph):
    await start_workflow(graph, "run-reject", {"test_risk_tier": "medium"})

    result = await resume_workflow(graph, "run-reject", {"decision": "rejected"})

    assert "__interrupt__" in result
    assert result["status"] == State.AWAITING_APPROVAL.value
    assert result["replan_cycles"] == 1


async def test_timeout_decision_escalates_directly(graph):
    await start_workflow(graph, "run-timeout", {"test_risk_tier": "medium"})

    result = await resume_workflow(graph, "run-timeout", {"decision": "timeout"})

    assert "__interrupt__" not in result
    assert result["status"] == State.ESCALATED.value
    assert result["final_state"] == State.ESCALATED.value


async def test_drift_on_resume_replans_instead_of_executing(graph):
    await start_workflow(graph, "run-drift", {"test_risk_tier": "medium", "test_drift_detected": True})

    result = await resume_workflow(graph, "run-drift", {"decision": "approved"})

    # Drift must abort straight to REPLANNING, never reach EXECUTING.
    assert "__interrupt__" in result
    assert result["status"] == State.AWAITING_APPROVAL.value
    assert result["replan_cycles"] == 1
    assert result["drift_detected"] is True


async def test_repeated_rejection_is_capped_at_three_replans_then_escalates(graph):
    await start_workflow(graph, "run-cap", {"test_risk_tier": "medium"})

    r1 = await resume_workflow(graph, "run-cap", {"decision": "rejected"})
    assert r1["replan_cycles"] == 1 and "__interrupt__" in r1

    r2 = await resume_workflow(graph, "run-cap", {"decision": "rejected"})
    assert r2["replan_cycles"] == 2 and "__interrupt__" in r2

    r3 = await resume_workflow(graph, "run-cap", {"decision": "rejected"})
    assert r3["replan_cycles"] == 3
    assert "__interrupt__" not in r3
    assert r3["status"] == State.ESCALATED.value


async def test_verification_failure_rolls_back_and_eventually_escalates(graph):
    result = await start_workflow(
        graph, "run-verify-fail", {"test_risk_tier": "low", "test_verification_passed": False}
    )

    assert result["status"] == State.ESCALATED.value
    assert result["replan_cycles"] == 3
    assert result["rollback_result"] is not None
