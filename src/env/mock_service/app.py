"""FastAPI app for a single mock service instance.

Reads SERVICE_NAME and SERVICE_ROLE ("compute" or "disk") from the
environment at import time so the same image runs as service_a/b/c in
docker-compose (see ../../../docker-compose.yml).
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.env.mock_service.state import FaultRate, FaultType, ServiceState


class FaultRequest(BaseModel):
    type: FaultType
    rate: FaultRate = FaultRate.HIGH


def create_app(name: str, has_disk: bool) -> FastAPI:
    state = ServiceState(name=name, has_disk=has_disk)
    app = FastAPI(title=f"mock-{name}")
    app.state.service_state = state

    @app.get("/health")
    def health():
        return {"service": state.name, "status": state.status(), "restart_count": state.restart_count}

    @app.get("/metrics")
    def metrics():
        return {"service": state.name, **state.metrics()}

    @app.get("/logs")
    def logs(limit: int = 50):
        return {"service": state.name, "lines": state.logs(limit)}

    @app.post("/restart")
    def restart():
        state.restart()
        return {"service": state.name, "restart_count": state.restart_count, "status": state.status()}

    if has_disk:

        @app.get("/disk")
        def disk():
            return {"service": state.name, "used_pct": state.disk_pct}

        @app.post("/disk/clean")
        def disk_clean():
            state.clean_disk()
            return {"service": state.name, "used_pct": state.disk_pct}

    @app.post("/admin/fault")
    def admin_fault(req: FaultRequest):
        try:
            state.apply_fault(req.type, req.rate)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"service": state.name, "status": state.status(), "active_faults": state.active_faults}

    @app.post("/admin/reset")
    def admin_reset():
        state.reset()
        return {"service": state.name, "status": state.status()}

    return app


app = create_app(
    name=os.environ.get("SERVICE_NAME", "service_a"),
    has_disk=os.environ.get("SERVICE_ROLE", "compute") == "disk",
)
