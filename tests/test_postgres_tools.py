"""Integration tests for the Postgres-backed ExecutedActionsStore and
RecordsRepository against the real schema in src/env/db/init.sql. Skipped
when no Postgres is reachable (this environment does not run
docker-compose); the in-memory equivalents are covered by
test_mutating_tools.py and test_readonly_tools.py."""

import asyncpg

from src.tools.context import PostgresExecutedActionsStore, PostgresRecordsRepository


async def test_executed_actions_store_round_trip(postgres_dsn):
    store = PostgresExecutedActionsStore(postgres_dsn)
    key = "pg-test:restart-service_a"

    conn = await asyncpg.connect(postgres_dsn)
    try:
        await conn.execute("DELETE FROM executed_actions WHERE idempotency_key = $1", key)
    finally:
        await conn.close()

    assert await store.get(key) is None

    await store.record(
        key, "restart_service", {"service": "service_a"}, {"status": "healthy"}, {"type": "noop"}
    )
    row = await store.get(key)
    assert row["tool_name"] == "restart_service"
    assert row["result"] == {"status": "healthy"}

    # A second record() for the same key must not overwrite the first.
    await store.record(key, "restart_service", {"service": "service_a"}, {"status": "changed"}, {})
    assert (await store.get(key))["result"] == {"status": "healthy"}


async def test_records_repository_round_trip(postgres_dsn):
    repo = PostgresRecordsRepository(postgres_dsn)

    conn = await asyncpg.connect(postgres_dsn)
    try:
        await conn.execute("DELETE FROM records WHERE kind = 'pg_test'")
    finally:
        await conn.close()

    await repo.insert_many([{"id": 99001, "kind": "pg_test", "payload": "a"}])
    rows = await repo.get_by_kind("pg_test", limit=10)
    assert [r["payload"] for r in rows] == ["a"]

    await repo.delete([99001])
    assert await repo.get_by_kind("pg_test", limit=10) == []
