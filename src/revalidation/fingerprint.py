"""State fingerprinting for staleness revalidation: a hash of the
observation fields a proposed action's outcome actually depends on, captured
at proposal time and recomputed at resume time. A mismatch on any of those
fields is material drift.

Scoping the hash to just the relevant fields (rather than hashing the
whole environment) is deliberate: an unrelated service degrading while a
run is suspended shouldn't by itself invalidate an approved action that
never depended on it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.tools.registry import KNOWN_TOOLS, RECORD_KINDS


def _service_fields(observations: dict[str, Any], service: str, fields: list[str]) -> dict[str, Any]:
    metrics = observations["services"][service]["metrics"]
    health = observations["services"][service]["health"]
    combined = {**health, **metrics}
    return {field: combined[field] for field in fields}


def relevant_fields(observations: dict[str, Any], tool: str, target: str) -> dict[str, Any]:
    """The subset of `observations` that `tool` acting on `target` depends
    on. `target` is a service name for every tool except delete_records,
    where it's a record kind."""
    if tool in ("restart_service", "scale_service"):
        return _service_fields(observations, target, ["status", "memory_pct", "cpu_pct", "error_rate"])

    if tool == "clear_cache":
        return {"disk_pct": observations["services"][target]["disk"]["used_pct"]}

    if tool == "rollback_deployment":
        return _service_fields(observations, target, ["status", "deployed_version"])

    if tool == "apply_config_change":
        # Config values aren't observable through any read-only tool
        # (src/tools/readonly.py has only 5); fall back to health,
        # which is also currently unused by any scenario's correct_actions.
        return _service_fields(observations, target, ["status"])

    if tool == "delete_records":
        matching = sorted(r["id"] for r in observations["records"] if r["kind"] == target)
        return {"kind": target, "ids": matching}

    raise ValueError(f"unknown tool {tool!r}")


def validate_proposal(observations: dict[str, Any], proposed_action: str) -> str | None:
    """None if `proposed_action` is grounded in the real environment (a
    real service/record kind that the tool actually applies to);
    otherwise a human-readable reason it isn't, for replan_context.
    Nothing in ActionProposalOutput's schema stops an LLM reasoner from
    proposing a target that doesn't exist or a tool that doesn't apply to
    it (e.g. clear_cache on a service with no disk surface) — this is the
    one place that catches it before it reaches compute_fingerprint's
    unguarded dict lookups."""
    tool, target = proposed_action.split(":", 1)
    if tool not in KNOWN_TOOLS:
        return f"{tool!r} is not a known tool"
    if tool == "delete_records":
        if target not in RECORD_KINDS:
            return f"{target!r} is not a known record kind"
        return None
    if target not in observations["services"]:
        return f"{target!r} is not a known service"
    if tool == "clear_cache" and "disk" not in observations["services"][target]:
        return f"{target!r} has no disk surface; clear_cache doesn't apply to it"
    return None


def compute_fingerprint(observations: dict[str, Any], proposed_action: str) -> str:
    tool, target = proposed_action.split(":", 1)
    fields = relevant_fields(observations, tool, target)
    canonical = json.dumps(fields, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
