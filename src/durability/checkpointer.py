"""AsyncPostgresSaver factory. This is the only checkpointer used against
real workloads; InMemorySaver is for unit tests of graph control flow
only, never for anything claiming durability.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Coroutine, TypeVar

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

_T = TypeVar("_T")


def run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """Like asyncio.run(), but with an event loop psycopg's async mode can
    actually use. Windows' default ProactorEventLoop is not one — see
    https://www.psycopg.org/psycopg3/docs/advanced/async.html#async-and-windows.
    Any process-level entrypoint that awaits postgres_checkpointer must
    start its loop through this, not a bare asyncio.run()."""
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)


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
