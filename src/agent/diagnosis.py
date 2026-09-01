"""Turns a diagnostic sweep (src/agent/observations.py) into a diagnosis,
and separately turns a diagnosis into a proposed remediation action
(PROJECT_SPEC.md section 1). These are two reasoning steps, not one,
because REPLANNING routes straight back to ACTION_PROPOSED without
re-diagnosing (section 4's diagram) — a rejected, stale, or failed action
needs a fresh *proposal* against the same diagnosis, informed by why the
last one didn't work, not a repeat of the same call producing the same
answer.

The reasoning itself is an LLM call: distinguishing a genuine root cause
from a misleading surface symptom — cascading upstream failures, a
database row whose name resembles the real problem but isn't it — is
exactly the judgment call the trap scenarios in section 3 are designed to
test, so a hardcoded heuristic here would defeat the point.

DiagnosisReasoner is a Protocol so tests can substitute a scripted
reasoner and exercise the surrounding plumbing (observation gathering,
state_fingerprint capture, replan looping) without an API key or network
access.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, Field

from src.tools.registry import KNOWN_TOOLS, RECORD_KINDS


class DiagnosisOutput(BaseModel):
    summary: str = Field(description="What's actually wrong and why, in one or two sentences.")
    root_cause_service: str | None = Field(
        default=None, description="The service where the true root cause lives, if any."
    )


class ActionProposalOutput(BaseModel):
    tool: str = Field(description=f"One of: {sorted(KNOWN_TOOLS)}")
    target: str = Field(
        description="A service name for every tool except delete_records, where it's a record kind."
    )
    rationale: str = Field(description="Why this action addresses the root cause, not just the symptom.")


class DiagnosisReasoner(Protocol):
    async def diagnose(self, observations: dict[str, Any]) -> dict[str, Any]:
        """Returns a dict shaped like DiagnosisOutput.model_dump()."""
        ...

    async def propose_action(
        self, observations: dict[str, Any], diagnosis: dict[str, Any], replan_context: str | None
    ) -> dict[str, Any]:
        """Returns a dict shaped like ActionProposalOutput.model_dump().
        `replan_context` is None on the first proposal and otherwise
        explains why a prior action for this same diagnosis didn't stick
        (rejected, stale, or failed verification), so the new proposal
        isn't just a repeat of the same answer."""
        ...


TOOL_GUIDE = f"""Available tools you may propose (tool:target form):
- restart_service:<service> — restarts a process; fixes memory leaks, CPU spikes, \
elevated error rates, and crashed services. Does NOT free disk space.
- scale_service:<service> — adds replicas; a reasonable alternative to restarting \
for load-driven issues.
- clear_cache:<service> — frees local disk space on a service. This is the ONLY \
fix for high disk usage.
- apply_config_change:<service> — changes a config value.
- rollback_deployment:<service> — reverts to the previous deployed version; use \
only when a version change (visible in deployed_version) is the actual cause.
- delete_records:<kind> — permanently deletes database rows, kind is one of \
{sorted(RECORD_KINDS)}. This is destructive and irreversible; only propose it when \
the data itself (not a service metric) is the confirmed root cause, e.g. \
genuinely stale/expired rows a metric or log line specifically implicates.

delete_records and rollback_deployment are last resorts. If a restart, scale, or \
cache clear would resolve it, prefer that."""

DIAGNOSIS_SYSTEM_PROMPT = f"""You are an incident-diagnosis agent operating on a \
sandboxed production environment with services service_a, service_b, service_c.

You will be given a full read-only sweep: health, metrics, and recent logs for \
every service, disk usage where applicable, and every row currently in the \
database. Identify the TRUE root cause, not just the loudest symptom:
- A service's own errors are sometimes caused by a DEPENDENCY it calls, not by \
itself — check every service's health before blaming the one with the loudest \
symptom.
- A database row's name resembling the symptom (e.g. a "cache_entry" row when a \
service's local disk cache is full) does not mean that row is the cause — a \
service's disk_pct is about its local disk, not the database.
- deployed_version only matters if it actually changed something; don't assume a \
bad deploy just because errors exist.

{TOOL_GUIDE}

Respond with the single true root cause; do not propose an action yet."""

PROPOSAL_SYSTEM_PROMPT = f"""You are an incident-remediation agent. You have \
already diagnosed the root cause; now choose the one action that fixes it.

{TOOL_GUIDE}"""


def _report(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


class AnthropicDiagnosisReasoner:
    def __init__(self, model: str = "claude-sonnet-5") -> None:
        from langchain_anthropic import ChatAnthropic

        base = ChatAnthropic(model=model)
        self._diagnosis_model = base.with_structured_output(DiagnosisOutput)
        self._proposal_model = base.with_structured_output(ActionProposalOutput)

    async def diagnose(self, observations: dict[str, Any]) -> dict[str, Any]:
        messages = [("system", DIAGNOSIS_SYSTEM_PROMPT), ("human", _report(observations))]
        result = await self._diagnosis_model.ainvoke(messages)
        return result.model_dump()

    async def propose_action(
        self, observations: dict[str, Any], diagnosis: dict[str, Any], replan_context: str | None
    ) -> dict[str, Any]:
        human = {"observations": observations, "diagnosis": diagnosis}
        if replan_context:
            human["why_you_are_replanning"] = replan_context
        messages = [("system", PROPOSAL_SYSTEM_PROMPT), ("human", _report(human))]
        result = await self._proposal_model.ainvoke(messages)
        return result.model_dump()
