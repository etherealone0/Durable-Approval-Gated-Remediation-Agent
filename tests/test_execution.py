"""Exercises real execution, verification, and rollback: execute_action
dispatches to the real mutating tools, verify_action checks outcomes with
only read-only tools (no ground-truth peeking), and apply_compensation
actually reverses a failed action.

The literal "kill the OS process mid-EXECUTING" proof belongs to the
chaos harness (src/chaos/harness.py) since it needs a real Postgres
checkpointer and separate processes like test_graph_cross_process.py.
Here we prove the specific mechanism prepare_execution relies on: the
idempotency_key is deterministic (run_id + proposed_action), so replaying
execute_action with that same key — exactly what happens when LangGraph
reruns a node from scratch after a crash — cannot double-apply.
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.execution import apply_compensation, execute_action, verify_action
from src.agent.graph import build_graph, resume_workflow, start_workflow
from src.agent.runtime import AgentRuntimeContext
from src.agent.state import State
from src.env.mock_service.state import FaultRate, FaultType
from tests.helpers import ScriptedReasoner, ScriptedRiskClassifier, build_tool_ctx


@pytest.fixture
def graph():
    return build_graph(InMemorySaver())


async def test_execute_action_restart_service_dispatches_correctly():
    ctx = build_tool_ctx()
    await ctx.client("service_a").post("/admin/fault", json={"type": "memory_leak", "rate": "high"})

    outcome = await execute_action(ctx, "restart_service:service_a", {}, "key-1")

    assert outcome["result"]["status"] == "healthy"
    assert outcome["compensation"] == {"type": "noop", "reason": "restart has no reversible side effect"}
    assert (await verify_action(ctx, "restart_service:service_a", outcome["result"])) is True


async def test_execute_action_delete_records_dispatches_correctly():
    ctx = build_tool_ctx(records=[{"id": 1, "kind": "cache_entry", "payload": "x"}])

    outcome = await execute_action(ctx, "delete_records:cache_entry", {}, "key-2")

    assert outcome["result"]["deleted_ids"] == [1]
    assert (await verify_action(ctx, "delete_records:cache_entry", outcome["result"])) is True


async def test_execute_action_rollback_deployment_fixes_health_and_version():
    ctx = build_tool_ctx()
    await ctx.client("service_a").post("/admin/fault", json={"type": "high_error_rate", "rate": "high"})

    outcome = await execute_action(ctx, "rollback_deployment:service_a", {}, "key-3")

    assert outcome["result"]["deployed_version"] == "v1.1.0"
    assert outcome["compensation"] == {"type": "deploy", "args": {"service": "service_a", "version": "v1.2.0"}}
    assert (await verify_action(ctx, "rollback_deployment:service_a", outcome["result"])) is True


async def test_execute_action_scale_service_requires_replicas_parameter():
    ctx = build_tool_ctx()

    outcome = await execute_action(ctx, "scale_service:service_a", {"replicas": 4}, "key-4")

    assert outcome["result"]["replicas"] == 4
    assert outcome["compensation"] == {"type": "scale_service", "args": {"service": "service_a", "replicas": 1}}


async def test_apply_compensation_reverses_a_scale_service_action():
    ctx = build_tool_ctx()
    outcome = await execute_action(ctx, "scale_service:service_a", {"replicas": 5}, "key-5")
    assert (await ctx.client("service_a").get("/metrics")).json()["replicas"] == 5

    result = await apply_compensation(ctx, outcome["compensation"])

    assert result == {"applied": True, "result": {"service": "service_a", "previous_replicas": 5, "replicas": 1}}
    assert (await ctx.client("service_a").get("/metrics")).json()["replicas"] == 1


async def test_apply_compensation_reinserts_deleted_records():
    ctx = build_tool_ctx(records=[{"id": 7, "kind": "session", "payload": "y"}])
    outcome = await execute_action(ctx, "delete_records:session", {}, "key-6")
    assert await ctx.records.get_by_kind("session", 10) == []

    await apply_compensation(ctx, outcome["compensation"])

    assert await ctx.records.get_by_kind("session", 10) == [{"id": 7, "kind": "session", "payload": "y"}]


async def test_apply_compensation_noop_does_not_raise():
    ctx = build_tool_ctx()
    result = await apply_compensation(ctx, {"type": "noop", "reason": "nothing to undo"})
    assert result == {"applied": False, "reason": "nothing to undo"}


async def test_deterministic_key_prevents_double_apply_across_a_simulated_replay():
    """Simulates a process death mid-EXECUTING: prepare_execution's key is
    deterministic (run_id + proposed_action), so a rerun from scratch
    calls execute_action again with the identical key. The mutating
    tool's own idempotency ledger must make the replay a no-op."""
    ctx = build_tool_ctx()
    run_id, proposed_action = "run-crash-42", "restart_service:service_a"
    key = f"{run_id}:{proposed_action}"

    first = await execute_action(ctx, proposed_action, {}, key)
    second = await execute_action(ctx, proposed_action, {}, key)  # the "rerun after crash"

    assert second["result"] == first["result"]
    assert (await ctx.client("service_a").get("/health")).json()["restart_count"] == 1


async def test_end_to_end_low_risk_run_executes_and_verifies_for_real(graph):
    # Redundant replicas so "low" isn't floored to "medium" by
    # src/risk/policy.py's redundancy floor.
    ctx = build_tool_ctx(replicas={"service_b": 2})
    await ctx.client("service_b").post("/admin/fault", json={"type": FaultType.MEMORY_LEAK.value, "rate": FaultRate.HIGH.value})
    context = AgentRuntimeContext(
        tool_ctx=ctx,
        reasoner=ScriptedReasoner(tool="restart_service", target="service_b"),
        risk_classifier=ScriptedRiskClassifier("low"),
    )

    result = await start_workflow(graph, "run-e2e-low", {}, context)

    assert result["status"] == State.COMPLETED.value
    assert result["verification_passed"] is True
    assert result["execution_result"]["status"] == "healthy"
    assert (await ctx.client("service_b").get("/health")).json()["status"] == "healthy"


async def test_end_to_end_medium_risk_run_executes_after_approval(graph):
    ctx = build_tool_ctx(records=[{"id": 1, "kind": "cache_entry", "payload": "x"}])
    context = AgentRuntimeContext(
        tool_ctx=ctx,
        reasoner=ScriptedReasoner(tool="delete_records", target="cache_entry"),
        risk_classifier=ScriptedRiskClassifier("low"),  # overridden to high by policy
    )

    await start_workflow(graph, "run-e2e-medium", {}, context)
    result = await resume_workflow(graph, "run-e2e-medium", {"decision": "approved"}, context)

    assert result["status"] == State.COMPLETED.value
    assert result["risk_tier_final"] == "high"
    assert await ctx.records.get_by_kind("cache_entry", 10) == []
