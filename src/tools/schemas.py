"""Pydantic input/output schemas for every read-only and mutating tool.
See src/tools/mutating.py for the idempotency/compensation contract every
mutating tool follows.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Read-only tools (no approval needed)
# ---------------------------------------------------------------------------


class GetServiceHealthInput(BaseModel):
    service: str


class GetServiceHealthOutput(BaseModel):
    service: str
    status: str
    restart_count: int


class ReadLogsInput(BaseModel):
    service: str
    limit: int = 50


class ReadLogsOutput(BaseModel):
    service: str
    lines: list[str]


class GetMetricsInput(BaseModel):
    service: str


class GetMetricsOutput(BaseModel):
    service: str
    memory_pct: float
    cpu_pct: float
    error_rate: float
    restart_count: int
    replicas: int
    deployed_version: str
    disk_pct: float | None = None


class QueryDbReadonlyInput(BaseModel):
    kind: str | None = None
    limit: int = 50


class QueryDbReadonlyOutput(BaseModel):
    rows: list[dict]


class CheckDiskUsageInput(BaseModel):
    service: str


class CheckDiskUsageOutput(BaseModel):
    service: str
    used_pct: float


# ---------------------------------------------------------------------------
# Mutating tools (approval-gated; every one is idempotent on idempotency_key
# and returns the compensating action it registered)
# ---------------------------------------------------------------------------


class MutationResult(BaseModel):
    tool: str
    idempotency_key: str
    idempotent_replay: bool = Field(
        description="True if this key was already executed; the tool no-op'd and returned the prior result."
    )
    result: dict
    compensation: dict


class RestartServiceInput(BaseModel):
    service: str
    idempotency_key: str


class ScaleServiceInput(BaseModel):
    service: str
    replicas: int
    idempotency_key: str


class DeleteRecordsInput(BaseModel):
    kind: str
    ids: list[int] | None = Field(
        default=None, description="Specific record ids to delete; omit to delete every row of `kind`."
    )
    idempotency_key: str


class ClearCacheInput(BaseModel):
    service: str
    idempotency_key: str


class ApplyConfigChangeInput(BaseModel):
    service: str
    key: str
    value: str
    idempotency_key: str


class RollbackDeploymentInput(BaseModel):
    service: str
    idempotency_key: str
