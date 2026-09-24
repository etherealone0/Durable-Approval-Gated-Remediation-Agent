"""Exercises the audit trail: every node
transition is logged with actor/decision/rationale, and the full run can
be reconstructed from the log alone."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.graph import build_graph, resume_workflow, start_workflow
from src.agent.runtime import AgentRuntimeContext
from src.audit.replay import is_complete_chain, replay_run
from src.audit.store import InMemoryAuditStore
from tests.helpers import ScriptedReasoner, ScriptedRiskClassifier, build_tool_ctx


@pytest.fixture
def graph():
    return build_graph(InMemorySaver())


def make_context(tier="low", tool="restart_service", target="service_a", replicas=None):
    return AgentRuntimeContext(
        tool_ctx=build_tool_ctx(replicas=replicas),
        reasoner=ScriptedReasoner(tool=tool, target=target),
        risk_classifier=ScriptedRiskClassifier(tier),
        audit_store=InMemoryAuditStore(),
    )


async def test_low_risk_run_logs_every_transition(graph):
    context = make_context(tier="low", replicas={"service_a": 2})
    await start_workflow(graph, "run-audit-low", {}, context)

    records = await context.audit_store.get_run("run-audit-low")

    assert replay_run(records) == [
        "DIAGNOSING",
        "ACTION_PROPOSED",
        "RISK_CLASSIFIED",
        "EXECUTING",
        "EXECUTING",
        "VERIFYING",
        "COMPLETED",
    ]
    assert is_complete_chain(records)
    assert all(r["actor"] == "agent" for r in records[:-1])
    assert records[-1]["actor"] == "system"  # completed
    assert records[0]["from_state"] is None  # CREATED, nothing logged before it


async def test_audit_captures_risk_tier_and_action_on_relevant_records(graph):
    context = make_context(tier="low", replicas={"service_a": 2})
    await start_workflow(graph, "run-audit-fields", {}, context)

    records = await context.audit_store.get_run("run-audit-fields")
    by_state = {r["to_state"]: r for r in records}

    assert by_state["ACTION_PROPOSED"]["action_proposed"] == "restart_service:service_a"
    assert by_state["ACTION_PROPOSED"]["rationale"] == "stub rationale"
    assert by_state["RISK_CLASSIFIED"]["risk_tier"] == "low"


async def test_human_approval_decision_is_logged_with_human_actor(graph):
    context = make_context(tier="medium")
    await start_workflow(graph, "run-audit-approve", {}, context)
    await resume_workflow(graph, "run-audit-approve", {"decision": "approved", "approver_id": "alice"}, context)

    records = await context.audit_store.get_run("run-audit-approve")
    approval_records = [r for r in records if r["decision"] == "approved"]

    assert len(approval_records) == 1
    assert approval_records[0]["actor"] == "human"
    assert approval_records[0]["approver_id"] == "alice"


async def test_timeout_decision_is_logged_with_system_actor(graph):
    context = make_context(tier="medium")
    await start_workflow(graph, "run-audit-timeout", {}, context)
    await resume_workflow(graph, "run-audit-timeout", {"decision": "timeout"}, context)

    records = await context.audit_store.get_run("run-audit-timeout")
    timeout_records = [r for r in records if r["decision"] == "timeout"]

    assert len(timeout_records) == 1
    assert timeout_records[0]["actor"] == "system"


async def test_rejected_run_logs_replan_context_as_rationale(graph):
    context = make_context(tier="medium")
    await start_workflow(graph, "run-audit-reject", {}, context)
    await resume_workflow(graph, "run-audit-reject", {"decision": "rejected"}, context)

    records = await context.audit_store.get_run("run-audit-reject")
    replan_record = next(r for r in records if r["to_state"] == "REPLANNING")

    assert "rejected" in replan_record["rationale"]


async def test_idempotency_key_is_logged_once_generated(graph):
    context = make_context(tier="low")
    await start_workflow(graph, "run-audit-key", {}, context)

    records = await context.audit_store.get_run("run-audit-key")
    executing_records = [r for r in records if r["to_state"] == "EXECUTING"]

    assert all(r["idempotency_key"] == "run-audit-key:restart_service:service_a" for r in executing_records)


def test_is_complete_chain_detects_a_gap():
    records = [
        {"timestamp": "1", "from_state": None, "to_state": "DIAGNOSING"},
        {"timestamp": "2", "from_state": "DIAGNOSING", "to_state": "ACTION_PROPOSED"},
        {"timestamp": "3", "from_state": "RISK_CLASSIFIED", "to_state": "EXECUTING"},  # gap: skips ACTION_PROPOSED -> RISK_CLASSIFIED
    ]
    assert is_complete_chain(records) is False


def test_is_complete_chain_true_for_a_clean_sequence():
    records = [
        {"timestamp": "1", "from_state": None, "to_state": "DIAGNOSING"},
        {"timestamp": "2", "from_state": "DIAGNOSING", "to_state": "ACTION_PROPOSED"},
        {"timestamp": "3", "from_state": "ACTION_PROPOSED", "to_state": "RISK_CLASSIFIED"},
    ]
    assert is_complete_chain(records) is True
