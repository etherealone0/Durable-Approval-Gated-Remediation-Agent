"""Deliberate, deterministic kill windows for the chaos harness. A no-op
unless CHAOS_KILL_AT names this exact point, so there's zero overhead in
normal operation. When it does match, it prints a marker the harness watches
for on stdout and then pauses just long enough for the harness to send a
kill signal before the node can finish and checkpoint — proving whatever
happened just before this call (a tool's real side effect, a compensation)
survives a crash that struck before the corresponding checkpoint was
written.
"""

from __future__ import annotations

import asyncio
import os

DEFAULT_KILL_WINDOW_SECONDS = 2.0


async def mark(name: str, delay: float = DEFAULT_KILL_WINDOW_SECONDS) -> None:
    if os.environ.get("CHAOS_KILL_AT") == name:
        print(f"CHAOS_MARKER:{name}", flush=True)
        await asyncio.sleep(delay)
