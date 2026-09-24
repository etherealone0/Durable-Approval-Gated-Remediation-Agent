"""Wraps every graph node so its transition is written to the audit log
without each node having to do its own bookkeeping. await_approval is the
one node that can suspend mid-call (interrupt()) — while suspended, the
wrapped call never reaches the audit write, and re-runs from the top on
resume, so a record is only written once the transition actually completes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from langgraph.runtime import get_runtime

from src.agent.runtime import AgentRuntimeContext
from src.audit.schema import Actor, AuditRecord

NODE_ACTORS: dict[str, Actor] = {
    "diagnose": "agent",
    "propose_action": "agent",
    "classify_risk": "agent",
    "mark_awaiting_approval": "system",
    "revalidate": "agent",
    "prepare_execution": "agent",
    "execute": "agent",
    "verify": "agent",
    "rolling_back": "agent",
    "rolled_back": "system",
    "replan": "agent",
    "escalate": "system",
    "completed": "system",
}


def _actor(node_name: str, update: dict[str, Any]) -> Actor:
    if node_name == "await_approval":
        return "system" if update.get("approval_decision") == "timeout" else "human"
    return NODE_ACTORS.get(node_name, "system")


def with_audit(node_name: str, node_fn: Callable[[dict], Awaitable[dict]]) -> Callable[[dict], Awaitable[dict]]:
    async def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        from_state = state.get("status")
        update = await node_fn(state)
        merged = {**state, **update}

        record = AuditRecord(
            run_id=merged["run_id"],
            timestamp=datetime.now(timezone.utc).isoformat(),
            from_state=from_state,
            to_state=merged.get("status", from_state),
            actor=_actor(node_name, update),
            # Cumulative context (legitimately still relevant even on a
            # later transition that didn't itself set it) vs. one-time
            # events (must come from `update` alone, or a stale value from
            # an earlier transition — e.g. a prior approval — would leak
            # into every transition after it).
            action_proposed=merged.get("proposed_action"),
            risk_tier=merged.get("risk_tier_final"),
            idempotency_key=merged.get("idempotency_key"),
            approver_id=update.get("approver_id"),
            decision=update.get("approval_decision"),
            rationale=update.get("action_rationale") or update.get("risk_rationale") or update.get("replan_context"),
        )
        runtime = get_runtime(AgentRuntimeContext)
        await runtime.context.audit_store.record(record)

        return update

    return wrapped
