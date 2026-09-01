"""Smoke-tests MockServices on its own: real, independent uvicorn
subprocesses for service_a/b/c, started and stopped without needing
Postgres or the rest of the chaos harness. This is the part of
test_chaos_harness.py that doesn't require Docker, so it runs everywhere;
the full kill-point trials additionally need a live Postgres and are
covered there (skipped in this environment)."""

import pytest

from src.chaos.harness import MockServices


@pytest.fixture
def services():
    svc = MockServices()
    svc.start()
    yield svc
    svc.stop()


def test_services_start_healthy_on_independent_ports(services):
    for name in ("service_a", "service_b", "service_c"):
        assert services.get_metrics(name)["memory_pct"] == 20.0

    assert len({services.ports[n] for n in services.ports}) == 3  # three distinct real ports


def test_inject_fault_and_reset_round_trip(services):
    services.inject_fault("service_a", "memory_leak", "high")
    assert services.get_metrics("service_a")["memory_pct"] == 95.0

    services.reset()
    assert services.get_metrics("service_a")["memory_pct"] == 20.0


def test_disk_full_only_valid_on_service_c(services):
    with pytest.raises(Exception):
        services.inject_fault("service_a", "disk_full", "high")

    services.inject_fault("service_c", "disk_full", "high")
    assert services.get_metrics("service_c")["disk_pct"] == 95.0
