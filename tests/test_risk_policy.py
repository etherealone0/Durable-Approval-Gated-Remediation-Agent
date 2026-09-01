"""Unit tests for the deterministic risk policy override (PROJECT_SPEC.md
section 6): delete_records and rollback_deployment are always high,
regardless of what the LLM says; everything else passes through
unchanged."""

import pytest

from src.risk.policy import apply_policy


@pytest.mark.parametrize("llm_tier", ["low", "medium", "high"])
@pytest.mark.parametrize("tool", ["delete_records", "rollback_deployment"])
def test_forced_high_tools_always_override_to_high(tool, llm_tier):
    assert apply_policy(tool, llm_tier) == "high"


@pytest.mark.parametrize("llm_tier", ["low", "medium", "high"])
@pytest.mark.parametrize(
    "tool", ["restart_service", "scale_service", "clear_cache", "apply_config_change"]
)
def test_non_forced_tools_pass_through_unchanged(tool, llm_tier):
    assert apply_policy(tool, llm_tier) == llm_tier
