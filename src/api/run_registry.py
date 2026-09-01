"""A denormalized "latest known status" snapshot per run, so the API's
GET /runs/{id} and GET /runs/pending-approval (PROJECT_SPEC.md section 10)
don't need to touch the checkpointer. The audit log (section 9) remains
the source of truth for full history; this is just a queryable cache the
API updates after every start/resume call.
"""

from __future__ import annotations

from typing import Any, Protocol

import asyncpg

RUN_FIELDS = (
    "run_id",
    "status",
    "scenario_id",
    "diagnosis",
    "proposed_action",
    "risk_tier",
    "rationale",
    "final_state",
)


class RunRegistry(Protocol):
    async def upsert(self, run_id: str, fields: dict[str, Any]) -> None: ...

    async def get(self, run_id: str) -> dict[str, Any] | None: ...

    async def list_pending_approval(self) -> list[dict[str, Any]]: ...


class InMemoryRunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}

    async def upsert(self, run_id: str, fields: dict[str, Any]) -> None:
        self._runs.setdefault(run_id, {"run_id": run_id})
        self._runs[run_id].update(fields)

    async def get(self, run_id: str) -> dict[str, Any] | None:
        return self._runs.get(run_id)

    async def list_pending_approval(self) -> list[dict[str, Any]]:
        return [r for r in self._runs.values() if r.get("status") == "AWAITING_APPROVAL"]


class PostgresRunRegistry:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def upsert(self, run_id: str, fields: dict[str, Any]) -> None:
        columns = ["run_id", *fields.keys()]
        values = [run_id, *fields.values()]
        placeholders = ", ".join(f"${i + 1}" for i in range(len(values)))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in fields)
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(
                f"""
                INSERT INTO runs ({", ".join(columns)}, updated_at)
                VALUES ({placeholders}, now())
                ON CONFLICT (run_id) DO UPDATE SET {updates}, updated_at = now()
                """,
                *values,
            )
        finally:
            await conn.close()

    async def get(self, run_id: str) -> dict[str, Any] | None:
        conn = await asyncpg.connect(self._dsn)
        try:
            row = await conn.fetchrow(
                f"SELECT {', '.join(RUN_FIELDS)} FROM runs WHERE run_id = $1", run_id
            )
        finally:
            await conn.close()
        return dict(row) if row is not None else None

    async def list_pending_approval(self) -> list[dict[str, Any]]:
        conn = await asyncpg.connect(self._dsn)
        try:
            rows = await conn.fetch(
                f"SELECT {', '.join(RUN_FIELDS)} FROM runs WHERE status = 'AWAITING_APPROVAL' ORDER BY updated_at"
            )
        finally:
            await conn.close()
        return [dict(r) for r in rows]
