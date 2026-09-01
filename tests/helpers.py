"""Shared test doubles for exercising the agent graph without a live LLM
or docker-compose: an in-process ToolContext backed by ASGITransport mock
services, and a scripted DiagnosisReasoner."""

from __future__ import annotations

from typing import Any

import httpx

from src.env.mock_service.app import create_app
from src.tools.context import InMemoryRecordsRepository, ToolContext


def build_tool_ctx(records: list[dict] | None = None) -> ToolContext:
    apps = {
        "service_a": create_app(name="service_a", has_disk=False),
        "service_b": create_app(name="service_b", has_disk=False),
        "service_c": create_app(name="service_c", has_disk=True),
    }
    clients = {
        name: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{name}")
        for name, app in apps.items()
    }
    return ToolContext(clients=clients, records=InMemoryRecordsRepository(records or []))


class ScriptedReasoner:
    """Always proposes the same action regardless of observations; tests
    the graph's plumbing and control flow, not diagnostic reasoning
    quality (that needs a real LLM, exercised separately with an API key
    against the scenario suite)."""

    def __init__(self, tool: str = "restart_service", target: str = "service_a") -> None:
        self.tool = tool
        self.target = target
        self.propose_action_calls: list[str | None] = []

    async def diagnose(self, observations: dict[str, Any]) -> dict[str, Any]:
        return {"summary": f"stub diagnosis pointing at {self.target}", "root_cause_service": self.target}

    async def propose_action(
        self, observations: dict[str, Any], diagnosis: dict[str, Any], replan_context: str | None
    ) -> dict[str, Any]:
        self.propose_action_calls.append(replan_context)
        return {"tool": self.tool, "target": self.target, "rationale": "stub rationale"}
