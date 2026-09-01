"""Deterministic policy override (PROJECT_SPEC.md section 6): the LLM's
risk tier is never trusted alone for safety-critical actions. Any
delete_records or rollback_deployment is forced to high regardless of
what the model says, because those two tools are the ones that can
destroy data or leave a bad deploy in place — exactly the actions the
scenario suite's forbidden_actions exist to keep the agent away from
without explicit human sign-off.
"""

from __future__ import annotations

from src.risk.schemas import RiskTier

FORCED_HIGH_TOOLS = {"delete_records", "rollback_deployment"}


def apply_policy(tool: str, llm_tier: str) -> str:
    """Returns the final risk tier for `tool`, overriding `llm_tier` when
    the deterministic policy applies."""
    if tool in FORCED_HIGH_TOOLS:
        return RiskTier.HIGH.value
    return llm_tier
