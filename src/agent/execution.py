"""Dispatches a proposed_action string to the real mutating tool, verifies
the fix with only the same read-only tools diagnosis uses (no ground-truth
peeking), and applies a compensation on rollback (PROJECT_SPEC.md section
8). One "tool:target" action is proposed and executed per attempt, so
"run compensations in reverse order" is trivially satisfied by there only
ever being one to run.
"""

from __future__ import annotations

from typing import Any

from src.tools.context import ToolContext
from src.tools.mutating import (
    apply_config_change,
    clear_cache,
    delete_records,
    restart_service,
    rollback_deployment,
    scale_service,
)
from src.tools.readonly import get_service_health, query_db_readonly
from src.tools.schemas import (
    ApplyConfigChangeInput,
    ClearCacheInput,
    DeleteRecordsInput,
    GetServiceHealthInput,
    QueryDbReadonlyInput,
    RestartServiceInput,
    RollbackDeploymentInput,
    ScaleServiceInput,
)

DEFAULT_SCALE_REPLICAS = 2


async def execute_action(
    ctx: ToolContext, proposed_action: str, parameters: dict[str, Any], idempotency_key: str
) -> dict[str, Any]:
    """Returns {"result": ..., "compensation": ...} from the underlying
    MutationResult, whichever tool this dispatches to."""
    tool, target = proposed_action.split(":", 1)

    if tool == "restart_service":
        outcome = await restart_service(ctx, RestartServiceInput(service=target, idempotency_key=idempotency_key))
    elif tool == "scale_service":
        replicas = parameters.get("replicas", DEFAULT_SCALE_REPLICAS)
        outcome = await scale_service(
            ctx, ScaleServiceInput(service=target, replicas=replicas, idempotency_key=idempotency_key)
        )
    elif tool == "clear_cache":
        outcome = await clear_cache(ctx, ClearCacheInput(service=target, idempotency_key=idempotency_key))
    elif tool == "apply_config_change":
        outcome = await apply_config_change(
            ctx,
            ApplyConfigChangeInput(
                service=target,
                key=parameters["key"],
                value=parameters["value"],
                idempotency_key=idempotency_key,
            ),
        )
    elif tool == "rollback_deployment":
        outcome = await rollback_deployment(ctx, RollbackDeploymentInput(service=target, idempotency_key=idempotency_key))
    elif tool == "delete_records":
        outcome = await delete_records(
            ctx, DeleteRecordsInput(kind=target, ids=parameters.get("ids"), idempotency_key=idempotency_key)
        )
    else:
        raise ValueError(f"unknown tool {tool!r}")

    return {"result": outcome.result, "compensation": outcome.compensation}


async def verify_action(ctx: ToolContext, proposed_action: str, execution_result: dict[str, Any]) -> bool:
    """A generic, ground-truth-free post-condition per tool: anything that
    targets a service should leave that service healthy; delete_records
    should leave none of the ids it deleted still present."""
    tool, target = proposed_action.split(":", 1)

    if tool == "delete_records":
        deleted_ids = set(execution_result.get("deleted_ids", []))
        if not deleted_ids:
            return True
        remaining = await query_db_readonly(ctx, QueryDbReadonlyInput(kind=target, limit=1_000_000))
        return not (deleted_ids & {r["id"] for r in remaining.rows})

    health = await get_service_health(ctx, GetServiceHealthInput(service=target))
    return health.status == "healthy"


async def apply_compensation(ctx: ToolContext, compensation: dict[str, Any]) -> dict[str, Any]:
    """Runs the compensating action a mutating tool registered at
    execution time. Not itself idempotency-keyed: the graph only reaches
    ROLLING_BACK once per failed verification (replanning afterward
    proposes fresh, unrelated actions), so there's nothing to replay."""
    comp_type = compensation.get("type")

    if comp_type == "noop":
        return {"applied": False, "reason": compensation.get("reason")}

    args = compensation.get("args", {})

    if comp_type == "scale_service":
        resp = await ctx.client(args["service"]).post("/scale", json={"replicas": args["replicas"]})
        resp.raise_for_status()
        return {"applied": True, "result": resp.json()}

    if comp_type == "apply_config_change":
        resp = await ctx.client(args["service"]).post(
            "/config", json={"key": args["key"], "value": args["value"]}
        )
        resp.raise_for_status()
        return {"applied": True, "result": resp.json()}

    if comp_type == "deploy":
        resp = await ctx.client(args["service"]).post("/deploy", json={"version": args["version"]})
        resp.raise_for_status()
        return {"applied": True, "result": resp.json()}

    if comp_type == "reinsert_records":
        await ctx.records.insert_many(args["rows"])
        return {"applied": True, "result": {"reinserted": len(args["rows"])}}

    raise ValueError(f"unknown compensation type {comp_type!r}")
