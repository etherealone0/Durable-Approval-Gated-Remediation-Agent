"""Exercises the read-only tools against in-process mock services and an
in-memory records repository."""

import httpx
import pytest

from src.env.mock_service.app import create_app
from src.env.mock_service.state import FaultRate, FaultType
from src.tools.context import InMemoryRecordsRepository, ToolContext
from src.tools.readonly import check_disk_usage, get_metrics, get_service_health, query_db_readonly, read_logs
from src.tools.schemas import (
    CheckDiskUsageInput,
    GetMetricsInput,
    GetServiceHealthInput,
    QueryDbReadonlyInput,
    ReadLogsInput,
)


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://svc")


@pytest.fixture
def ctx():
    apps = {
        "service_a": create_app(name="service_a", has_disk=False),
        "service_c": create_app(name="service_c", has_disk=True),
    }
    repo = InMemoryRecordsRepository(
        rows=[
            {"id": 1, "kind": "order", "payload": "order-1001"},
            {"id": 2, "kind": "cache_entry", "payload": "cache-key-42"},
        ]
    )
    return ToolContext(clients={n: _client(a) for n, a in apps.items()}, records=repo), apps


async def test_get_service_health(ctx):
    tool_ctx, apps = ctx
    apps["service_a"].state.service_state.apply_fault(FaultType.MEMORY_LEAK, FaultRate.HIGH)

    result = await get_service_health(tool_ctx, GetServiceHealthInput(service="service_a"))
    assert result.status == "unhealthy"


async def test_read_logs(ctx):
    tool_ctx, apps = ctx
    apps["service_a"].state.service_state.apply_fault(FaultType.HIGH_ERROR_RATE, FaultRate.HIGH)

    result = await read_logs(tool_ctx, ReadLogsInput(service="service_a", limit=10))
    assert any("elevated error rate" in line for line in result.lines)


async def test_get_metrics(ctx):
    tool_ctx, _ = ctx
    result = await get_metrics(tool_ctx, GetMetricsInput(service="service_a"))
    assert result.memory_pct == 20.0
    assert result.disk_pct is None


async def test_check_disk_usage(ctx):
    tool_ctx, apps = ctx
    apps["service_c"].state.service_state.apply_fault(FaultType.DISK_FULL, FaultRate.MEDIUM)

    result = await check_disk_usage(tool_ctx, CheckDiskUsageInput(service="service_c"))
    assert result.used_pct == 80.0


async def test_query_db_readonly_filters_by_kind(ctx):
    tool_ctx, _ = ctx
    result = await query_db_readonly(tool_ctx, QueryDbReadonlyInput(kind="cache_entry"))
    assert [r["id"] for r in result.rows] == [2]


async def test_query_db_readonly_no_filter_returns_all(ctx):
    tool_ctx, _ = ctx
    result = await query_db_readonly(tool_ctx, QueryDbReadonlyInput())
    assert len(result.rows) == 2
