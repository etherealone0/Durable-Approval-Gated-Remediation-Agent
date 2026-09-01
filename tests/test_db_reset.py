"""Integration test for reset_database(). Requires a live Postgres (e.g.
`docker compose up postgres`) at DATABASE_URL; skipped otherwise since
this environment does not run docker-compose."""

import asyncpg

from src.env.db.reset import SEED_RECORDS, reset_database


async def test_reset_database_reseeds_baseline(postgres_dsn):
    conn = await asyncpg.connect(postgres_dsn)
    try:
        await conn.execute("DELETE FROM records")
        await conn.execute("INSERT INTO records (kind, payload) VALUES ('junk', 'from a test')")

        await reset_database(postgres_dsn)

        rows = await conn.fetch("SELECT kind, payload FROM records ORDER BY id")
        assert [(r["kind"], r["payload"]) for r in rows] == SEED_RECORDS
    finally:
        await conn.close()
