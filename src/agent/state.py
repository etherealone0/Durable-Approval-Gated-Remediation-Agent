"""The workflow's persisted state and the named states from the state
machine diagram (see README.md).

Where each node's logic lives: diagnose and propose_action in
src/agent/diagnosis.py, src/agent/observations.py and
src/revalidation/fingerprint.py; classify_risk in src/risk/classifier.py
and src/risk/policy.py; revalidate in src/revalidation/fingerprint.py;
and prepare_execution/execute/verify/rolling_back in
src/agent/execution.py.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, TypedDict

MAX_REPLAN_CYCLES = 3


class State(str, Enum):
    CREATED = "CREATED"
    DIAGNOSING = "DIAGNOSING"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    RISK_CLASSIFIED = "RISK_CLASSIFIED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REVALIDATING = "REVALIDATING"
    REPLANNING = "REPLANNING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"


ApprovalDecision = Literal["approved", "edited", "rejected", "timeout"]


class AgentState(TypedDict, total=False):
    run_id: str
    scenario_id: str | None
    status: str

    observations: dict[str, Any] | None
    diagnosis: str | None
    proposed_action: str | None
    action_parameters: dict[str, Any] | None
    action_rationale: str | None
    state_fingerprint: str | None
    invalid_proposal_reason: str | None

    risk_tier_llm: str | None
    risk_tier_final: str | None
    risk_rationale: str | None
    redundancy_floor_applied: bool | None

    approval_decision: ApprovalDecision | None
    approver_id: str | None

    drift_detected: bool | None

    idempotency_key: str | None
    execution_result: dict[str, Any] | None
    verification_passed: bool | None
    compensation: dict[str, Any] | None
    rollback_result: dict[str, Any] | None

    replan_cycles: int
    replan_context: str | None
    final_state: str | None
