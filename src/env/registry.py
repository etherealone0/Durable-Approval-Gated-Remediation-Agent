"""Single source of truth for which services exist in the sandbox and
which have a disk surface, mirroring docker-compose.yml. Used by the
scenario validator and anything else that needs to know what the fault
injector can actually target.
"""

from __future__ import annotations

SERVICE_ROLES = {
    "service_a": "compute",
    "service_b": "compute",
    "service_c": "disk",
}

KNOWN_SERVICES = tuple(SERVICE_ROLES)


def has_disk(service: str) -> bool:
    return SERVICE_ROLES.get(service) == "disk"
