"""Standalone script (real OS subprocess): builds a brand new, unrelated
FastAPI app instance and submits an approval decision for a run it never
saw start. Shares nothing with api_create_run.py except the Postgres DSN
— this is the literal "different machine" proof for the decision
endpoint (PROJECT_SPEC.md section 10).

Usage: python api_submit_decision.py <dsn> <run_id>
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
    # A fresh ToolContext (independent mock services) is fine here: the
    # scripted reasoner's target starts at the same healthy baseline in
    # both processes, so revalidate finds no drift, same as production
    # would if both processes talked to the same docker-compose services.
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
            resp = await client.post(
                f"/runs/{run_id}/decision", json={"decision": "approved", "approver_id": "remote-operator"}
            )
            print(json.dumps(resp.json()))


if __name__ == "__main__":
    run_async(main(sys.argv[1], sys.argv[2]))
