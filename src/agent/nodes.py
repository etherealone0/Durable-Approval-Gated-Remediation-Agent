"""Node implementations for the state machine in PROJECT_SPEC.md section
4. See state.py's module docstring for which nodes are still stubs and
which future prompt replaces them with real logic.
"""

from __future__ import annotations

import hashlib
from typing import Any

from langgraph.types import interrupt

from src.agent.state import MAX_REPLAN_CYCLES, AgentState, State


async def diagnose(state: AgentState) -> dict[str, Any]:
    diagnosis = state.get("diagnosis") or "stub diagnosis (real tool-calling diagnosis: Prompt 6)"
    return {"status": State.DIAGNOSING.value, "diagnosis": diagnosis}


async def propose_action(state: AgentState) -> dict[str, Any]:
    proposed_action = state.get("proposed_action") or "restart_service:service_a"
    fingerprint = hashlib.sha256(f"{state['diagnosis']}:{proposed_action}".encode()).hexdigest()[:16]
    return {
        "status": State.ACTION_PROPOSED.value,
        "proposed_action": proposed_action,
        "state_fingerprint": fingerprint,
    }


async def classify_risk(state: AgentState) -> dict[str, Any]:
    risk_tier = state.get("test_risk_tier") or "low"
    return {"status": State.RISK_CLASSIFIED.value, "risk_tier_llm": risk_tier, "risk_tier_final": risk_tier}


def route_after_risk_classification(state: AgentState) -> str:
    return "execute" if state["risk_tier_final"] == "low" else "mark_awaiting_approval"


async def mark_awaiting_approval(state: AgentState) -> dict[str, Any]:
    return {"status": State.AWAITING_APPROVAL.value}


async def await_approval(state: AgentState) -> dict[str, Any]:
    """Suspends the workflow. Resumed via Command(resume=decision) where
    decision is {"decision": "approved"|"edited"|"rejected"|"timeout",
    "edited_action": str | None, "approver_id": str | None}."""
    decision = interrupt(
        {
            "run_id": state["run_id"],
            "diagnosis": state["diagnosis"],
            "proposed_action": state["proposed_action"],
            "risk_tier": state["risk_tier_final"],
        }
    )
    update: dict[str, Any] = {
        "approval_decision": decision["decision"],
        "approver_id": decision.get("approver_id"),
    }
    if decision.get("edited_action"):
        update["proposed_action"] = decision["edited_action"]
    return update


def route_after_approval(state: AgentState) -> str:
    decision = state["approval_decision"]
    if decision in ("approved", "edited"):
        return "revalidate"
    if decision == "rejected":
        return "replan"
    return "escalate"  # timeout (SLA breach)


async def revalidate(state: AgentState) -> dict[str, Any]:
    drift_detected = bool(state.get("test_drift_detected", False))
    return {"status": State.REVALIDATING.value, "drift_detected": drift_detected}


def route_after_revalidation(state: AgentState) -> str:
    return "replan" if state["drift_detected"] else "execute"


async def execute(state: AgentState) -> dict[str, Any]:
    idempotency_key = f"{state['run_id']}:{state['proposed_action']}"
    return {
        "status": State.EXECUTING.value,
        "idempotency_key": idempotency_key,
        "execution_result": {"action": state["proposed_action"]},
    }


async def verify(state: AgentState) -> dict[str, Any]:
    verification_passed = bool(state.get("test_verification_passed", True))
    return {"status": State.VERIFYING.value, "verification_passed": verification_passed}


def route_after_verification(state: AgentState) -> str:
    return "completed" if state["verification_passed"] else "rolling_back"


async def rolling_back(state: AgentState) -> dict[str, Any]:
    return {"status": State.ROLLING_BACK.value, "rollback_result": {"compensated": state.get("proposed_action")}}


async def rolled_back(state: AgentState) -> dict[str, Any]:
    return {"status": State.ROLLED_BACK.value}


async def replan(state: AgentState) -> dict[str, Any]:
    return {"status": State.REPLANNING.value, "replan_cycles": state.get("replan_cycles", 0) + 1}


def route_after_replan(state: AgentState) -> str:
    return "escalate" if state["replan_cycles"] >= MAX_REPLAN_CYCLES else "propose_action"


async def completed(state: AgentState) -> dict[str, Any]:
    return {"status": State.COMPLETED.value, "final_state": State.COMPLETED.value}


async def escalate(state: AgentState) -> dict[str, Any]:
    return {"status": State.ESCALATED.value, "final_state": State.ESCALATED.value}
