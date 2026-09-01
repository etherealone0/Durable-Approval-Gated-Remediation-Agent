"""Structured output for LLM-based risk classification (PROJECT_SPEC.md
section 6): never free text, always one of low/medium/high plus the
factors that justify it.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskClassificationOutput(BaseModel):
    tier: RiskTier
    reversibility: str = Field(description="Can this action be undone, and how easily?")
    blast_radius: str = Field(description="How many services/systems does this affect?")
    data_destructiveness: str = Field(description="Does this permanently destroy data?")
    rationale: str = Field(description="One or two sentences justifying the tier from the above.")
