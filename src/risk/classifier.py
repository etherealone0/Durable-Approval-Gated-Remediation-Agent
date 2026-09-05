"""LLM-based risk classification (PROJECT_SPEC.md section 6). RiskClassifier
is a Protocol so tests can substitute a scripted classifier and exercise
the deterministic policy override (src/risk/policy.py) without an API key.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from src.risk.schemas import RiskClassificationOutput

SYSTEM_PROMPT = """You are a risk classifier for an infrastructure remediation \
agent. Classify the proposed action into low, medium, or high risk using ONLY:
- reversibility: can this action be undone, and how easily?
- blast radius: how many services or systems does this affect?
- data destructiveness: does this permanently destroy data?

You will also be given `situational`: live environment state for the action's \
target at the moment of classification — not just the tool's generic profile:
- replicas / has_redundant_replica: current replica count. A restart or \
cache-clear on a target with only one replica takes that service fully \
offline for the duration, a materially bigger blast radius than the same \
action on a target with two or more replicas absorbing traffic in parallel.
- restart_count_this_run: how many times this target has already been acted \
on this run. Repeating an action that hasn't worked is a bigger call than \
trying it once, regardless of replica count.
- memory_pct, cpu_pct, disk_pct, error_rate: the target's actual current \
severity. This is the main signal for how bad the underlying fault is — a \
service sitting a little above its normal baseline is a mild, easily-reversed \
situation; one deep into the high 80s/90s or beyond is a service already in \
real trouble, where a restart is a more consequential, less certain fix. \
Weigh these numbers directly rather than assuming any single fault \
description is inherently mild or severe.

Do not consider how confident the diagnosis is — only the properties of the \
action itself and its target's current situational state.

Examples (tool, situational, correct tier, why):
- restart_service, {"replicas": 3, "restart_count_this_run": 0, "error_rate": 0.05, \
"cpu_pct": 40}, low: two other replicas keep serving while this one restarts, and \
cpu_pct is only modestly above baseline — no user-facing gap, mild fault.
- restart_service, {"replicas": 1, "restart_count_this_run": 0, "error_rate": 0.0, \
"cpu_pct": 65}, low: sole instance, but cpu_pct is well short of the ~90 range that \
signals real trouble — a brief restart of a mildly elevated process, first attempt.
- restart_service, {"replicas": 1, "restart_count_this_run": 0, "error_rate": 0.0, \
"memory_pct": 95}, medium: sole instance AND deep into severe memory exhaustion — \
the only copy of a badly-degraded process going offline is a real blast radius.
- restart_service, {"replicas": 2, "restart_count_this_run": 2, "error_rate": 0.3}, \
medium: already restarted twice this run without resolving the issue — a third blind \
restart on a thrashing service is a bigger call than a first attempt, even though it's \
redundant.
- clear_cache, {"replicas": 2, "restart_count_this_run": 0, "disk_pct": 32}, \
low: redundant service, disk usage near baseline — a cache clear on one of several \
instances, for a minor issue, is low-impact.
- clear_cache, {"replicas": 1, "restart_count_this_run": 0, "disk_pct": 95}, \
medium: the sole instance, and disk usage is critically full — every request hits a \
cold cache until it warms back up, with no redundant instance to absorb that.

If `situational.has_redundant_replica` is false AND `situational.restart_count_this_run` \
is 1 or more (this is a repeat attempt on a sole instance that didn't recover), you may \
not classify the action "low" — choose medium or high based on the other factors instead. \
A policy layer downstream enforces this floor regardless of what you say in that specific \
case. Outside that case — including a *first* attempt on a sole instance — judge low vs. \
medium the same way you would with redundancy: from actual severity, not replica count alone.

As a rough guide: anything that permanently deletes data or changes what's \
deployed is at least medium and often high."""


class RiskClassifier(Protocol):
    async def classify(
        self,
        proposed_action: str,
        diagnosis: str,
        rationale: str | None,
        situational: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Returns a dict shaped like RiskClassificationOutput.model_dump().
        `situational` is src.risk.policy.situational_features' output for
        this action's target (None for delete_records)."""
        ...


def _payload(
    proposed_action: str, diagnosis: str, rationale: str | None, situational: dict[str, Any] | None
) -> str:
    return json.dumps(
        {
            "proposed_action": proposed_action,
            "diagnosis": diagnosis,
            "rationale": rationale,
            "situational": situational,
        },
        sort_keys=True,
    )


class AnthropicRiskClassifier:
    def __init__(self, model: str = "claude-sonnet-5") -> None:
        from langchain_anthropic import ChatAnthropic

        self.model = model
        self._chat = ChatAnthropic(model=model).with_structured_output(RiskClassificationOutput)

    async def classify(
        self,
        proposed_action: str,
        diagnosis: str,
        rationale: str | None,
        situational: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        messages = [("system", SYSTEM_PROMPT), ("human", _payload(proposed_action, diagnosis, rationale, situational))]
        result = await self._chat.ainvoke(messages)
        return result.model_dump()


class OpenAIRiskClassifier:
    """Stand-in for AnthropicRiskClassifier backed by the OpenAI API
    (structured outputs via chat.completions.parse) instead of
    Anthropic's, for environments with an OpenAI key but no
    ANTHROPIC_API_KEY."""

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self._client = AsyncOpenAI(api_key=api_key)

    async def classify(
        self,
        proposed_action: str,
        diagnosis: str,
        rationale: str | None,
        situational: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _payload(proposed_action, diagnosis, rationale, situational)},
            ],
            response_format=RiskClassificationOutput,
        )
        return response.choices[0].message.parsed.model_dump()


class OllamaRiskClassifier:
    """Free, local stand-in for AnthropicRiskClassifier: same prompt and
    structured-output contract, backed by an open-weight model served by
    Ollama (https://ollama.com) instead of a paid API."""

    def __init__(self, model: str = "llama3.1:8b", base_url: str = "http://localhost:11434") -> None:
        self.model = model
        self.base_url = base_url

    async def classify(
        self,
        proposed_action: str,
        diagnosis: str,
        rationale: str | None,
        situational: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from src.llm.ollama_client import structured_chat

        result = await structured_chat(
            self.model,
            SYSTEM_PROMPT,
            _payload(proposed_action, diagnosis, rationale, situational),
            RiskClassificationOutput,
            base_url=self.base_url,
        )
        return result.model_dump()
