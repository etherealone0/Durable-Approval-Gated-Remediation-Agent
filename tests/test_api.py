"""Exercises the FastAPI interface end to
end over real HTTP semantics (httpx + ASGITransport), with InMemorySaver
and in-memory registries so no Postgres is needed. The decision endpoint
triggering real workflow resumption is the main thing under test; a
genuinely separate-process proof lives in test_api_cross_process.py."""

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.graph import build_graph
from src.agent.runtime import AgentRuntimeContext
from src.api.main import create_app
from src.api.run_registry import InMemoryRunRegistry
from tests.helpers import ScriptedReasoner, ScriptedRiskClassifier, build_tool_ctx


@pytest.fixture
async def client():
    graph = build_graph(InMemorySaver())
    context = AgentRuntimeContext(
        tool_ctx=build_tool_ctx(),
        reasoner=ScriptedReasoner(tool="restart_service", target="service_a"),
        risk_classifier=ScriptedRiskClassifier("medium"),
    )
    app = create_app(graph, context, InMemoryRunRegistry())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
        yield c


async def test_create_run_starts_and_suspends_for_medium_risk(client):
    resp = await client.post("/runs", json={"run_id": "api-run-1"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "api-run-1"
    assert body["status"] == "AWAITING_APPROVAL"
    assert body["proposed_action"] == "restart_service:service_a"
    assert body["risk_tier"] == "medium"


async def test_get_run_returns_current_status(client):
    await client.post("/runs", json={"run_id": "api-run-2"})

    resp = await client.get("/runs/api-run-2")

    assert resp.status_code == 200
    assert resp.json()["status"] == "AWAITING_APPROVAL"


async def test_get_run_404_for_unknown_run(client):
    resp = await client.get("/runs/does-not-exist")
    assert resp.status_code == 404


async def test_pending_approval_lists_only_suspended_runs(client):
    await client.post("/runs", json={"run_id": "api-run-pending"})
    await client.post("/runs", json={"run_id": "api-run-pending-2"})

    resp = await client.get("/runs/pending-approval")

    assert resp.status_code == 200
    ids = {r["run_id"] for r in resp.json()}
    assert {"api-run-pending", "api-run-pending-2"} <= ids
    assert all(r["status"] == "AWAITING_APPROVAL" for r in resp.json())


async def test_decision_endpoint_triggers_resumption_and_leaves_pending_queue(client):
    await client.post("/runs", json={"run_id": "api-run-3"})
    assert {r["run_id"] for r in (await client.get("/runs/pending-approval")).json()} >= {"api-run-3"}

    resp = await client.post(
        "/runs/api-run-3/decision", json={"decision": "approved", "approver_id": "alice"}
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"
    remaining = {r["run_id"] for r in (await client.get("/runs/pending-approval")).json()}
    assert "api-run-3" not in remaining


async def test_decision_on_unknown_run_404s(client):
    resp = await client.post("/runs/nope/decision", json={"decision": "approved"})
    assert resp.status_code == 404


async def test_decision_on_a_run_not_awaiting_approval_409s(client):
    await client.post("/runs", json={"run_id": "api-run-4"})
    await client.post("/runs/api-run-4/decision", json={"decision": "approved"})

    resp = await client.post("/runs/api-run-4/decision", json={"decision": "approved"})

    assert resp.status_code == 409


async def test_edited_decision_updates_proposed_action_via_api(client):
    await client.post("/runs", json={"run_id": "api-run-5"})

    resp = await client.post(
        "/runs/api-run-5/decision",
        json={"decision": "edited", "edited_action": "clear_cache:service_c"},
    )

    assert resp.json()["proposed_action"] == "clear_cache:service_c"


async def test_audit_endpoint_returns_full_transition_log(client):
    await client.post("/runs", json={"run_id": "api-run-6"})
    await client.post("/runs/api-run-6/decision", json={"decision": "approved", "approver_id": "bob"})

    resp = await client.get("/runs/api-run-6/audit")

    assert resp.status_code == 200
    records = resp.json()
    assert [r["to_state"] for r in records][:3] == ["DIAGNOSING", "ACTION_PROPOSED", "RISK_CLASSIFIED"]
    approval_records = [r for r in records if r["decision"] == "approved"]
    assert approval_records and approval_records[0]["approver_id"] == "bob"


async def test_audit_endpoint_404_for_unknown_run(client):
    resp = await client.get("/runs/nope/audit")
    assert resp.status_code == 404
