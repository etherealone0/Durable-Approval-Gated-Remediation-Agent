"""Drives one scenario from data/scenarios.json through the real graph and
produces one results/runs.jsonl-shaped record (PROJECT_SPEC.md section 11),
in one of the three required ablation configurations:

- "full": AsyncPostgresSaver when a DSN is available (durable) with
  revalidation on.
- "no_durability": InMemorySaver — state lives only in this process.
- "no_revalidation": approved actions execute blindly, no drift check
  (src/agent/graph.py's `revalidate=False`).

Uses AnthropicDiagnosisReasoner/AnthropicRiskClassifier when
ANTHROPIC_API_KEY is set, and falls back to a ground-truth-free heuristic
reasoner/classifier otherwise so the suite can still run end-to-end without
an API key (src/eval/run_suite.py reports which one produced a given
results file).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any, Literal

import httpx
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.diagnosis import AnthropicDiagnosisReasoner, DiagnosisReasoner
from src.agent.graph import build_graph, resume_workflow, start_workflow
from src.agent.runtime import AgentRuntimeContext
from src.audit.store import InMemoryAuditStore
from src.env.fault_injector import FaultInjector
from src.env.mock_service.app import create_app
from src.risk.classifier import AnthropicRiskClassifier, RiskClassifier
from src.tools.context import InMemoryRecordsRepository, ToolContext

AblationMode = Literal["full", "no_durability", "no_revalidation"]

SEED_RECORDS = [
    {"id": 1, "kind": "order", "payload": "order-1001: shipped"},
    {"id": 2, "kind": "order", "payload": "order-1002: pending"},
    {"id": 3, "kind": "order", "payload": "order-1003: delivered"},
    {"id": 4, "kind": "session", "payload": "session-a1: active"},
    {"id": 5, "kind": "session", "payload": "session-a2: expired"},
    {"id": 6, "kind": "cache_entry", "payload": "cache-key-42: stale"},
]  # mirrors src/env/db/init.sql's seed rows


class HeuristicDiagnosisReasoner:
    """A ground-truth-free stand-in for AnthropicDiagnosisReasoner, used
    automatically when ANTHROPIC_API_KEY is not set so the scenario suite
    can still be exercised end-to-end. Picks the service with the worst
    single signal from the same read-only sweep an LLM would see, and only
    ever proposes restart_service/clear_cache — never delete_records or
    rollback_deployment. That's an honest limitation (it legitimately
    scores 0 on scenarios whose correct action needs one of those two),
    not a shortcut: it never peeks at scenario ground truth. Swap in
    AnthropicDiagnosisReasoner for real diagnostic judgment and full
    correct_actions coverage.
    """

    async def diagnose(self, observations: dict[str, Any]) -> dict[str, Any]:
        worst_service, worst_signal, worst_score = None, "status", -1.0
        for name, obs in observations["services"].items():
            metrics = obs["metrics"]
            signals = {
                "memory_pct": metrics.get("memory_pct", 0.0),
                "cpu_pct": metrics.get("cpu_pct", 0.0),
                "error_rate_pct": metrics.get("error_rate", 0.0) * 100,
            }
            if "disk" in obs:
                signals["disk_pct"] = obs["disk"]["used_pct"]
            signal, score = max(signals.items(), key=lambda kv: kv[1])
            if score > worst_score:
                worst_service, worst_signal, worst_score = name, signal, score
        return {
            "summary": f"{worst_service} shows the worst signal: {worst_signal}={worst_score:.0f}",
            "root_cause_service": worst_service,
            "_signal": worst_signal,
        }

    async def propose_action(
        self, observations: dict[str, Any], diagnosis: dict[str, Any], replan_context: str | None
    ) -> dict[str, Any]:
        target = diagnosis.get("root_cause_service") or next(iter(observations["services"]))
        tool = "clear_cache" if diagnosis.get("_signal") == "disk_pct" else "restart_service"
        return {
            "tool": tool,
            "target": target,
            "rationale": f"heuristic reasoner: {target}'s worst signal was {diagnosis.get('_signal')}",
            "parameters": {},
        }


class HeuristicRiskClassifier:
    """Rule-of-thumb stand-in for AnthropicRiskClassifier: reversible,
    single-service actions are low; anything with wider or less-reversible
    effect is medium. delete_records/rollback_deployment are classified
    high here too, though the deterministic policy (src/risk/policy.py)
    would force that regardless of what this returns."""

    _TIERS = {
        "clear_cache": "low",
        "scale_service": "low",
        "restart_service": "medium",
        "apply_config_change": "medium",
        "rollback_deployment": "high",
        "delete_records": "high",
    }

    async def classify(self, proposed_action: str, diagnosis: str, rationale: str | None) -> dict[str, Any]:
        tool = proposed_action.split(":", 1)[0]
        tier = self._TIERS.get(tool, "medium")
        return {
            "tier": tier,
            "reversibility": "heuristic guess",
            "blast_radius": "heuristic guess",
            "data_destructiveness": "heuristic guess",
            "rationale": f"heuristic classifier: {tool} defaults to {tier}",
        }


def default_reasoner() -> DiagnosisReasoner:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicDiagnosisReasoner()
    return HeuristicDiagnosisReasoner()


def default_risk_classifier() -> RiskClassifier:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicRiskClassifier()
    return HeuristicRiskClassifier()


def _approx_tokens(*parts: Any) -> int:
    """A rough (~4 chars/token) stand-in for real usage metadata, which
    langchain's with_structured_output() doesn't surface without
    restructuring the reasoner/classifier call sites. Good enough for a
    relative cost_per_run comparison across scenarios and ablation modes,
    not a billing-accurate count."""
    return max(1, len(json.dumps(parts, default=str)) // 4)


class _InstrumentedReasoner:
    def __init__(self, inner: DiagnosisReasoner) -> None:
        self._inner = inner
        self.llm_calls = 0
        self.total_tokens = 0

    async def diagnose(self, observations: dict[str, Any]) -> dict[str, Any]:
        self.llm_calls += 1
        result = await self._inner.diagnose(observations)
        self.total_tokens += _approx_tokens(observations) + _approx_tokens(result)
        return result

    async def propose_action(
        self, observations: dict[str, Any], diagnosis: dict[str, Any], replan_context: str | None
    ) -> dict[str, Any]:
        self.llm_calls += 1
        result = await self._inner.propose_action(observations, diagnosis, replan_context)
        self.total_tokens += _approx_tokens(observations, diagnosis, replan_context) + _approx_tokens(result)
        return result


class _InstrumentedRiskClassifier:
    def __init__(self, inner: RiskClassifier) -> None:
        self._inner = inner
        self.llm_calls = 0
        self.total_tokens = 0

    async def classify(self, proposed_action: str, diagnosis: str, rationale: str | None) -> dict[str, Any]:
        self.llm_calls += 1
        result = await self._inner.classify(proposed_action, diagnosis, rationale)
        self.total_tokens += _approx_tokens(proposed_action, diagnosis, rationale) + _approx_tokens(result)
        return result


def build_sandbox(service_urls: dict[str, str] | None) -> tuple[dict[str, httpx.AsyncClient], ToolContext]:
    if service_urls:
        clients = {name: httpx.AsyncClient(base_url=url) for name, url in service_urls.items()}
    else:
        apps = {
            "service_a": create_app(name="service_a", has_disk=False),
            "service_b": create_app(name="service_b", has_disk=False),
            "service_c": create_app(name="service_c", has_disk=True),
        }
        clients = {
            name: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{name}")
            for name, app in apps.items()
        }
    tool_ctx = ToolContext(clients=clients, records=InMemoryRecordsRepository([dict(r) for r in SEED_RECORDS]))
    return clients, tool_ctx


async def _apply_staleness_change(
    clients: dict[str, httpx.AsyncClient], tool_ctx: ToolContext, change: dict[str, Any]
) -> None:
    action = change["action"]
    if action == "reset":
        resp = await clients[change["service"]].post("/admin/reset")
        resp.raise_for_status()
    elif action == "inject_fault":
        await FaultInjector(clients).inject(change["service"], change["type"], change.get("rate", "high"))
    elif action == "rollback":
        resp = await clients[change["service"]].post("/rollback")
        resp.raise_for_status()
    elif action == "delete_records":
        rows = await tool_ctx.records.get_by_kind(change["kind"], limit=1_000_000)
        await tool_ctx.records.delete([r["id"] for r in rows])
    else:
        raise ValueError(f"unknown staleness action {action!r}")


async def run_scenario(
    scenario: dict[str, Any],
    *,
    mode: AblationMode,
    reasoner: DiagnosisReasoner | None = None,
    risk_classifier: RiskClassifier | None = None,
    checkpointer: Any = None,
    service_urls: dict[str, str] | None = None,
    simulated_approval_wait_seconds: float = 2.0,
    approver_id: str = "eval-harness",
) -> dict[str, Any]:
    """Runs one scenario start-to-finish and returns a runs.jsonl-shaped
    record. A suspended run is approved by this same harness after a short
    simulated wait (simulated_approval_wait_seconds) rather than a real
    human — long enough for compute_idle_ratio to show a real gap between
    wall-clock and active-compute time without the suite taking hours."""
    clients, tool_ctx = build_sandbox(service_urls)
    try:
        await FaultInjector(clients).inject(
            scenario["fault_injection"]["service"],
            scenario["fault_injection"]["type"],
            scenario["fault_injection"]["rate"],
        )

        instrumented_reasoner = _InstrumentedReasoner(reasoner or default_reasoner())
        instrumented_classifier = _InstrumentedRiskClassifier(risk_classifier or default_risk_classifier())
        audit_store = InMemoryAuditStore()
        context = AgentRuntimeContext(
            tool_ctx=tool_ctx,
            reasoner=instrumented_reasoner,
            risk_classifier=instrumented_classifier,
            audit_store=audit_store,
        )

        cp = checkpointer or InMemorySaver()
        graph = build_graph(cp, revalidate=(mode != "no_revalidation"))

        run_id = f"eval-{scenario['id']}-{mode}-{uuid.uuid4().hex[:8]}"
        wall_start = time.perf_counter()
        compute_active = 0.0

        t0 = time.perf_counter()
        result = await start_workflow(graph, run_id, {}, context)
        compute_active += time.perf_counter() - t0

        approval_wait_seconds: float | None = None
        time_to_resume_ms: float | None = None
        applied_staleness = False

        first_suspension = "__interrupt__" in result
        if first_suspension and scenario["category"] == "staleness":
            # Every staleness scenario in data/scenarios.json injects its
            # drift "during_approval_wait" (the only timing the suite
            # currently uses); this is the harness's stand-in for that
            # window.
            for change in scenario["staleness_injection"]["changes"]:
                await _apply_staleness_change(clients, tool_ctx, change)
            applied_staleness = True

        drift_detected_overall: bool | None = None
        while "__interrupt__" in result:
            if simulated_approval_wait_seconds > 0:
                await asyncio.sleep(simulated_approval_wait_seconds)
            approval_wait_seconds = (approval_wait_seconds or 0.0) + simulated_approval_wait_seconds

            decision = {"decision": "approved", "approver_id": approver_id}
            t_resume = time.perf_counter()
            result = await resume_workflow(graph, run_id, decision, context)
            resume_elapsed = time.perf_counter() - t_resume
            compute_active += resume_elapsed
            if time_to_resume_ms is None:
                time_to_resume_ms = resume_elapsed * 1000
            if "drift_detected" in result:
                # A run can suspend, get replanned after drift, and suspend
                # again; a later clean revalidation must not erase that an
                # earlier one caught real drift (staleness_detection_rate
                # depends on this having happened at all, not just last).
                drift_detected_overall = bool(drift_detected_overall) or bool(result["drift_detected"])

        wall_clock_seconds = time.perf_counter() - wall_start
        audit_records = await audit_store.get_run(run_id)

        proposed_actions = [rec["action_proposed"] for rec in audit_records if rec["to_state"] == "ACTION_PROPOSED"]
        executed_actions = [
            rec["action_proposed"] for rec in audit_records if rec["to_state"] == "VERIFYING" and rec["action_proposed"]
        ]
        rollback_invoked = any(rec["to_state"] == "ROLLING_BACK" for rec in audit_records)

        return {
            "run_id": run_id,
            "scenario_id": scenario["id"],
            "ablation_mode": mode,
            "final_state": result.get("final_state") or result.get("status"),
            "proposed_actions": proposed_actions,
            "risk_tier_llm": result.get("risk_tier_llm"),
            "risk_tier_final": result.get("risk_tier_final"),
            "approval_wait_seconds": approval_wait_seconds,
            "time_to_resume_ms": time_to_resume_ms,
            "revalidation_triggered": mode != "no_revalidation" and first_suspension,
            "drift_detected": drift_detected_overall,
            "staleness_injection_applied": applied_staleness,
            "executed_actions": executed_actions,
            "idempotency_collisions": 0,  # only chaos-kill trials can produce these; see src/chaos/harness.py
            "verification_passed": result.get("verification_passed"),
            "rollback_invoked": rollback_invoked,
            "replan_cycles": result.get("replan_cycles", 0),
            "process_kills_survived": 0,  # ditto
            "total_tokens": instrumented_reasoner.total_tokens + instrumented_classifier.total_tokens,
            "llm_calls": instrumented_reasoner.llm_calls + instrumented_classifier.llm_calls,
            "wall_clock_seconds": wall_clock_seconds,
            "compute_seconds_active": compute_active,
            "audit_records": audit_records,
        }
    finally:
        for client in clients.values():
            await client.aclose()
