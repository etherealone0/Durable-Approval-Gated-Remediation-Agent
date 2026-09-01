"""Exercises FaultInjector against in-process mock services (ASGITransport,
no docker needed). The database-reset path is covered separately in
test_db_reset.py, which skips when no Postgres is reachable."""

import httpx
import pytest

from src.env.fault_injector import FaultInjector
from src.env.mock_service.app import create_app


@pytest.fixture
def injector():
    apps = {
        "service_a": create_app(name="service_a", has_disk=False),
        "service_b": create_app(name="service_b", has_disk=False),
        "service_c": create_app(name="service_c", has_disk=True),
    }
    clients = {
        name: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{name}")
        for name, app in apps.items()
    }
    return FaultInjector(clients)


async def test_inject_memory_leak_on_named_service(injector):
    result = await injector.inject("service_b", "memory_leak", rate="high")
    assert result["status"] == "unhealthy"
    assert "memory_leak" in result["active_faults"]

    health = await injector.get_health("service_b")
    assert health["status"] == "unhealthy"

    other = await injector.get_health("service_a")
    assert other["status"] == "healthy"


async def test_inject_unknown_service_raises(injector):
    with pytest.raises(ValueError):
        await injector.inject("service_z", "memory_leak")


async def test_inject_disk_full_only_valid_on_disk_service(injector):
    with pytest.raises(httpx.HTTPStatusError):
        await injector.inject("service_a", "disk_full")

    result = await injector.inject("service_c", "disk_full", rate="medium")
    assert result["status"] in ("degraded", "unhealthy")


async def test_reset_all_restores_every_service(injector):
    await injector.inject("service_a", "cpu_spike", rate="high")
    await injector.inject("service_b", "high_error_rate", rate="high")
    await injector.inject("service_c", "disk_full", rate="high")

    await injector.reset_all()

    for service in ("service_a", "service_b", "service_c"):
        health = await injector.get_health(service)
        assert health["status"] == "healthy"
