"""Verifies compute_fingerprint: the hash is
scoped to only the observation fields the proposed action's tool+target
actually depends on, so an unrelated change elsewhere doesn't count as
drift, but a relevant change always does."""

import copy

from src.revalidation.fingerprint import compute_fingerprint, validate_proposal

BASE_OBSERVATIONS = {
    "services": {
        "service_a": {
            "health": {"service": "service_a", "status": "healthy", "restart_count": 0},
            "metrics": {
                "memory_pct": 20.0,
                "cpu_pct": 15.0,
                "error_rate": 0.0,
                "restart_count": 0,
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
                "replicas": 1,
                "deployed_version": "v1.2.0",
                "disk_pct": 30.0,
            },
            "logs": [],
            "disk": {"service": "service_c", "used_pct": 30.0},
        },
    },
    "records": [
        {"id": 1, "kind": "cache_entry", "payload": "a"},
        {"id": 2, "kind": "order", "payload": "b"},
    ],
}


def test_restart_service_fingerprint_stable_when_nothing_relevant_changes():
    obs2 = copy.deepcopy(BASE_OBSERVATIONS)
    assert compute_fingerprint(BASE_OBSERVATIONS, "restart_service:service_a") == compute_fingerprint(
        obs2, "restart_service:service_a"
    )


def test_restart_service_fingerprint_changes_when_target_health_changes():
    drifted = copy.deepcopy(BASE_OBSERVATIONS)
    drifted["services"]["service_a"]["health"]["status"] = "unhealthy"

    assert compute_fingerprint(BASE_OBSERVATIONS, "restart_service:service_a") != compute_fingerprint(
        drifted, "restart_service:service_a"
    )


def test_restart_service_fingerprint_unaffected_by_unrelated_service_change():
    drifted = copy.deepcopy(BASE_OBSERVATIONS)
    drifted["services"]["service_c"]["health"]["status"] = "unhealthy"
    drifted["services"]["service_c"]["metrics"]["disk_pct"] = 99.0

    assert compute_fingerprint(BASE_OBSERVATIONS, "restart_service:service_a") == compute_fingerprint(
        drifted, "restart_service:service_a"
    )


def test_clear_cache_fingerprint_depends_only_on_disk_usage():
    drifted = copy.deepcopy(BASE_OBSERVATIONS)
    drifted["services"]["service_c"]["disk"]["used_pct"] = 95.0

    assert compute_fingerprint(BASE_OBSERVATIONS, "clear_cache:service_c") != compute_fingerprint(
        drifted, "clear_cache:service_c"
    )

    # A CPU spike on the same service isn't relevant to clear_cache.
    unrelated = copy.deepcopy(BASE_OBSERVATIONS)
    unrelated["services"]["service_c"]["metrics"]["cpu_pct"] = 99.0
    assert compute_fingerprint(BASE_OBSERVATIONS, "clear_cache:service_c") == compute_fingerprint(
        unrelated, "clear_cache:service_c"
    )


def test_rollback_deployment_fingerprint_depends_on_deployed_version():
    drifted = copy.deepcopy(BASE_OBSERVATIONS)
    drifted["services"]["service_a"]["metrics"]["deployed_version"] = "v1.1.0"

    assert compute_fingerprint(BASE_OBSERVATIONS, "rollback_deployment:service_a") != compute_fingerprint(
        drifted, "rollback_deployment:service_a"
    )


def test_delete_records_fingerprint_depends_on_matching_kind_only():
    drifted_other_kind = copy.deepcopy(BASE_OBSERVATIONS)
    drifted_other_kind["records"].append({"id": 3, "kind": "order", "payload": "c"})
    assert compute_fingerprint(BASE_OBSERVATIONS, "delete_records:cache_entry") == compute_fingerprint(
        drifted_other_kind, "delete_records:cache_entry"
    )

    drifted_target_kind = copy.deepcopy(BASE_OBSERVATIONS)
    drifted_target_kind["records"].append({"id": 4, "kind": "cache_entry", "payload": "d"})
    assert compute_fingerprint(BASE_OBSERVATIONS, "delete_records:cache_entry") != compute_fingerprint(
        drifted_target_kind, "delete_records:cache_entry"
    )


def test_delete_records_fingerprint_goes_to_empty_when_rows_already_deleted():
    already_deleted = copy.deepcopy(BASE_OBSERVATIONS)
    already_deleted["records"] = [r for r in already_deleted["records"] if r["kind"] != "cache_entry"]

    assert compute_fingerprint(BASE_OBSERVATIONS, "delete_records:cache_entry") != compute_fingerprint(
        already_deleted, "delete_records:cache_entry"
    )


def test_validate_proposal_accepts_real_actions():
    assert validate_proposal(BASE_OBSERVATIONS, "restart_service:service_a") is None
    assert validate_proposal(BASE_OBSERVATIONS, "clear_cache:service_c") is None
    assert validate_proposal(BASE_OBSERVATIONS, "delete_records:cache_entry") is None


def test_validate_proposal_rejects_unknown_service():
    assert validate_proposal(BASE_OBSERVATIONS, "restart_service:service_z") is not None


def test_validate_proposal_rejects_unknown_tool():
    assert validate_proposal(BASE_OBSERVATIONS, "reboot_the_datacenter:service_a") is not None


def test_validate_proposal_rejects_unknown_record_kind():
    assert validate_proposal(BASE_OBSERVATIONS, "delete_records:widget") is not None


def test_validate_proposal_rejects_clear_cache_on_service_without_disk():
    # service_a has no "disk" observation surface in BASE_OBSERVATIONS.
    assert validate_proposal(BASE_OBSERVATIONS, "clear_cache:service_a") is not None
