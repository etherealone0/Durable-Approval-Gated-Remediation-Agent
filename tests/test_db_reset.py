"""Integration test for reset_database(). Requires a live Postgres (e.g.
`docker compose up postgres`) at DATABASE_URL; skipped otherwise since
this environment does not run docker-compose."""

import os

import asyncpg
import pytest

from src.env.db.reset import SEED_RECORDS, reset_database

DSN = os.environ.get("DATABASE_URL", "postgresql://interlock:interlock@localhost:5432/interlock")


async def _postgres_available() -> bool:
    try:
        conn = await asyncpg.connect(DSN, timeout=2)
    except (OSError, asyncpg.PostgresError):
        return False
    await conn.close()
    return True


async def test_reset_database_reseeds_baseline():
    if not await _postgres_available():
        pytest.skip("no Postgres reachable at DATABASE_URL")

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM records")
        await conn.execute("INSERT INTO records (kind, payload) VALUES ('junk', 'from a test')")

        await reset_database(DSN)

        rows = await conn.fetch("SELECT kind, payload FROM records ORDER BY id")
        assert [(r["kind"], r["payload"]) for r in rows] == SEED_RECORDS
    finally:
        await conn.close()
