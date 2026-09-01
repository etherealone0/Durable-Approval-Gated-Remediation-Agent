"""In-memory fault state for a single mock service instance.

Kept separate from app.py so the fault-application logic is unit-testable
without spinning up FastAPI/ASGI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class FaultType(str, Enum):
    MEMORY_LEAK = "memory_leak"
    HIGH_ERROR_RATE = "high_error_rate"
    CPU_SPIKE = "cpu_spike"
    SERVICE_DOWN = "service_down"
    DISK_FULL = "disk_full"


class FaultRate(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_RATE_MAGNITUDE = {
    FaultRate.LOW: 65.0,
    FaultRate.MEDIUM: 80.0,
    FaultRate.HIGH: 95.0,
}
_RATE_ERROR_RATE = {
    FaultRate.LOW: 0.15,
    FaultRate.MEDIUM: 0.3,
    FaultRate.HIGH: 0.6,
}

BASELINE_MEMORY_PCT = 20.0
BASELINE_CPU_PCT = 15.0
BASELINE_DISK_PCT = 30.0
BASELINE_ERROR_RATE = 0.0
BASELINE_REPLICAS = 1
BASELINE_CONFIG = {"log_level": "info", "max_connections": "100", "cache_ttl_seconds": "300"}
BASELINE_VERSION_HISTORY = ["v1.0.0", "v1.1.0", "v1.2.0"]

DEGRADED_THRESHOLD = 75.0
UNHEALTHY_THRESHOLD = 90.0
DEGRADED_ERROR_RATE = 0.1
UNHEALTHY_ERROR_RATE = 0.5


@dataclass
class ServiceState:
    name: str
    has_disk: bool = False

    memory_pct: float = BASELINE_MEMORY_PCT
    cpu_pct: float = BASELINE_CPU_PCT
    disk_pct: float = BASELINE_DISK_PCT
    error_rate: float = BASELINE_ERROR_RATE
    forced_down: bool = False
    restart_count: int = 0
    active_faults: list[str] = field(default_factory=list)

    replicas: int = BASELINE_REPLICAS
    config: dict[str, str] = field(default_factory=lambda: dict(BASELINE_CONFIG))
    version_history: list[str] = field(default_factory=lambda: list(BASELINE_VERSION_HISTORY))

    @property
    def deployed_version(self) -> str:
        return self.version_history[-1]

    def status(self) -> str:
        if self.forced_down or self.error_rate >= UNHEALTHY_ERROR_RATE:
            return "unhealthy"
        worst = max(self.memory_pct, self.cpu_pct, self.disk_pct if self.has_disk else 0.0)
        if worst >= UNHEALTHY_THRESHOLD:
            return "unhealthy"
        if worst >= DEGRADED_THRESHOLD or self.error_rate >= DEGRADED_ERROR_RATE:
            return "degraded"
        return "healthy"

    def metrics(self) -> dict:
        data = {
            "memory_pct": self.memory_pct,
            "cpu_pct": self.cpu_pct,
            "error_rate": self.error_rate,
            "restart_count": self.restart_count,
            "replicas": self.replicas,
            "deployed_version": self.deployed_version,
        }
        if self.has_disk:
            data["disk_pct"] = self.disk_pct
        return data

    def logs(self, limit: int = 50) -> list[str]:
        now = datetime.now(timezone.utc).isoformat()
        lines = [f"{now} INFO {self.name} heartbeat ok"]
        if self.error_rate >= DEGRADED_ERROR_RATE:
            lines.append(f"{now} ERROR {self.name} elevated error rate={self.error_rate:.2f}")
        if self.memory_pct >= DEGRADED_THRESHOLD:
            lines.append(f"{now} WARN {self.name} high memory usage={self.memory_pct:.0f}% possible leak")
        if self.cpu_pct >= DEGRADED_THRESHOLD:
            lines.append(f"{now} WARN {self.name} high cpu utilization={self.cpu_pct:.0f}%")
        if self.has_disk and self.disk_pct >= DEGRADED_THRESHOLD:
            lines.append(f"{now} WARN {self.name} disk usage critical={self.disk_pct:.0f}%")
        if self.forced_down:
            lines.append(f"{now} CRITICAL {self.name} service unresponsive")
        return list(reversed(lines))[:limit]

    def apply_fault(self, fault_type: FaultType, rate: FaultRate) -> None:
        if fault_type == FaultType.MEMORY_LEAK:
            self.memory_pct = _RATE_MAGNITUDE[rate]
        elif fault_type == FaultType.CPU_SPIKE:
            self.cpu_pct = _RATE_MAGNITUDE[rate]
        elif fault_type == FaultType.HIGH_ERROR_RATE:
            self.error_rate = _RATE_ERROR_RATE[rate]
        elif fault_type == FaultType.SERVICE_DOWN:
            self.forced_down = True
            self.error_rate = 1.0
        elif fault_type == FaultType.DISK_FULL:
            if not self.has_disk:
                raise ValueError(f"{self.name} has no disk surface")
            self.disk_pct = _RATE_MAGNITUDE[rate]
        else:  # pragma: no cover - guarded by FaultType enum
            raise ValueError(f"unknown fault type {fault_type}")
        if fault_type.value not in self.active_faults:
            self.active_faults.append(fault_type.value)

    def restart(self) -> None:
        """Simulate a process restart: clears transient faults but NOT disk
        usage, since restarting a process does not free disk space."""
        self.restart_count += 1
        self.memory_pct = BASELINE_MEMORY_PCT
        self.cpu_pct = BASELINE_CPU_PCT
        self.error_rate = BASELINE_ERROR_RATE
        self.forced_down = False
        self.active_faults = [f for f in self.active_faults if f == FaultType.DISK_FULL.value]

    def clean_disk(self) -> None:
        if not self.has_disk:
            raise ValueError(f"{self.name} has no disk surface")
        self.disk_pct = BASELINE_DISK_PCT
        self.active_faults = [f for f in self.active_faults if f != FaultType.DISK_FULL.value]

    def scale(self, replicas: int) -> int:
        previous = self.replicas
        self.replicas = replicas
        return previous

    def set_config(self, key: str, value: str) -> str | None:
        previous = self.config.get(key)
        self.config[key] = value
        return previous

    def rollback(self) -> tuple[str, str]:
        """Pop the current deployed version and fall back to the previous
        one. Returns (previous_current, new_current). Also clears transient
        faults (not disk usage), since switching away from a bad release
        stops that release's misbehavior, same as restart()."""
        if len(self.version_history) < 2:
            raise ValueError(f"{self.name} has no prior version to roll back to")
        previous_current = self.version_history.pop()
        self.memory_pct = BASELINE_MEMORY_PCT
        self.cpu_pct = BASELINE_CPU_PCT
        self.error_rate = BASELINE_ERROR_RATE
        self.forced_down = False
        self.active_faults = [f for f in self.active_faults if f == FaultType.DISK_FULL.value]
        return previous_current, self.deployed_version

    def deploy(self, version: str) -> str:
        """Push `version` as the new current deployed version. Used both as
        a general deploy action and to compensate a prior rollback."""
        previous = self.deployed_version
        self.version_history.append(version)
        return previous

    def reset(self) -> None:
        self.memory_pct = BASELINE_MEMORY_PCT
        self.cpu_pct = BASELINE_CPU_PCT
        self.disk_pct = BASELINE_DISK_PCT
        self.error_rate = BASELINE_ERROR_RATE
        self.forced_down = False
        self.restart_count = 0
        self.active_faults = []
        self.replicas = BASELINE_REPLICAS
        self.config = dict(BASELINE_CONFIG)
        self.version_history = list(BASELINE_VERSION_HISTORY)
