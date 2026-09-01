"""Exercises real staleness revalidation (PROJECT_SPEC.md section 7): the
environment is genuinely mutated — through the same mock-service HTTP
surface the fault injector uses — while a run sits suspended at
AWAITING_APPROVAL, mirroring the staleness scenarios in
data/scenarios.json (self-recovery, an unrelated fault, a concurrent
rollback, a concurrent deletion). revalidate must catch drift on the
proposed action's own target and route to REPLANNING instead of blindly
executing, while ignoring changes the action never depended on.
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.graph import build_graph, resume_workflow, start_workflow
from src.agent.runtime import AgentRuntimeContext
from src.agent.state import State
from src.env.mock_service.state import FaultRate, FaultType
from src.tools.context import InMemoryRecordsRepository
from tests.helpers import ScriptedReasoner, ScriptedRiskClassifier, build_tool_ctx


@pytest.fixture
def graph():
    return build_graph(InMemorySaver())


def make_context(tool_ctx, tool, target, tier="medium"):
    return AgentRuntimeContext(
        tool_ctx=tool_ctx,
        reasoner=ScriptedReasoner(tool=tool, target=target),
        risk_classifier=ScriptedRiskClassifier(tier),
    )


async def test_no_drift_when_nothing_changes_proceeds_to_execute(graph):
    ctx = build_tool_ctx()
    context = make_context(ctx, "restart_service", "service_a")

    await start_workflow(graph, "run-no-drift", {}, context)
    result = await resume_workflow(graph, "run-no-drift", {"decision": "approved"}, context)

    assert result["drift_detected"] is False
    assert result["status"] == State.COMPLETED.value
    assert result["execution_result"] is not None


async def test_drift_when_approved_target_self_recovers_blocks_execution(graph):
    ctx = build_tool_ctx()
    context = make_context(ctx, "restart_service", "service_a")

    await ctx.client("service_a").post("/admin/fault", json={"type": "memory_leak", "rate": "high"})
    await start_workflow(graph, "run-self-recover", {}, context)

    # Someone else fixes it while the approval sits in the queue.
    await ctx.client("service_a").post("/restart")

    result = await resume_workflow(graph, "run-self-recover", {"decision": "approved"}, context)

    assert result["drift_detected"] is True
    assert result.get("execution_result") is None  # never reached EXECUTING
    assert "__interrupt__" in result  # replanned, then suspended again
    assert result["replan_cycles"] == 1
    assert context.reasoner.propose_action_calls == [None, result["replan_context"]]


async def test_unrelated_service_change_is_not_material_drift(graph):
    ctx = build_tool_ctx()
    context = make_context(ctx, "restart_service", "service_a")

    await start_workflow(graph, "run-unrelated", {}, context)

    # service_b degrades while service_a's approval is pending; the
    # proposed action (restart_service:service_a) never depended on it.
    resp = await ctx.client("service_b").post("/admin/fault", json={"type": "cpu_spike", "rate": "high"})
    assert resp.status_code == 200
    assert (await ctx.client("service_b").get("/health")).json()["status"] == "unhealthy"

    result = await resume_workflow(graph, "run-unrelated", {"decision": "approved"}, context)

    assert result["drift_detected"] is False
    assert result["status"] == State.COMPLETED.value


async def test_concurrent_rollback_is_detected_as_drift_preventing_double_rollback(graph):
    ctx = build_tool_ctx()
    context = make_context(ctx, "rollback_deployment", "service_a")

    await start_workflow(graph, "run-double-rollback", {}, context)

    # Another operator manually rolls back service_a while this approval
    # is pending. Blindly executing the approved rollback again would pop
    # the version history a second time and land one version too far back.
    await ctx.client("service_a").post("/rollback")
    version_after_manual_rollback = (await ctx.client("service_a").get("/version")).json()["deployed_version"]

    result = await resume_workflow(graph, "run-double-rollback", {"decision": "approved"}, context)

    assert result["drift_detected"] is True
    assert result.get("execution_result") is None
    final_version = (await ctx.client("service_a").get("/version")).json()["deployed_version"]
    assert final_version == version_after_manual_rollback  # not rolled back a second time


async def test_concurrent_deletion_is_detected_as_drift():
    graph_ = build_graph(InMemorySaver())
    records = InMemoryRecordsRepository([{"id": 1, "kind": "cache_entry", "payload": "x"}])
    ctx = build_tool_ctx()
    ctx.records = records
    context = make_context(ctx, "delete_records", "cache_entry")

    await start_workflow(graph_, "run-concurrent-delete", {}, context)

    # Someone else already deleted the row this approval is meant to clean up.
    await records.delete([1])

    result = await resume_workflow(graph_, "run-concurrent-delete", {"decision": "approved"}, context)

    assert result["drift_detected"] is True
    assert result.get("execution_result") is None


async def test_disk_cleared_by_other_operator_is_detected_as_drift(graph):
    ctx = build_tool_ctx()
    context = make_context(ctx, "clear_cache", "service_c")

    await ctx.client("service_c").post("/admin/fault", json={"type": FaultType.DISK_FULL.value, "rate": FaultRate.HIGH.value})
    await start_workflow(graph, "run-disk-cleared", {}, context)

    await ctx.client("service_c").post("/disk/clean")

    result = await resume_workflow(graph, "run-disk-cleared", {"decision": "approved"}, context)

    assert result["drift_detected"] is True
    assert result.get("execution_result") is None
