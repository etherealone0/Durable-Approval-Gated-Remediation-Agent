"""Gathers a full diagnostic sweep of the sandbox using only read-only
tools: health/metrics/logs for every service, disk usage for services with
a disk surface, and every record currently in the mock database.

Used at proposal time (this phase) and reused unchanged at resume time by
revalidation, since staleness is defined as
drift between two sweeps taken with the same method.
"""

from __future__ import annotations

from typing import Any

from src.env.registry import KNOWN_SERVICES, has_disk
from src.tools.context import ToolContext
from src.tools.readonly import check_disk_usage, get_metrics, get_service_health, query_db_readonly, read_logs
from src.tools.schemas import (
    CheckDiskUsageInput,
    GetMetricsInput,
    GetServiceHealthInput,
    QueryDbReadonlyInput,
    ReadLogsInput,
)


async def gather_observations(ctx: ToolContext) -> dict[str, Any]:
    services: dict[str, Any] = {}
    for service in KNOWN_SERVICES:
        health = await get_service_health(ctx, GetServiceHealthInput(service=service))
        metrics = await get_metrics(ctx, GetMetricsInput(service=service))
        logs = await read_logs(ctx, ReadLogsInput(service=service, limit=10))
        service_obs: dict[str, Any] = {
            "health": health.model_dump(),
            "metrics": metrics.model_dump(),
            "logs": logs.lines,
        }
        if has_disk(service):
            disk = await check_disk_usage(ctx, CheckDiskUsageInput(service=service))
            service_obs["disk"] = disk.model_dump()
        services[service] = service_obs

    records = await query_db_readonly(ctx, QueryDbReadonlyInput(limit=1000))

    return {"services": services, "records": records.rows}
