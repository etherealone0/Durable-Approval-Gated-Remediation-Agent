"""Standalone agent process for the chaos harness (PROJECT_SPEC.md
section 12): starts or resumes a run against real, out-of-process mock
services and a real Postgres checkpointer/audit/executed-actions store,
so killing THIS process (which src/chaos/harness.py does, at the moments
marked by src/chaos/hooks.py) tests genuine durability rather than
in-memory state that would die with it.

Deliberately does not import test doubles from tests/ — src/ must never
depend on tests/ — so the scripted reasoner/classifier below are a small,
local, fully-deterministic equivalent, just enough to drive one specific
action per trial without needing an LLM API key.

Usage:
  python -m src.chaos.agent_process start <dsn> <run_id>
  python -m src.chaos.agent_process resume <dsn> <run_id> <decision_json>

Env: SERVICE_A_URL, SERVICE_B_URL, SERVICE_C_URL (from src.chaos.harness's
own uvicorn subprocesses), CHAOS_TIER, CHAOS_TOOL, CHAOS_TARGET,
CHAOS_KILL_AT (optional; consumed by src.chaos.hooks.mark),
CHAOS_DIE_AFTER_INTERRUPT (optional; os._exit immediately on suspension,
before this script would otherwise print its result and exit cleanly).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

from src.agent.graph import build_graph, resume_workflow, start_workflow
from src.agent.runtime import AgentRuntimeContext
from src.audit.store import PostgresAuditStore
from src.durability.checkpointer import postgres_checkpointer, run_async
from src.env.fault_injector import DEFAULT_SERVICE_ENV_VARS
from src.tools.context import PostgresExecutedActionsStore, PostgresRecordsRepository, ToolContext


class _ScriptedReasoner:
    def __init__(self, tool: str, target: str) -> None:
        self._tool = tool
        self._target = target

    async def diagnose(self, observations: dict[str, Any]) -> dict[str, Any]:
        return {"summary": f"chaos stub diagnosis for {self._target}", "root_cause_service": self._target}

    async def propose_action(
        self, observations: dict[str, Any], diagnosis: dict[str, Any], replan_context: str | None
    ) -> dict[str, Any]:
        return {"tool": self._tool, "target": self._target, "rationale": "chaos stub rationale", "parameters": {}}


class _ScriptedRiskClassifier:
    def __init__(self, tier: str) -> None:
        self._tier = tier

    async def classify(
        self,
        proposed_action: str,
        diagnosis: str,
        rationale: str | None,
        situational: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "tier": self._tier,
            "reversibility": "n/a",
            "blast_radius": "n/a",
            "data_destructiveness": "n/a",
            "rationale": "chaos stub",
        }


def _build_context(dsn: str) -> AgentRuntimeContext:
    clients = {
        service: httpx.AsyncClient(base_url=os.environ[env_var])
        for service, env_var in DEFAULT_SERVICE_ENV_VARS.items()
    }
    tool_ctx = ToolContext(
        clients=clients,
        store=PostgresExecutedActionsStore(dsn),
        records=PostgresRecordsRepository(dsn),
    )
    return AgentRuntimeContext(
        tool_ctx=tool_ctx,
        reasoner=_ScriptedReasoner(os.environ.get("CHAOS_TOOL", "restart_service"), os.environ.get("CHAOS_TARGET", "service_a")),
        risk_classifier=_ScriptedRiskClassifier(os.environ.get("CHAOS_TIER", "low")),
        audit_store=PostgresAuditStore(dsn),
    )


async def _start(dsn: str, run_id: str) -> None:
    context = _build_context(dsn)
    async with postgres_checkpointer(dsn) as checkpointer:
        graph = build_graph(checkpointer)
        result = await start_workflow(graph, run_id, {}, context)
        if "__interrupt__" in result and os.environ.get("CHAOS_DIE_AFTER_INTERRUPT"):
            # Suspension is already durably checkpointed (durability="sync")
            # by the time ainvoke() returns; die with zero chance to do
            # anything else, proving that durability doesn't depend on the
            # process surviving long enough to notice it suspended.
            os._exit(1)
        print(json.dumps({"status": result.get("status"), "suspended": "__interrupt__" in result}))


async def _resume(dsn: str, run_id: str, decision: dict[str, Any]) -> None:
    context = _build_context(dsn)
    async with postgres_checkpointer(dsn) as checkpointer:
        graph = build_graph(checkpointer)
        result = await resume_workflow(graph, run_id, decision, context)
        print(
            json.dumps(
                {
                    "status": result.get("status"),
                    "final_state": result.get("final_state"),
                    "verification_passed": result.get("verification_passed"),
                    "suspended": "__interrupt__" in result,
                }
            )
        )


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "start":
        run_async(_start(sys.argv[2], sys.argv[3]))
    elif mode == "resume":
        run_async(_resume(sys.argv[2], sys.argv[3], json.loads(sys.argv[4])))
    else:
        raise ValueError(f"unknown mode {mode!r}")
