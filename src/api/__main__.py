"""Runs the production FastAPI app: `python -m src.api`.

build_production_app() is an async context manager (it needs to hold the
AsyncPostgresSaver connection open for the server's lifetime), so it can't
be referenced directly as `uvicorn module:app` the way a plain module-level
app object could — this wraps it in the lifespan uvicorn's Server API
expects instead.
"""

from __future__ import annotations

import os

import uvicorn

from src.api.main import build_production_app
from src.durability.checkpointer import run_async


async def _serve() -> None:
    async with build_production_app() as app:
        config = uvicorn.Config(
            app,
            host=os.environ.get("API_HOST", "0.0.0.0"),
            port=int(os.environ.get("API_PORT", "8000")),
        )
        await uvicorn.Server(config).serve()


if __name__ == "__main__":
    run_async(_serve())
