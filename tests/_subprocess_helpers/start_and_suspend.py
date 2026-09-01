"""Standalone script (run as a real OS subprocess, not imported): starts a
workflow that suspends at the approval gate, then exits. Shares no Python
process, memory, or object state with whatever resumes it later.

Usage: python start_and_suspend.py <dsn> <thread_id>
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.agent.graph import build_graph, start_workflow  # noqa: E402
from src.agent.runtime import AgentRuntimeContext  # noqa: E402
from src.durability.checkpointer import postgres_checkpointer, run_async  # noqa: E402
from tests.helpers import ScriptedReasoner, ScriptedRiskClassifier, build_tool_ctx  # noqa: E402


async def main(dsn: str, thread_id: str) -> None:
    context = AgentRuntimeContext(
        tool_ctx=build_tool_ctx(), reasoner=ScriptedReasoner(), risk_classifier=ScriptedRiskClassifier("medium")
    )
    async with postgres_checkpointer(dsn) as checkpointer:
        graph = build_graph(checkpointer)
        result = await start_workflow(graph, thread_id, {}, context)
        print(json.dumps({"status": result.get("status"), "suspended": "__interrupt__" in result}))


if __name__ == "__main__":
    run_async(main(sys.argv[1], sys.argv[2]))
