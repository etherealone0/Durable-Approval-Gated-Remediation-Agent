"""One audit record per state transition,
with exactly the fields listed there."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Actor = Literal["agent", "human", "system"]


class AuditRecord(BaseModel):
    run_id: str
    timestamp: str
    from_state: str | None
    to_state: str | None
    actor: Actor
    action_proposed: str | None = None
    risk_tier: str | None = None
    approver_id: str | None = None
    decision: str | None = None
    idempotency_key: str | None = None
    rationale: str | None = None
