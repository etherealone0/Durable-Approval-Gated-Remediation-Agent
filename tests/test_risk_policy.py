"""Unit tests for the deterministic risk policy overrides (PROJECT_SPEC.md
section 6): delete_records and rollback_deployment are always high
regardless of what the LLM says; a target with no redundant replica that's
being repeated this run (restart_count_this_run >= 1) is floored to at
least medium; everything else passes through unchanged."""

import pytest

from src.risk.policy import apply_policy, situational_features


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


def test_redundancy_floor_promotes_low_to_medium_on_repeat_attempt_without_redundant_replica():
    situational = {"has_redundant_replica": False, "restart_count_this_run": 1}
    assert apply_policy("restart_service", "low", situational=situational) == "medium"
    assert apply_policy("clear_cache", "low", situational=situational) == "medium"


def test_redundancy_floor_does_not_apply_on_first_attempt_without_redundant_replica():
    # A first attempt on a sole instance is exactly the case severity (not
    # replica count) should decide - see policy.py's module docstring for
    # why this floor no longer fires on redundancy alone.
    situational = {"has_redundant_replica": False, "restart_count_this_run": 0}
    assert apply_policy("restart_service", "low", situational=situational) == "low"


def test_redundancy_floor_does_not_apply_with_redundant_replica():
    situational = {"has_redundant_replica": True, "restart_count_this_run": 3}
    assert apply_policy("restart_service", "low", situational=situational) == "low"


def test_redundancy_floor_never_downgrades_medium_or_high():
    situational = {"has_redundant_replica": False, "restart_count_this_run": 1}
    assert apply_policy("restart_service", "medium", situational=situational) == "medium"
    assert apply_policy("restart_service", "high", situational=situational) == "high"


def test_redundancy_floor_ignored_when_situational_not_provided():
    assert apply_policy("restart_service", "low") == "low"
    assert apply_policy("restart_service", "low", situational=None) == "low"


def test_forced_high_tools_win_over_redundancy_floor():
    # Not that it matters (both land on "high"), but delete_records/
    # rollback_deployment must not fall into the floor's "medium" branch.
    situational = {"has_redundant_replica": False, "restart_count_this_run": 1}
    assert apply_policy("delete_records", "low", situational=situational) == "high"


BASE_OBSERVATIONS = {
    "services": {
        "service_a": {
            "health": {"service": "service_a", "status": "healthy", "restart_count": 2},
            "metrics": {
                "memory_pct": 20.0,
                "cpu_pct": 15.0,
                "error_rate": 0.1,
                "restart_count": 2,
                "replicas": 1,
                "deployed_version": "v1.2.0",
            },
            "logs": [],
        },
        "service_c": {
            "health": {"service": "service_c", "status": "healthy", "restart_count": 0},
            "metrics": {
                "memory_pct": 20.0,
                "cpu_pct": 15.0,
                "error_rate": 0.0,
                "restart_count": 0,
                "replicas": 3,
                "deployed_version": "v1.2.0",
                "disk_pct": 30.0,
            },
            "logs": [],
            "disk": {"service": "service_c", "used_pct": 30.0},
        },
    },
    "records": [{"id": 1, "kind": "cache_entry", "payload": "a"}],
}


def test_situational_features_flags_single_replica_as_not_redundant():
    features = situational_features(BASE_OBSERVATIONS, "restart_service:service_a")
    assert features == {
        "replicas": 1,
        "restart_count_this_run": 2,
        "error_rate": 0.1,
        "memory_pct": 20.0,
        "cpu_pct": 15.0,
        "disk_pct": None,
        "has_redundant_replica": False,
    }


def test_situational_features_flags_multi_replica_as_redundant():
    features = situational_features(BASE_OBSERVATIONS, "clear_cache:service_c")
    assert features["has_redundant_replica"] is True


def test_situational_features_none_for_delete_records():
    assert situational_features(BASE_OBSERVATIONS, "delete_records:cache_entry") is None
