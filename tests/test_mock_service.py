"""Exercises the mock service FastAPI app in-process (no docker needed):
health/metrics/logs derived from fault state, restart semantics, and the
disk-only endpoints on the disk-role instance."""

import httpx
import pytest

from src.env.mock_service.app import create_app


@pytest.fixture
def compute_client():
    app = create_app(name="service_a", has_disk=False)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://service_a")


@pytest.fixture
def disk_client():
    app = create_app(name="service_c", has_disk=True)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://service_c")


async def test_healthy_baseline(compute_client):
    async with compute_client as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"service": "service_a", "status": "healthy", "restart_count": 0}


async def test_memory_leak_fault_marks_unhealthy(compute_client):
    async with compute_client as client:
        resp = await client.post("/admin/fault", json={"type": "memory_leak", "rate": "high"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "unhealthy"

        health = await client.get("/health")
        assert health.json()["status"] == "unhealthy"

        metrics = await client.get("/metrics")
        assert metrics.json()["memory_pct"] == 95.0


async def test_restart_clears_transient_fault(compute_client):
    async with compute_client as client:
        await client.post("/admin/fault", json={"type": "high_error_rate", "rate": "high"})
        assert (await client.get("/health")).json()["status"] == "unhealthy"

        restart = await client.post("/restart")
        assert restart.status_code == 200
        assert restart.json()["restart_count"] == 1

        health = await client.get("/health")
        assert health.json()["status"] == "healthy"


async def test_disk_full_survives_restart_but_not_clean(disk_client):
    async with disk_client as client:
        await client.post("/admin/fault", json={"type": "disk_full", "rate": "high"})
        assert (await client.get("/disk")).json()["used_pct"] == 95.0

        await client.post("/restart")
        assert (await client.get("/disk")).json()["used_pct"] == 95.0
        assert (await client.get("/health")).json()["status"] == "unhealthy"

        clean = await client.post("/disk/clean")
        assert clean.json()["used_pct"] == 30.0
        assert (await client.get("/health")).json()["status"] == "healthy"


async def test_compute_service_has_no_disk_endpoints(compute_client):
    async with compute_client as client:
        resp = await client.get("/disk")
        assert resp.status_code == 404


async def test_disk_fault_rejected_on_compute_only_service():
    app = create_app(name="service_a", has_disk=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://service_a") as client:
        resp = await client.post("/admin/fault", json={"type": "disk_full", "rate": "high"})
        assert resp.status_code == 400


async def test_admin_reset_restores_baseline(compute_client):
    async with compute_client as client:
        await client.post("/admin/fault", json={"type": "cpu_spike", "rate": "medium"})
        await client.post("/restart")
        await client.post("/admin/reset")

        health = await client.get("/health")
        assert health.json() == {"service": "service_a", "status": "healthy", "restart_count": 0}


async def test_logs_reflect_fault_state(compute_client):
    async with compute_client as client:
        await client.post("/admin/fault", json={"type": "memory_leak", "rate": "high"})
        resp = await client.get("/logs")
        lines = resp.json()["lines"]
        assert any("possible leak" in line for line in lines)
