"""The workflow's persisted state and the named states from the state
machine diagram in PROJECT_SPEC.md section 4.

Nodes are stubbed in this phase (Prompt 5: state machine and durable
graph) wherever the real logic belongs to a later phase:
  - diagnose / propose_action: real tool-calling diagnosis lands in
    Prompt 6 (src/agent/nodes.py currently reads test-provided hints).
  - classify_risk: real LLM + deterministic policy lands in Prompt 7.
  - revalidate: real staleness detection lands in Prompt 8.
  - execute / verify / rolling_back: real tool execution and
    compensation lands in Prompt 9.
Each stub is deterministic and driven by hints on the initial input state
so the graph's control flow (every named state and transition) is fully
testable before that logic exists.
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

    diagnosis: str | None
    proposed_action: str | None
    state_fingerprint: str | None

    risk_tier_llm: str | None
    risk_tier_final: str | None

    approval_decision: ApprovalDecision | None
    approver_id: str | None

    drift_detected: bool | None

    idempotency_key: str | None
    execution_result: dict[str, Any] | None
    verification_passed: bool | None
    compensation: dict[str, Any] | None
    rollback_result: dict[str, Any] | None

    replan_cycles: int
    final_state: str | None

    # Deterministic stub hints consumed by src/agent/nodes.py until the
    # real logic behind each of them is implemented (see module docstring).
    test_risk_tier: str | None
    test_drift_detected: bool | None
    test_verification_passed: bool | None
