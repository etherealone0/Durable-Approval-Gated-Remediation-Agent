"""Shared test doubles for exercising the agent graph without a live LLM
or docker-compose: an in-process ToolContext backed by ASGITransport mock
services, a scripted DiagnosisReasoner, and a scripted RiskClassifier."""

from __future__ import annotations

from typing import Any

import httpx

from src.env.mock_service.app import create_app
from src.tools.context import InMemoryRecordsRepository, ToolContext


def build_tool_ctx(records: list[dict] | None = None, replicas: dict[str, int] | None = None) -> ToolContext:
    """`replicas` overrides a service's starting replica count from the
    mock's default of 1 — needed for any test exercising a genuinely-low
    (not floored) risk tier, since src/risk/policy.py's redundancy floor
    forces "low" up to "medium" for a target with fewer than 2 replicas."""
    replicas = replicas or {}
    apps = {
        "service_a": create_app(name="service_a", has_disk=False, replicas=replicas.get("service_a", 1)),
        "service_b": create_app(name="service_b", has_disk=False, replicas=replicas.get("service_b", 1)),
        "service_c": create_app(name="service_c", has_disk=True, replicas=replicas.get("service_c", 1)),
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

    def __init__(
        self,
        tool: str = "restart_service",
        target: str = "service_a",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        self.tool = tool
        self.target = target
        self.parameters = parameters or {}
        self.propose_action_calls: list[str | None] = []

    async def diagnose(self, observations: dict[str, Any]) -> dict[str, Any]:
        return {"summary": f"stub diagnosis pointing at {self.target}", "root_cause_service": self.target}

    async def propose_action(
        self, observations: dict[str, Any], diagnosis: dict[str, Any], replan_context: str | None
    ) -> dict[str, Any]:
        self.propose_action_calls.append(replan_context)
        return {
            "tool": self.tool,
            "target": self.target,
            "rationale": "stub rationale",
            "parameters": self.parameters,
        }


class ScriptedRiskClassifier:
    """Always returns the same tier; tests the graph's plumbing and the
    deterministic policy override, not risk-judgment quality."""

    def __init__(self, tier: str = "low") -> None:
        self.tier = tier

    async def classify(
        self,
        proposed_action: str,
        diagnosis: str,
        rationale: str | None,
        situational: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "reversibility": "stub",
            "blast_radius": "stub",
            "data_destructiveness": "stub",
            "rationale": "stub rationale",
        }
