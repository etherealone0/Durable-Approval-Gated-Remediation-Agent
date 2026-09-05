"""FastAPI app for a single mock service instance.

Reads SERVICE_NAME and SERVICE_ROLE ("compute" or "disk") from the
environment at import time so the same image runs as service_a/b/c in
docker-compose (see ../../../docker-compose.yml).
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.env.mock_service.state import BASELINE_REPLICAS, FaultRate, FaultType, ServiceState


class FaultRequest(BaseModel):
    type: FaultType
    rate: FaultRate = FaultRate.HIGH


class ScaleRequest(BaseModel):
    replicas: int


class ConfigRequest(BaseModel):
    key: str
    value: str


class DeployRequest(BaseModel):
    version: str


def create_app(name: str, has_disk: bool, replicas: int = BASELINE_REPLICAS) -> FastAPI:
    state = ServiceState(name=name, has_disk=has_disk, replicas=replicas)
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

    @app.post("/scale")
    def scale(req: ScaleRequest):
        previous = state.scale(req.replicas)
        return {"service": state.name, "previous_replicas": previous, "replicas": state.replicas}

    @app.get("/config")
    def get_config():
        return {"service": state.name, "config": state.config}

    @app.post("/config")
    def set_config(req: ConfigRequest):
        previous = state.set_config(req.key, req.value)
        return {"service": state.name, "key": req.key, "previous_value": previous, "value": req.value}

    @app.get("/version")
    def version():
        return {"service": state.name, "deployed_version": state.deployed_version, "history": state.version_history}

    @app.post("/rollback")
    def rollback():
        try:
            previous_current, new_current = state.rollback()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"service": state.name, "rolled_back_from": previous_current, "deployed_version": new_current}

    @app.post("/deploy")
    def deploy(req: DeployRequest):
        previous = state.deploy(req.version)
        return {"service": state.name, "previous_version": previous, "deployed_version": state.deployed_version}

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
