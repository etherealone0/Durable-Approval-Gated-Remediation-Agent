"""Verifies gather_observations produces the full diagnostic sweep
(health/metrics/logs for every service, disk usage only where it exists,
every database row) that diagnosis and fingerprinting depend on."""

from src.agent.observations import gather_observations
from src.env.mock_service.state import FaultRate, FaultType
from tests.helpers import build_tool_ctx


async def test_gathers_health_metrics_logs_for_every_service():
    ctx = build_tool_ctx()

    observations = await gather_observations(ctx)

    assert set(observations["services"]) == {"service_a", "service_b", "service_c"}
    for service in ("service_a", "service_b", "service_c"):
        obs = observations["services"][service]
        assert obs["health"]["status"] == "healthy"
        assert obs["metrics"]["memory_pct"] == 20.0
        assert obs["metrics"]["deployed_version"] == "v1.2.0"
        assert isinstance(obs["logs"], list)


async def test_disk_usage_only_gathered_for_disk_capable_services():
    ctx = build_tool_ctx()

    observations = await gather_observations(ctx)

    assert "disk" in observations["services"]["service_c"]
    assert "disk" not in observations["services"]["service_a"]
    assert "disk" not in observations["services"]["service_b"]


async def test_gathers_every_database_record():
    rows = [{"id": 1, "kind": "cache_entry", "payload": "x"}, {"id": 2, "kind": "order", "payload": "y"}]
    ctx = build_tool_ctx(records=rows)

    observations = await gather_observations(ctx)

    assert {r["id"] for r in observations["records"]} == {1, 2}


async def test_reflects_current_fault_state():
    ctx = build_tool_ctx()
    # Drive service_b unhealthy through the same HTTP surface the real
    # fault injector uses, then confirm the sweep picks it up.
    resp = await ctx.client("service_b").post(
        "/admin/fault", json={"type": FaultType.MEMORY_LEAK.value, "rate": FaultRate.HIGH.value}
    )
    assert resp.status_code == 200

    observations = await gather_observations(ctx)

    assert observations["services"]["service_b"]["health"]["status"] == "unhealthy"
    assert observations["services"]["service_b"]["metrics"]["memory_pct"] == 95.0
