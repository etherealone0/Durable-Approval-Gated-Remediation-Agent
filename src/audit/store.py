"""Append-only audit log storage (PROJECT_SPEC.md section 9), behind a
Protocol so graph tests don't need a live Postgres.
"""

from __future__ import annotations

from typing import Protocol

import asyncpg

from src.audit.schema import AuditRecord


class AuditStore(Protocol):
    async def record(self, entry: AuditRecord) -> None: ...

    async def get_run(self, run_id: str) -> list[dict]:
        """Returns every record for `run_id`, oldest first."""
        ...


class InMemoryAuditStore:
    def __init__(self) -> None:
        self._records: list[dict] = []

    async def record(self, entry: AuditRecord) -> None:
        self._records.append(entry.model_dump())

    async def get_run(self, run_id: str) -> list[dict]:
        return [r for r in self._records if r["run_id"] == run_id]


class PostgresAuditStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def record(self, entry: AuditRecord) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(
                """
                INSERT INTO audit_log
                    (run_id, ts, from_state, to_state, actor, action_proposed,
                     risk_tier, approver_id, decision, idempotency_key, rationale)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                entry.run_id,
                entry.timestamp,
                entry.from_state,
                entry.to_state,
                entry.actor,
                entry.action_proposed,
                entry.risk_tier,
                entry.approver_id,
                entry.decision,
                entry.idempotency_key,
                entry.rationale,
            )
        finally:
            await conn.close()

    async def get_run(self, run_id: str) -> list[dict]:
        conn = await asyncpg.connect(self._dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT run_id, ts AS timestamp, from_state, to_state, actor, action_proposed,
                       risk_tier, approver_id, decision, idempotency_key, rationale
                FROM audit_log WHERE run_id = $1 ORDER BY id
                """,
                run_id,
            )
        finally:
            await conn.close()
        return [dict(r, timestamp=r["timestamp"].isoformat()) for r in rows]
