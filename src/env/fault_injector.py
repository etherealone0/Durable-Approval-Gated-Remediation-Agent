"""Fault injector: puts the sandbox environment into a named bad state on
command, and resets it to a clean baseline between runs.

See PROJECT_SPEC.md section 2. Talks to each mock service's /admin/*
endpoints over HTTP rather than mutating state directly, so it exercises
the same interface a real chaos tool would use and works whether the
services are in-process (tests) or in separate docker-compose containers.
"""

from __future__ import annotations

import os

import httpx

from src.env.db.reset import reset_database
from src.env.mock_service.state import FaultRate, FaultType

DEFAULT_SERVICE_ENV_VARS = {
    "service_a": "SERVICE_A_URL",
    "service_b": "SERVICE_B_URL",
    "service_c": "SERVICE_C_URL",
}


class FaultInjector:
    def __init__(self, service_clients: dict[str, httpx.AsyncClient], database_dsn: str | None = None):
        """service_clients maps service name -> an httpx.AsyncClient whose
        base_url already points at that service (a real base_url against a
        docker-compose host, or an ASGITransport client in tests)."""
        self._clients = service_clients
        self._database_dsn = database_dsn

    async def inject(self, service: str, fault_type: str, rate: str = "high") -> dict:
        client = self._require_client(service)
        resp = await client.post(
            "/admin/fault",
            json={"type": FaultType(fault_type).value, "rate": FaultRate(rate).value},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_health(self, service: str) -> dict:
        resp = await self._require_client(service).get("/health")
        resp.raise_for_status()
        return resp.json()

    async def get_metrics(self, service: str) -> dict:
        resp = await self._require_client(service).get("/metrics")
        resp.raise_for_status()
        return resp.json()

    async def reset_all(self) -> None:
        for client in self._clients.values():
            resp = await client.post("/admin/reset")
            resp.raise_for_status()
        if self._database_dsn:
            await reset_database(self._database_dsn)

    def _require_client(self, service: str) -> httpx.AsyncClient:
        try:
            return self._clients[service]
        except KeyError as exc:
            raise ValueError(f"unknown service {service!r}; known: {sorted(self._clients)}") from exc


def build_default_injector() -> FaultInjector:
    """Build a FaultInjector pointed at the docker-compose services and
    database, using URLs/DSN from the environment (see .env.example)."""
    clients = {
        service: httpx.AsyncClient(base_url=os.environ[env_var])
        for service, env_var in DEFAULT_SERVICE_ENV_VARS.items()
    }
    return FaultInjector(clients, database_dsn=os.environ.get("DATABASE_URL"))
