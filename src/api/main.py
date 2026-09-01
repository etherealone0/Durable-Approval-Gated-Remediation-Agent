"""FastAPI interface (PROJECT_SPEC.md section 10): start/inspect runs,
list the approval queue, and submit decisions that trigger workflow
resumption. create_app() takes its dependencies as arguments so tests can
inject in-memory doubles; build_production_app() wires the real ones
(AsyncPostgresSaver, live Anthropic reasoner/classifier, docker-compose
service URLs) for actual deployment.

The decision endpoint is what makes "resumed by external event" literal
rather than cosmetic: any process holding a graph built against the same
Postgres DSN can call it, including one that never saw this run start
(see tests/test_api_cross_process.py).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from langgraph.graph.state import CompiledStateGraph

from src.agent.graph import build_graph, resume_workflow, start_workflow
from src.agent.runtime import AgentRuntimeContext
from src.api.run_registry import RunRegistry
from src.api.schemas import CreateRunRequest, DecisionRequest, RunStatusResponse


def _to_response(run_id: str, result: dict) -> RunStatusResponse:
    return RunStatusResponse(
        run_id=run_id,
        status=result.get("status", "UNKNOWN"),
        scenario_id=result.get("scenario_id"),
        diagnosis=result.get("diagnosis"),
        proposed_action=result.get("proposed_action"),
        risk_tier=result.get("risk_tier_final"),
        rationale=result.get("action_rationale"),
        final_state=result.get("final_state"),
    )


async def _sync_registry(run_registry: RunRegistry, run_id: str, result: dict) -> None:
    response = _to_response(run_id, result)
    await run_registry.upsert(run_id, response.model_dump(exclude={"run_id"}))


def create_app(
    graph: CompiledStateGraph, context: AgentRuntimeContext, run_registry: RunRegistry
) -> FastAPI:
    app = FastAPI(title="interlock")

    @app.post("/runs", response_model=RunStatusResponse)
    async def create_run(body: CreateRunRequest):
        import uuid

        run_id = body.run_id or str(uuid.uuid4())
        initial_state = dict(body.initial_state)
        if body.scenario_id:
            initial_state["scenario_id"] = body.scenario_id
        result = await start_workflow(graph, run_id, initial_state, context)
        await _sync_registry(run_registry, run_id, result)
        return _to_response(run_id, result)

    @app.get("/runs/pending-approval", response_model=list[RunStatusResponse])
    async def pending_approval():
        rows = await run_registry.list_pending_approval()
        return [RunStatusResponse(**row) for row in rows]

    @app.get("/runs/{run_id}", response_model=RunStatusResponse)
    async def get_run(run_id: str):
        row = await run_registry.get(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown run_id {run_id!r}")
        return RunStatusResponse(**row)

    @app.post("/runs/{run_id}/decision", response_model=RunStatusResponse)
    async def submit_decision(run_id: str, body: DecisionRequest):
        existing = await run_registry.get(run_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"unknown run_id {run_id!r}")
        if existing["status"] != "AWAITING_APPROVAL":
            raise HTTPException(
                status_code=409, detail=f"run {run_id!r} is not awaiting approval (status={existing['status']!r})"
            )
        decision = {
            "decision": body.decision,
            "approver_id": body.approver_id,
            "edited_action": body.edited_action,
        }
        result = await resume_workflow(graph, run_id, decision, context)
        await _sync_registry(run_registry, run_id, result)
        return _to_response(run_id, result)

    @app.get("/runs/{run_id}/audit")
    async def get_audit(run_id: str):
        if await run_registry.get(run_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown run_id {run_id!r}")
        return await context.audit_store.get_run(run_id)

    return app


@asynccontextmanager
async def build_production_app() -> AsyncIterator[FastAPI]:
    """Wires the real dependencies for actual deployment: AsyncPostgresSaver,
    a live Anthropic reasoner/classifier, and the docker-compose services
    from .env.example. Not exercised by the automated test suite (which
    injects in-memory doubles instead); this is what `uvicorn` would run."""
    import os

    import httpx

    from src.agent.diagnosis import AnthropicDiagnosisReasoner
    from src.api.run_registry import PostgresRunRegistry
    from src.audit.store import PostgresAuditStore
    from src.durability.checkpointer import postgres_checkpointer
    from src.env.fault_injector import DEFAULT_SERVICE_ENV_VARS
    from src.risk.classifier import AnthropicRiskClassifier
    from src.tools.context import PostgresExecutedActionsStore, PostgresRecordsRepository, ToolContext

    dsn = os.environ["DATABASE_URL"]
    clients = {
        service: httpx.AsyncClient(base_url=os.environ[env_var])
        for service, env_var in DEFAULT_SERVICE_ENV_VARS.items()
    }
    tool_ctx = ToolContext(
        clients=clients,
        store=PostgresExecutedActionsStore(dsn),
        records=PostgresRecordsRepository(dsn),
    )
    context = AgentRuntimeContext(
        tool_ctx=tool_ctx,
        reasoner=AnthropicDiagnosisReasoner(),
        risk_classifier=AnthropicRiskClassifier(),
        audit_store=PostgresAuditStore(dsn),
    )
    run_registry = PostgresRunRegistry(dsn)

    async with postgres_checkpointer(dsn) as checkpointer:
        graph = build_graph(checkpointer)
        yield create_app(graph, context, run_registry)
