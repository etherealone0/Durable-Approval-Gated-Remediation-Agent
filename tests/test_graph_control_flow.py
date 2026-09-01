"""Exercises every named state and transition in PROJECT_SPEC.md section
4's state machine diagram, using InMemorySaver (unit-test only checkpointer
per section 5; durability itself is proven separately in
test_graph_cross_process.py against a real Postgres) and scripted
DiagnosisReasoner/RiskClassifier doubles (real LLM reasoning is exercised
separately against the scenario suite with an API key, not in this
control-flow suite)."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.graph import build_graph, resume_workflow, start_workflow
from src.agent.runtime import AgentRuntimeContext
from src.agent.state import State
from tests.helpers import ScriptedReasoner, ScriptedRiskClassifier, build_tool_ctx


@pytest.fixture
def graph():
    return build_graph(InMemorySaver())


@pytest.fixture
def make_context():
    def _make(tier: str = "low", tool: str = "restart_service", target: str = "service_a"):
        return AgentRuntimeContext(
            tool_ctx=build_tool_ctx(),
            reasoner=ScriptedReasoner(tool=tool, target=target),
            risk_classifier=ScriptedRiskClassifier(tier),
        )

    return _make


async def test_low_risk_executes_autonomously_without_suspending(graph, make_context):
    context = make_context(tier="low")
    result = await start_workflow(graph, "run-low", {}, context)

    assert "__interrupt__" not in result
    assert result["status"] == State.COMPLETED.value
    assert result["final_state"] == State.COMPLETED.value
    assert result["proposed_action"] == "restart_service:service_a"
    assert result["diagnosis"]
    assert result["state_fingerprint"]
    assert result["risk_tier_llm"] == "low"
    assert result["risk_tier_final"] == "low"


async def test_every_named_state_is_persisted_in_order_for_the_happy_path(graph, make_context):
    await start_workflow(graph, "run-history", {}, make_context(tier="low"))

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


async def test_medium_risk_suspends_then_resumes_on_approval(graph, make_context):
    context = make_context(tier="medium")
    suspended = await start_workflow(graph, "run-medium", {}, context)

    assert "__interrupt__" in suspended
    assert suspended["status"] == State.AWAITING_APPROVAL.value

    result = await resume_workflow(
        graph, "run-medium", {"decision": "approved", "approver_id": "alice"}, context
    )

    assert result["status"] == State.COMPLETED.value
    assert result["approval_decision"] == "approved"
    assert result["approver_id"] == "alice"


async def test_deterministic_policy_overrides_llm_tier_for_delete_records(graph, make_context):
    context = make_context(tier="low", tool="delete_records", target="cache_entry")

    result = await start_workflow(graph, "run-policy-delete", {}, context)

    assert result["risk_tier_llm"] == "low"
    assert result["risk_tier_final"] == "high"
    assert "__interrupt__" in result  # forced to high, so it must suspend despite the LLM saying low


async def test_deterministic_policy_overrides_llm_tier_for_rollback_deployment(graph, make_context):
    context = make_context(tier="medium", tool="rollback_deployment", target="service_a")

    result = await start_workflow(graph, "run-policy-rollback", {}, context)

    assert result["risk_tier_llm"] == "medium"
    assert result["risk_tier_final"] == "high"


async def test_policy_does_not_override_non_forced_tools(graph, make_context):
    context = make_context(tier="high", tool="restart_service", target="service_a")

    result = await start_workflow(graph, "run-policy-noop", {}, context)

    assert result["risk_tier_llm"] == "high"
    assert result["risk_tier_final"] == "high"


async def test_edited_decision_updates_the_proposed_action(graph, make_context):
    context = make_context(tier="medium")
    await start_workflow(graph, "run-edit", {}, context)

    result = await resume_workflow(
        graph, "run-edit", {"decision": "edited", "edited_action": "clear_cache:service_c"}, context
    )

    assert result["proposed_action"] == "clear_cache:service_c"
    assert result["status"] == State.COMPLETED.value


async def test_rejected_decision_replans_with_a_fresh_proposal_and_suspends_again(graph, make_context):
    context = make_context(tier="medium")
    await start_workflow(graph, "run-reject", {}, context)

    result = await resume_workflow(graph, "run-reject", {"decision": "rejected"}, context)

    assert "__interrupt__" in result
    assert result["status"] == State.AWAITING_APPROVAL.value
    assert result["replan_cycles"] == 1
    # propose_action must have been called again (once for the original
    # proposal with no replan context, once more explaining the rejection).
    assert context.reasoner.propose_action_calls == [None, result["replan_context"]]
    assert "rejected" in result["replan_context"]


async def test_timeout_decision_escalates_directly(graph, make_context):
    context = make_context(tier="medium")
    await start_workflow(graph, "run-timeout", {}, context)

    result = await resume_workflow(graph, "run-timeout", {"decision": "timeout"}, context)

    assert "__interrupt__" not in result
    assert result["status"] == State.ESCALATED.value
    assert result["final_state"] == State.ESCALATED.value



async def test_repeated_rejection_is_capped_at_three_replans_then_escalates(graph, make_context):
    context = make_context(tier="medium")
    await start_workflow(graph, "run-cap", {}, context)

    r1 = await resume_workflow(graph, "run-cap", {"decision": "rejected"}, context)
    assert r1["replan_cycles"] == 1 and "__interrupt__" in r1

    r2 = await resume_workflow(graph, "run-cap", {"decision": "rejected"}, context)
    assert r2["replan_cycles"] == 2 and "__interrupt__" in r2

    r3 = await resume_workflow(graph, "run-cap", {"decision": "rejected"}, context)
    assert r3["replan_cycles"] == 3
    assert "__interrupt__" not in r3
    assert r3["status"] == State.ESCALATED.value


async def test_verification_failure_rolls_back_and_eventually_escalates(graph, make_context):
    context = make_context(tier="low")
    result = await start_workflow(graph, "run-verify-fail", {"test_verification_passed": False}, context)

    assert result["status"] == State.ESCALATED.value
    assert result["replan_cycles"] == 3
    assert result["rollback_result"] is not None
    assert "did not fix" in result["replan_context"]
