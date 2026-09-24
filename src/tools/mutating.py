"""Mutating tools: approval-gated in the running agent (the graph
in src/agent/graph.py is what actually withholds these behind approval), but
every one is idempotent on `idempotency_key` and registers a compensating
action here regardless of who calls it.

A repeat call with the same idempotency_key
must no-op and return the original result rather than acting twice.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from src.tools.context import ToolContext
from src.tools.schemas import (
    ApplyConfigChangeInput,
    ClearCacheInput,
    DeleteRecordsInput,
    MutationResult,
    RestartServiceInput,
    RollbackDeploymentInput,
    ScaleServiceInput,
)


async def _run_idempotent(
    ctx: ToolContext,
    tool_name: str,
    idempotency_key: str,
    args: dict,
    perform: Callable[[], Awaitable[tuple[dict, dict]]],
) -> MutationResult:
    """Look up `idempotency_key` in the executed_actions store; if it has
    already run, return the recorded result without calling `perform`
    again. Otherwise run `perform` (which returns (result, compensation)),
    persist it, and return it."""
    existing = await ctx.store.get(idempotency_key)
    if existing is not None:
        return MutationResult(
            tool=tool_name,
            idempotency_key=idempotency_key,
            idempotent_replay=True,
            result=existing["result"],
            compensation=existing["compensation"],
        )

    result, compensation = await perform()
    await ctx.store.record(idempotency_key, tool_name, args, result, compensation)
    return MutationResult(
        tool=tool_name,
        idempotency_key=idempotency_key,
        idempotent_replay=False,
        result=result,
        compensation=compensation,
    )


async def restart_service(ctx: ToolContext, input: RestartServiceInput) -> MutationResult:
    async def perform() -> tuple[dict, dict]:
        resp = await ctx.client(input.service).post("/restart")
        resp.raise_for_status()
        result = resp.json()
        # Restarting a healthy process back to a bad state isn't a
        # meaningful undo; there is nothing to compensate.
        compensation = {"type": "noop", "reason": "restart has no reversible side effect"}
        return result, compensation

    return await _run_idempotent(
        ctx, "restart_service", input.idempotency_key, {"service": input.service}, perform
    )


async def scale_service(ctx: ToolContext, input: ScaleServiceInput) -> MutationResult:
    async def perform() -> tuple[dict, dict]:
        resp = await ctx.client(input.service).post("/scale", json={"replicas": input.replicas})
        resp.raise_for_status()
        result = resp.json()
        compensation = {
            "type": "scale_service",
            "args": {"service": input.service, "replicas": result["previous_replicas"]},
        }
        return result, compensation

    return await _run_idempotent(
        ctx,
        "scale_service",
        input.idempotency_key,
        {"service": input.service, "replicas": input.replicas},
        perform,
    )


async def delete_records(ctx: ToolContext, input: DeleteRecordsInput) -> MutationResult:
    async def perform() -> tuple[dict, dict]:
        if input.ids is not None:
            rows = await ctx.records.get_by_ids(input.ids)
        else:
            rows = await ctx.records.get_by_kind(input.kind, limit=1_000_000)
        ids_to_delete = [r["id"] for r in rows]
        await ctx.records.delete(ids_to_delete)
        result = {"kind": input.kind, "deleted_ids": ids_to_delete, "deleted_count": len(ids_to_delete)}
        compensation = {"type": "reinsert_records", "args": {"rows": rows}}
        return result, compensation

    return await _run_idempotent(
        ctx,
        "delete_records",
        input.idempotency_key,
        {"kind": input.kind, "ids": input.ids},
        perform,
    )


async def clear_cache(ctx: ToolContext, input: ClearCacheInput) -> MutationResult:
    async def perform() -> tuple[dict, dict]:
        resp = await ctx.client(input.service).post("/disk/clean")
        resp.raise_for_status()
        result = resp.json()
        # Freed disk space cannot be un-freed; nothing to compensate.
        compensation = {"type": "noop", "reason": "cleared cache space cannot be restored"}
        return result, compensation

    return await _run_idempotent(
        ctx, "clear_cache", input.idempotency_key, {"service": input.service}, perform
    )


async def apply_config_change(ctx: ToolContext, input: ApplyConfigChangeInput) -> MutationResult:
    async def perform() -> tuple[dict, dict]:
        resp = await ctx.client(input.service).post(
            "/config", json={"key": input.key, "value": input.value}
        )
        resp.raise_for_status()
        result = resp.json()
        compensation = {
            "type": "apply_config_change",
            "args": {"service": input.service, "key": input.key, "value": result["previous_value"]},
        }
        return result, compensation

    return await _run_idempotent(
        ctx,
        "apply_config_change",
        input.idempotency_key,
        {"service": input.service, "key": input.key, "value": input.value},
        perform,
    )


async def rollback_deployment(ctx: ToolContext, input: RollbackDeploymentInput) -> MutationResult:
    async def perform() -> tuple[dict, dict]:
        resp = await ctx.client(input.service).post("/rollback")
        resp.raise_for_status()
        result = resp.json()
        compensation = {
            "type": "deploy",
            "args": {"service": input.service, "version": result["rolled_back_from"]},
        }
        return result, compensation

    return await _run_idempotent(
        ctx, "rollback_deployment", input.idempotency_key, {"service": input.service}, perform
    )
