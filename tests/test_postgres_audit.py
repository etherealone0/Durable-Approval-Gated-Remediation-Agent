"""Integration test for PostgresAuditStore against the real schema in
src/env/db/init.sql. Skipped when no Postgres is reachable; the in-memory
equivalent is covered by test_audit.py."""

import asyncpg

from src.audit.schema import AuditRecord
from src.audit.store import PostgresAuditStore


async def test_postgres_audit_store_round_trip_preserves_order(postgres_dsn):
    store = PostgresAuditStore(postgres_dsn)
    run_id = "pg-audit-test-run"

    conn = await asyncpg.connect(postgres_dsn)
    try:
        await conn.execute("DELETE FROM audit_log WHERE run_id = $1", run_id)
    finally:
        await conn.close()

    await store.record(
        AuditRecord(
            run_id=run_id,
            timestamp="2026-01-01T00:00:00+00:00",
            from_state=None,
            to_state="DIAGNOSING",
            actor="agent",
        )
    )
    await store.record(
        AuditRecord(
            run_id=run_id,
            timestamp="2026-01-01T00:00:01+00:00",
            from_state="DIAGNOSING",
            to_state="ACTION_PROPOSED",
            actor="agent",
            action_proposed="restart_service:service_a",
            rationale="stub rationale",
        )
    )

    records = await store.get_run(run_id)

    assert [r["to_state"] for r in records] == ["DIAGNOSING", "ACTION_PROPOSED"]
    assert records[1]["action_proposed"] == "restart_service:service_a"
