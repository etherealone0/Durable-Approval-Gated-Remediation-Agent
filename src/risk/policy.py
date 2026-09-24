"""Deterministic policy overrides: the LLM's
risk tier is never trusted alone for safety-critical actions.

Two independent, classifier-agnostic overrides — both act on whatever tier
the classifier returned, heuristic fallback or real model alike:

1. Any delete_records or rollback_deployment is forced to high regardless
   of what the model says, because those two tools are the ones that can
   destroy data or leave a bad deploy in place — exactly the actions the
   scenario suite's forbidden_actions exist to keep the agent away from
   without explicit human sign-off.

2. A redundancy floor: a target with no redundant replica that is ALSO
   being acted on for at least the second time this run can never be
   classified "low" — repeating restart_service/clear_cache on a sole
   instance that didn't recover the first time is a materially bigger
   call than a first attempt (see the classifier's few-shot examples).

   Earlier this floor fired on "no redundant replica" alone. That turned
   out to be wrong against this sandbox specifically: every service here
   always runs BASELINE_REPLICAS=1 (src/env/mock_service/state.py) — no
   scenario ever varies it — so "no redundant replica" is a constant, not
   a live situational signal, and flooring on it alone made "low"
   unreachable for restart_service/clear_cache across the entire suite,
   contradicting the ~15 low_risk scenarios in data/scenarios.json that
   are supposed to proceed autonomously. The actual signal that data
   varies is fault *severity* (rate: low/medium/high -> real magnitude
   differences in memory_pct/cpu_pct/disk_pct/error_rate, all now in
   situational_features below) — that's what should carry low-vs-medium
   judgment for a first attempt, which is the classifier's job, informed
   by real numbers, not a hardcoded magnitude threshold picked here to
   match scenario labels. The floor's remaining, narrower job is the one
   case severity alone can't express: a target already restarted once
   this run without recovering is thrashing, regardless of how mild the
   original fault looked.
"""

from __future__ import annotations

from typing import Any

from src.risk.schemas import RiskTier

FORCED_HIGH_TOOLS = {"delete_records", "rollback_deployment"}
MIN_REDUNDANT_REPLICAS = 2


def situational_features(observations: dict[str, Any], proposed_action: str) -> dict[str, Any] | None:
    """Live environment state for `proposed_action`'s target at
    classification time, for the classifier to weigh alongside the
    action's generic reversibility/blast-radius/destructiveness profile:
    current replica count, how many times this target has already been
    restarted in this run, its current error rate, and its current
    memory/CPU/disk usage — the magnitude fields that actually carry
    fault *severity* in this sandbox (a rate="low" fault and a rate="high"
    fault differ in these numbers, not in replica count or, for most
    fault types, error_rate). None for delete_records, whose target is a
    record kind with no service-level metrics surface.

    Deliberately does not include a "number of dependent services"
    feature: the sandbox has no dependency
    graph between service_a/b/c — the "cascading upstream" language in
    some trap scenarios' expected_diagnosis is narrative flavor for the
    diagnosis reasoner, not a mechanically-tracked relationship any tool
    exposes. Inventing one here would be fabricating a feature the
    environment doesn't actually have.
    """
    tool, target = proposed_action.split(":", 1)
    if tool == "delete_records":
        return None
    service_obs = observations.get("services", {}).get(target)
    if service_obs is None:
        return None
    metrics = service_obs["metrics"]
    replicas = metrics.get("replicas")
    return {
        "replicas": replicas,
        "restart_count_this_run": metrics.get("restart_count"),
        "error_rate": metrics.get("error_rate"),
        "memory_pct": metrics.get("memory_pct"),
        "cpu_pct": metrics.get("cpu_pct"),
        "disk_pct": metrics.get("disk_pct"),
        "has_redundant_replica": replicas is not None and replicas >= MIN_REDUNDANT_REPLICAS,
    }


def apply_policy(tool: str, llm_tier: str, situational: dict[str, Any] | None = None) -> str:
    """Returns the final risk tier for `tool`, overriding `llm_tier` when
    a deterministic policy applies. `situational` (situational_features'
    output) drives the redundancy floor; omit it to skip that check (e.g.
    delete_records, or a caller that hasn't computed it) — only the
    forced-high override still applies in that case."""
    if tool in FORCED_HIGH_TOOLS:
        return RiskTier.HIGH.value
    if (
        llm_tier == RiskTier.LOW.value
        and situational is not None
        and situational.get("has_redundant_replica") is False
        and (situational.get("restart_count_this_run") or 0) >= 1
    ):
        return RiskTier.MEDIUM.value
    return llm_tier
