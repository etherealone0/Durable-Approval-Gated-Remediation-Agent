"""Proves idempotency for every mutating tool: calling the same tool twice
with the same idempotency_key must no-op on the second call (the recorded
result is returned, but the underlying side effect happens only once),
even when the second call's other arguments differ.

See PROJECT_SPEC.md section 8."""

import httpx
import pytest

from src.env.mock_service.app import create_app
from src.env.mock_service.state import FaultRate, FaultType
from src.tools.context import InMemoryRecordsRepository, ToolContext
from src.tools.mutating import (
    apply_config_change,
    clear_cache,
    delete_records,
    restart_service,
    rollback_deployment,
    scale_service,
)
from src.tools.schemas import (
    ApplyConfigChangeInput,
    ClearCacheInput,
    DeleteRecordsInput,
    RestartServiceInput,
    RollbackDeploymentInput,
    ScaleServiceInput,
)


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://svc")


@pytest.fixture
def compute_ctx():
    app = create_app(name="service_a", has_disk=False)
    ctx = ToolContext(clients={"service_a": _client(app)})
    return ctx, app


@pytest.fixture
def disk_ctx():
    app = create_app(name="service_c", has_disk=True)
    ctx = ToolContext(clients={"service_c": _client(app)})
    return ctx, app


async def test_restart_service_is_idempotent(compute_ctx):
    ctx, app = compute_ctx
    key = "run-1:restart"

    first = await restart_service(ctx, RestartServiceInput(service="service_a", idempotency_key=key))
    second = await restart_service(ctx, RestartServiceInput(service="service_a", idempotency_key=key))

    assert first.idempotent_replay is False
    assert second.idempotent_replay is True
    assert second.result == first.result
    assert app.state.service_state.restart_count == 1


async def test_scale_service_second_call_ignored_even_with_different_args(compute_ctx):
    ctx, app = compute_ctx
    key = "run-2:scale"

    first = await scale_service(
        ctx, ScaleServiceInput(service="service_a", replicas=5, idempotency_key=key)
    )
    second = await scale_service(
        ctx, ScaleServiceInput(service="service_a", replicas=9, idempotency_key=key)
    )

    assert first.result["replicas"] == 5
    assert second.idempotent_replay is True
    assert second.result == first.result
    assert app.state.service_state.replicas == 5  # not re-applied to 9
    assert first.compensation == {"type": "scale_service", "args": {"service": "service_a", "replicas": 1}}


async def test_delete_records_second_call_does_not_touch_new_rows():
    repo = InMemoryRecordsRepository(
        rows=[
            {"id": 1, "kind": "cache_entry", "payload": "a"},
            {"id": 2, "kind": "cache_entry", "payload": "b"},
        ]
    )
    ctx = ToolContext(clients={}, records=repo)
    key = "run-3:delete"

    first = await delete_records(ctx, DeleteRecordsInput(kind="cache_entry", idempotency_key=key))
    assert first.result["deleted_count"] == 2
    assert await repo.get_by_kind("cache_entry", 10) == []

    # New data arrives under the same kind after the action already ran.
    await repo.insert_many([{"id": 3, "kind": "cache_entry", "payload": "c"}])

    second = await delete_records(ctx, DeleteRecordsInput(kind="cache_entry", idempotency_key=key))
    assert second.idempotent_replay is True
    assert second.result == first.result
    # The replay must not have deleted the row that arrived after the fact.
    assert [r["id"] for r in await repo.get_by_kind("cache_entry", 10)] == [3]


async def test_clear_cache_is_idempotent(disk_ctx):
    ctx, app = disk_ctx
    state = app.state.service_state
    state.apply_fault(FaultType.DISK_FULL, FaultRate.HIGH)
    key = "run-4:clear-cache"

    first = await clear_cache(ctx, ClearCacheInput(service="service_c", idempotency_key=key))
    assert first.result["used_pct"] == 30.0

    # Drift happens after the first (real) call; the replay must not re-run.
    state.disk_pct = 95.0

    second = await clear_cache(ctx, ClearCacheInput(service="service_c", idempotency_key=key))
    assert second.idempotent_replay is True
    assert second.result == first.result
    assert state.disk_pct == 95.0  # untouched by the replay


async def test_apply_config_change_second_call_ignored(compute_ctx):
    ctx, app = compute_ctx
    key = "run-5:config"

    first = await apply_config_change(
        ctx,
        ApplyConfigChangeInput(service="service_a", key="log_level", value="debug", idempotency_key=key),
    )
    assert first.result == {"service": "service_a", "key": "log_level", "previous_value": "info", "value": "debug"}

    second = await apply_config_change(
        ctx,
        ApplyConfigChangeInput(service="service_a", key="log_level", value="trace", idempotency_key=key),
    )
    assert second.idempotent_replay is True
    assert second.result == first.result
    assert app.state.service_state.config["log_level"] == "debug"  # not overwritten to trace


async def test_rollback_deployment_is_idempotent(compute_ctx):
    ctx, app = compute_ctx
    key = "run-6:rollback"

    first = await rollback_deployment(ctx, RollbackDeploymentInput(service="service_a", idempotency_key=key))
    assert first.result == {"service": "service_a", "rolled_back_from": "v1.2.0", "deployed_version": "v1.1.0"}
    assert first.compensation == {"type": "deploy", "args": {"service": "service_a", "version": "v1.2.0"}}

    second = await rollback_deployment(ctx, RollbackDeploymentInput(service="service_a", idempotency_key=key))
    assert second.idempotent_replay is True
    assert second.result == first.result
    # Only one version popped, despite two calls with the same key.
    assert app.state.service_state.version_history == ["v1.0.0", "v1.1.0"]
