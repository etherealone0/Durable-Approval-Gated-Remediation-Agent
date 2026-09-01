"""Standalone script (run as a real OS subprocess, not imported): starts a
workflow that suspends at the approval gate, then exits. Shares no Python
process, memory, or object state with whatever resumes it later.

Usage: python start_and_suspend.py <dsn> <thread_id>
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.agent.graph import build_graph, start_workflow  # noqa: E402
from src.durability.checkpointer import postgres_checkpointer  # noqa: E402


async def main(dsn: str, thread_id: str) -> None:
    async with postgres_checkpointer(dsn) as checkpointer:
        graph = build_graph(checkpointer)
        result = await start_workflow(graph, thread_id, {"test_risk_tier": "medium"})
        print(json.dumps({"status": result.get("status"), "suspended": "__interrupt__" in result}))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
