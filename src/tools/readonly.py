"""Read-only diagnostic tools. No approval gate: these never mutate the
sandbox environment.
"""

from __future__ import annotations

from src.tools.context import ToolContext
from src.tools.schemas import (
    CheckDiskUsageInput,
    CheckDiskUsageOutput,
    GetMetricsInput,
    GetMetricsOutput,
    GetServiceHealthInput,
    GetServiceHealthOutput,
    QueryDbReadonlyInput,
    QueryDbReadonlyOutput,
    ReadLogsInput,
    ReadLogsOutput,
)


async def get_service_health(ctx: ToolContext, input: GetServiceHealthInput) -> GetServiceHealthOutput:
    resp = await ctx.client(input.service).get("/health")
    resp.raise_for_status()
    return GetServiceHealthOutput(**resp.json())


async def read_logs(ctx: ToolContext, input: ReadLogsInput) -> ReadLogsOutput:
    resp = await ctx.client(input.service).get("/logs", params={"limit": input.limit})
    resp.raise_for_status()
    return ReadLogsOutput(**resp.json())


async def get_metrics(ctx: ToolContext, input: GetMetricsInput) -> GetMetricsOutput:
    resp = await ctx.client(input.service).get("/metrics")
    resp.raise_for_status()
    return GetMetricsOutput(**resp.json())


async def query_db_readonly(ctx: ToolContext, input: QueryDbReadonlyInput) -> QueryDbReadonlyOutput:
    rows = await ctx.records.get_by_kind(input.kind, input.limit)
    return QueryDbReadonlyOutput(rows=rows)


async def check_disk_usage(ctx: ToolContext, input: CheckDiskUsageInput) -> CheckDiskUsageOutput:
    resp = await ctx.client(input.service).get("/disk")
    resp.raise_for_status()
    return CheckDiskUsageOutput(**resp.json())
