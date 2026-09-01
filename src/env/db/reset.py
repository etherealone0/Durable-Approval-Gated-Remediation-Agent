"""Reseed the mock production database to its clean baseline.

Used by FaultInjector.reset_all() between scenario runs. Requires a live
Postgres reachable at `dsn`; callers are expected to handle connection
failure (e.g. skip in environments with no Postgres running).
"""

from __future__ import annotations

import asyncpg

SEED_RECORDS = [
    ("order", "order-1001: shipped"),
    ("order", "order-1002: pending"),
    ("order", "order-1003: delivered"),
    ("session", "session-a1: active"),
    ("session", "session-a2: expired"),
    ("cache_entry", "cache-key-42: stale"),
]


async def reset_database(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("TRUNCATE TABLE records RESTART IDENTITY")
            await conn.executemany(
                "INSERT INTO records (kind, payload) VALUES ($1, $2)",
                SEED_RECORDS,
            )
    finally:
        await conn.close()
