"""Standalone script (real OS subprocess): builds its own FastAPI app
instance against a real Postgres checkpointer/audit store/run registry and
creates a run through it, then exits. Proves the run is genuinely
resumable by a process that shares nothing but the Postgres DSN.

Usage: python api_create_run.py <dsn> <run_id>
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx  # noqa: E402

from src.agent.graph import build_graph  # noqa: E402
from src.agent.runtime import AgentRuntimeContext  # noqa: E402
from src.api.main import create_app  # noqa: E402
from src.api.run_registry import PostgresRunRegistry  # noqa: E402
from src.audit.store import PostgresAuditStore  # noqa: E402
from src.durability.checkpointer import postgres_checkpointer, run_async  # noqa: E402
from tests.helpers import ScriptedReasoner, ScriptedRiskClassifier, build_tool_ctx  # noqa: E402


async def main(dsn: str, run_id: str) -> None:
    context = AgentRuntimeContext(
        tool_ctx=build_tool_ctx(),
        reasoner=ScriptedReasoner(tool="restart_service", target="service_a"),
        risk_classifier=ScriptedRiskClassifier("medium"),
        audit_store=PostgresAuditStore(dsn),
    )
    async with postgres_checkpointer(dsn) as checkpointer:
        graph = build_graph(checkpointer)
        app = create_app(graph, context, PostgresRunRegistry(dsn))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            resp = await client.post("/runs", json={"run_id": run_id})
            print(json.dumps(resp.json()))


if __name__ == "__main__":
    run_async(main(sys.argv[1], sys.argv[2]))
