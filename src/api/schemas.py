"""Request/response schemas for the FastAPI interface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class CreateRunRequest(BaseModel):
    run_id: str | None = None
    scenario_id: str | None = None
    initial_state: dict[str, Any] = {}


class DecisionRequest(BaseModel):
    decision: Literal["approved", "edited", "rejected", "timeout"]
    approver_id: str | None = None
    edited_action: str | None = None


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    scenario_id: str | None = None
    diagnosis: str | None = None
    proposed_action: str | None = None
    risk_tier: str | None = None
    rationale: str | None = None
    final_state: str | None = None
