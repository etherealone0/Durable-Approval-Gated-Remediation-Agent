"""AsyncPostgresSaver factory. This is the only checkpointer used against
real workloads; InMemorySaver is for unit tests of graph control flow
only, never for anything claiming durability.

See PROJECT_SPEC.md section 5.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


@asynccontextmanager
async def postgres_checkpointer(dsn: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Yields a ready-to-use AsyncPostgresSaver with its tables created.
    Each fresh call opens its own connection, independent of any other
    process or previous checkpointer instance — this is what lets a
    suspended run be resumed by a completely separate process using only
    its thread_id (see tests/test_graph_cross_process.py)."""
    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
