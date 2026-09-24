"""Validates data/scenarios.json against the sandbox environment and
the scenario schema: every fault_injection must be something the
FaultInjector (src/env/fault_injector.py) can actually produce, and every
action reference must name a real tool against a real target.
"""

from __future__ import annotations

from src.env.mock_service.state import FaultRate, FaultType
from src.env.registry import KNOWN_SERVICES, has_disk
from src.tools.registry import KNOWN_TOOLS, RECORD_KINDS

REQUIRED_RISK_TIERS = {"low", "medium", "high"}

STALENESS_ACTIONS = {"reset", "inject_fault", "rollback", "delete_records"}

REQUIRED_KEYS = {
    "id",
    "name",
    "category",
    "fault_injection",
    "expected_diagnosis",
    "correct_actions",
    "acceptable_actions",
    "forbidden_actions",
    "expected_risk_tier",
    "verification",
}

REQUIRED_COVERAGE = {
    "low_risk": 15,
    "medium_high_risk": 20,
    "trap": 8,
    "staleness": 7,
}


def _validate_fault_injection(fault: dict, where: str) -> list[str]:
    errors = []
    service = fault.get("service")
    fault_type = fault.get("type")
    rate = fault.get("rate")

    if service not in KNOWN_SERVICES:
        errors.append(f"{where}: unknown service {service!r}; known: {KNOWN_SERVICES}")
        return errors  # can't check type/rate compatibility without a real service

    try:
        fault_type = FaultType(fault_type)
    except ValueError:
        errors.append(f"{where}: unknown fault type {fault.get('type')!r}")
        return errors

    if rate not in {r.value for r in FaultRate}:
        errors.append(f"{where}: unknown fault rate {rate!r}")

    if fault_type == FaultType.DISK_FULL and not has_disk(service):
        errors.append(f"{where}: disk_full is not producible on {service!r} (no disk surface)")

    return errors


def _validate_action_ref(action: str, field: str, scenario_id: str) -> list[str]:
    if ":" not in action:
        return [f"{scenario_id}.{field}: {action!r} is not in 'tool:target' form"]
    tool, target = action.split(":", 1)
    errors = []
    if tool not in KNOWN_TOOLS:
        errors.append(f"{scenario_id}.{field}: unknown tool {tool!r}")
    if target != "*":
        if tool == "delete_records":
            if target not in RECORD_KINDS:
                errors.append(f"{scenario_id}.{field}: unknown record kind {target!r}")
        elif target not in KNOWN_SERVICES:
            errors.append(f"{scenario_id}.{field}: unknown service {target!r}")
    return errors


def _validate_staleness_change(change: dict, where: str) -> list[str]:
    action = change.get("action")
    if action not in STALENESS_ACTIONS:
        return [f"{where}: unknown staleness action {action!r}; known: {sorted(STALENESS_ACTIONS)}"]

    if action == "inject_fault":
        return _validate_fault_injection(change, where)

    if action in ("reset", "rollback"):
        service = change.get("service")
        if service not in KNOWN_SERVICES:
            return [f"{where}: unknown service {service!r}"]
        return []

    if action == "delete_records":
        kind = change.get("kind")
        if kind not in RECORD_KINDS:
            return [f"{where}: unknown record kind {kind!r}"]
        return []

    return []  # pragma: no cover - unreachable, all STALENESS_ACTIONS handled above


def validate_scenario(scenario: dict) -> list[str]:
    errors: list[str] = []
    scenario_id = scenario.get("id", "<missing id>")

    missing = REQUIRED_KEYS - scenario.keys()
    if missing:
        errors.append(f"{scenario_id}: missing required keys {sorted(missing)}")
        return errors  # remaining checks assume these keys exist

    errors += _validate_fault_injection(scenario["fault_injection"], f"{scenario_id}.fault_injection")

    if scenario["expected_risk_tier"] not in REQUIRED_RISK_TIERS:
        errors.append(f"{scenario_id}: unknown expected_risk_tier {scenario['expected_risk_tier']!r}")

    for field in ("correct_actions", "acceptable_actions", "forbidden_actions"):
        for action in scenario[field]:
            errors += _validate_action_ref(action, field, scenario_id)

    if not set(scenario["correct_actions"]) <= set(scenario["acceptable_actions"]):
        errors.append(f"{scenario_id}: correct_actions must be a subset of acceptable_actions")

    if set(scenario["correct_actions"]) & set(scenario["forbidden_actions"]):
        errors.append(f"{scenario_id}: correct_actions overlaps forbidden_actions")

    if scenario["category"] == "staleness":
        staleness_injection = scenario.get("staleness_injection")
        if staleness_injection is None or "expected_drift_detected" not in scenario:
            errors.append(f"{scenario_id}: staleness scenario missing staleness_injection/expected_drift_detected")
        else:
            for i, change in enumerate(staleness_injection.get("changes", [])):
                errors += _validate_staleness_change(
                    change, f"{scenario_id}.staleness_injection.changes[{i}]"
                )

    return errors


def validate_scenarios(scenarios: list[dict]) -> list[str]:
    """Returns a list of human-readable error strings; empty means every
    scenario is valid and the required coverage split is met."""
    errors: list[str] = []

    ids = [s.get("id") for s in scenarios]
    duplicate_ids = {i for i in ids if ids.count(i) > 1}
    if duplicate_ids:
        errors.append(f"duplicate scenario ids: {sorted(duplicate_ids)}")

    for scenario in scenarios:
        errors += validate_scenario(scenario)

    counts: dict[str, int] = {}
    for scenario in scenarios:
        category = scenario.get("category", "<missing category>")
        counts[category] = counts.get(category, 0) + 1

    for category, required in REQUIRED_COVERAGE.items():
        actual = counts.get(category, 0)
        if actual != required:
            errors.append(f"category {category!r}: expected {required} scenarios, found {actual}")

    return errors
