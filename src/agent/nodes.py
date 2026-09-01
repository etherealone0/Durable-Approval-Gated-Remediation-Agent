"""Node implementations for the state machine in PROJECT_SPEC.md section
4. See state.py's module docstring for which nodes are still stubs and
which future prompt replaces them with real logic.
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import get_runtime
from langgraph.types import interrupt

from src.agent.execution import apply_compensation, execute_action, verify_action
from src.agent.observations import gather_observations
from src.agent.runtime import AgentRuntimeContext
from src.agent.state import MAX_REPLAN_CYCLES, AgentState, State
from src.chaos.hooks import mark
from src.revalidation.fingerprint import compute_fingerprint
from src.risk.policy import apply_policy


async def diagnose(state: AgentState) -> dict[str, Any]:
    await mark("DIAGNOSING")
    runtime = get_runtime(AgentRuntimeContext)
    observations = await gather_observations(runtime.context.tool_ctx)
    diagnosis = await runtime.context.reasoner.diagnose(observations)
    return {"status": State.DIAGNOSING.value, "observations": observations, "diagnosis": diagnosis["summary"]}


async def propose_action(state: AgentState) -> dict[str, Any]:
    """Also reached directly from REPLANNING (skipping DIAGNOSING), so this
    must be able to produce a fresh proposal against the existing
    diagnosis rather than assuming it's always the first attempt."""
    runtime = get_runtime(AgentRuntimeContext)
    action = await runtime.context.reasoner.propose_action(
        state["observations"], {"summary": state["diagnosis"]}, state.get("replan_context")
    )
    proposed_action = f"{action['tool']}:{action['target']}"
    fingerprint = compute_fingerprint(state["observations"], proposed_action)
    return {
        "status": State.ACTION_PROPOSED.value,
        "proposed_action": proposed_action,
        "action_parameters": action.get("parameters", {}),
        "action_rationale": action["rationale"],
        "state_fingerprint": fingerprint,
    }


async def classify_risk(state: AgentState) -> dict[str, Any]:
    runtime = get_runtime(AgentRuntimeContext)
    tool, _target = state["proposed_action"].split(":", 1)
    result = await runtime.context.risk_classifier.classify(
        state["proposed_action"], state["diagnosis"], state.get("action_rationale")
    )
    llm_tier = result["tier"]
    final_tier = apply_policy(tool, llm_tier)
    return {
        "status": State.RISK_CLASSIFIED.value,
        "risk_tier_llm": llm_tier,
        "risk_tier_final": final_tier,
        "risk_rationale": result["rationale"],
    }


def route_after_risk_classification(state: AgentState) -> str:
    return "prepare_execution" if state["risk_tier_final"] == "low" else "mark_awaiting_approval"


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
        new_action = decision["edited_action"]
        update["proposed_action"] = new_action
        # An edit changes what the fingerprint is even about (a different
        # tool/target), so the old one can't be compared against a fresh
        # sweep in revalidate — it would always look like drift, even with
        # no environment change at all. Re-anchor it to the edited action
        # against the best snapshot we have (the proposal-time sweep).
        update["state_fingerprint"] = compute_fingerprint(state["observations"], new_action)
    return update


def route_after_approval(state: AgentState) -> str:
    decision = state["approval_decision"]
    if decision in ("approved", "edited"):
        return "revalidate"
    if decision == "rejected":
        return "replan"
    return "escalate"  # timeout (SLA breach)


def route_after_approval_no_revalidation(state: AgentState) -> str:
    """The --no-revalidation ablation (PROJECT_SPEC.md section 11): skips
    straight to prepare_execution on approval, executing the proposed
    action blindly against whatever the world looks like now, with no
    drift check at all. Used to measure how many incorrect actions
    revalidation actually prevents (src/eval/runner.py)."""
    decision = state["approval_decision"]
    if decision in ("approved", "edited"):
        return "prepare_execution"
    if decision == "rejected":
        return "replan"
    return "escalate"  # timeout (SLA breach)


async def revalidate(state: AgentState) -> dict[str, Any]:
    """Re-runs the read-only diagnostic sweep and recomputes the
    state_fingerprint against the same proposed_action; a mismatch means
    the world changed while this run was suspended waiting for approval,
    so the approved action must not be blindly executed (PROJECT_SPEC.md
    section 7)."""
    runtime = get_runtime(AgentRuntimeContext)
    fresh_observations = await gather_observations(runtime.context.tool_ctx)
    fresh_fingerprint = compute_fingerprint(fresh_observations, state["proposed_action"])
    drift_detected = fresh_fingerprint != state["state_fingerprint"]
    return {
        "status": State.REVALIDATING.value,
        "observations": fresh_observations,
        "state_fingerprint": fresh_fingerprint,
        "drift_detected": drift_detected,
    }


def route_after_revalidation(state: AgentState) -> str:
    return "replan" if state["drift_detected"] else "prepare_execution"


async def prepare_execution(state: AgentState) -> dict[str, Any]:
    """A deterministic key derived from run_id + proposed_action, not a
    random one: if the process dies mid-EXECUTING, LangGraph reruns this
    whole node from scratch on resume, and it must regenerate the exact
    same key so the mutating tool's own idempotency check (section 8)
    recognizes the retry and no-ops instead of double-applying."""
    idempotency_key = f"{state['run_id']}:{state['proposed_action']}"
    return {"status": State.EXECUTING.value, "idempotency_key": idempotency_key}


async def execute(state: AgentState) -> dict[str, Any]:
    runtime = get_runtime(AgentRuntimeContext)
    outcome = await execute_action(
        runtime.context.tool_ctx,
        state["proposed_action"],
        state.get("action_parameters") or {},
        state["idempotency_key"],
    )
    await mark("EXECUTING")  # after the tool call's real side effect, before this checkpoints
    return {
        "status": State.EXECUTING.value,
        "execution_result": outcome["result"],
        "compensation": outcome["compensation"],
    }


async def verify(state: AgentState) -> dict[str, Any]:
    runtime = get_runtime(AgentRuntimeContext)
    verification_passed = await verify_action(
        runtime.context.tool_ctx, state["proposed_action"], state["execution_result"]
    )
    return {"status": State.VERIFYING.value, "verification_passed": verification_passed}


def route_after_verification(state: AgentState) -> str:
    return "completed" if state["verification_passed"] else "rolling_back"


async def rolling_back(state: AgentState) -> dict[str, Any]:
    runtime = get_runtime(AgentRuntimeContext)
    rollback_result = await apply_compensation(runtime.context.tool_ctx, state["compensation"])
    await mark("ROLLING_BACK")  # after the compensation's real side effect, before this checkpoints
    return {"status": State.ROLLING_BACK.value, "rollback_result": rollback_result}


async def rolled_back(state: AgentState) -> dict[str, Any]:
    return {"status": State.ROLLED_BACK.value}


def _replan_reason(state: AgentState) -> str:
    action = state.get("proposed_action")
    if state.get("approval_decision") == "rejected":
        return f"a human reviewer rejected the proposed action ({action}); propose a different one"
    if state.get("drift_detected"):
        return f"the environment changed since {action} was proposed, invalidating that diagnosis's basis"
    if state.get("verification_passed") is False:
        return f"{action} was executed and rolled back because it did not fix the problem"
    return f"{action} did not resolve the incident"


async def replan(state: AgentState) -> dict[str, Any]:
    return {
        "status": State.REPLANNING.value,
        "replan_cycles": state.get("replan_cycles", 0) + 1,
        "replan_context": _replan_reason(state),
    }


def route_after_replan(state: AgentState) -> str:
    return "escalate" if state["replan_cycles"] >= MAX_REPLAN_CYCLES else "propose_action"


async def completed(state: AgentState) -> dict[str, Any]:
    return {"status": State.COMPLETED.value, "final_state": State.COMPLETED.value}


async def escalate(state: AgentState) -> dict[str, Any]:
    return {"status": State.ESCALATED.value, "final_state": State.ESCALATED.value}
