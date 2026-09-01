import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import asyncpg
import pytest

POSTGRES_DSN = os.environ.get("DATABASE_URL", "postgresql://interlock:interlock@localhost:5432/interlock")


@pytest.fixture
async def postgres_dsn():
    """DSN for a live Postgres with the sandbox schema loaded. Skips the
    test when nothing is reachable (e.g. docker-compose isn't running)."""
    try:
        conn = await asyncpg.connect(POSTGRES_DSN, timeout=2)
    except (OSError, asyncpg.PostgresError):
        pytest.skip("no Postgres reachable at DATABASE_URL")
    await conn.close()
    return POSTGRES_DSN
