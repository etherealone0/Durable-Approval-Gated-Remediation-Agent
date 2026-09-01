"""Validates data/scenarios.json: every fault_injection must be producible
by the FaultInjector from Prompt 2, every action reference must be well
formed, and the required coverage split from PROJECT_SPEC.md section 3
must hold (15 low-risk / 20 medium-high-risk / 8 trap / 7 staleness)."""

import json
from pathlib import Path

from src.eval.scenario_validation import REQUIRED_COVERAGE, validate_scenarios

SCENARIOS_PATH = Path(__file__).parent.parent / "data" / "scenarios.json"


def _load_scenarios():
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def test_scenario_suite_is_valid():
    scenarios = _load_scenarios()
    errors = validate_scenarios(scenarios)
    assert errors == []


def test_scenario_suite_has_fifty_scenarios():
    assert len(_load_scenarios()) == 50


def test_scenario_ids_are_unique():
    scenarios = _load_scenarios()
    ids = [s["id"] for s in scenarios]
    assert len(ids) == len(set(ids))


def test_coverage_split_matches_spec():
    scenarios = _load_scenarios()
    counts = {}
    for s in scenarios:
        counts[s["category"]] = counts.get(s["category"], 0) + 1
    assert counts == REQUIRED_COVERAGE


def test_staleness_scenarios_have_drift_injection():
    scenarios = _load_scenarios()
    staleness = [s for s in scenarios if s["category"] == "staleness"]
    assert len(staleness) == 7
    for s in staleness:
        assert s["expected_drift_detected"] is True
        assert len(s["staleness_injection"]["changes"]) >= 1


def test_trap_scenarios_forbid_the_tempting_action():
    scenarios = _load_scenarios()
    traps = [s for s in scenarios if s["category"] == "trap"]
    assert len(traps) == 8
    for s in traps:
        assert s["forbidden_actions"], f"{s['id']} has no forbidden_actions to trap against"
