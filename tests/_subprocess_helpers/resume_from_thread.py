"""Standalone script (run as a real OS subprocess, not imported): resumes
a workflow purely by thread_id, with a brand new process, brand new
AsyncPostgresSaver connection, and zero shared memory with whichever
process originally suspended it.

Usage: python resume_from_thread.py <dsn> <thread_id> <decision_json>
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.agent.graph import build_graph, resume_workflow  # noqa: E402
from src.agent.runtime import AgentRuntimeContext  # noqa: E402
from src.durability.checkpointer import postgres_checkpointer, run_async  # noqa: E402
from tests.helpers import ScriptedReasoner, ScriptedRiskClassifier, build_tool_ctx  # noqa: E402


async def main(dsn: str, thread_id: str, decision_json: str) -> None:
    decision = json.loads(decision_json)
    # A fresh context object in a brand new process; revalidate/execute/verify
    # don't touch tool_ctx/reasoner/risk_classifier yet (still stubs, see
    # state.py), but AgentRuntimeContext is required by the graph's
    # context_schema regardless.
    context = AgentRuntimeContext(
        tool_ctx=build_tool_ctx(), reasoner=ScriptedReasoner(), risk_classifier=ScriptedRiskClassifier("medium")
    )
    async with postgres_checkpointer(dsn) as checkpointer:
        graph = build_graph(checkpointer)
        result = await resume_workflow(graph, thread_id, decision, context)
        print(
            json.dumps(
                {
                    "status": result.get("status"),
                    "final_state": result.get("final_state"),
                    "approval_decision": result.get("approval_decision"),
                    "approver_id": result.get("approver_id"),
                }
            )
        )


if __name__ == "__main__":
    run_async(main(sys.argv[1], sys.argv[2], sys.argv[3]))
