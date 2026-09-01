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

Do not consider how confident the diagnosis is — only the properties of the \
action itself. As a rough guide: a restart of one service is typically low or \
medium; anything that permanently deletes data or changes what's deployed is at \
least medium and often high."""


class RiskClassifier(Protocol):
    async def classify(self, proposed_action: str, diagnosis: str, rationale: str | None) -> dict[str, Any]:
        """Returns a dict shaped like RiskClassificationOutput.model_dump()."""
        ...


class AnthropicRiskClassifier:
    def __init__(self, model: str = "claude-sonnet-5") -> None:
        from langchain_anthropic import ChatAnthropic

        self._model = ChatAnthropic(model=model).with_structured_output(RiskClassificationOutput)

    async def classify(self, proposed_action: str, diagnosis: str, rationale: str | None) -> dict[str, Any]:
        payload = {"proposed_action": proposed_action, "diagnosis": diagnosis, "rationale": rationale}
        messages = [("system", SYSTEM_PROMPT), ("human", json.dumps(payload, sort_keys=True))]
        result = await self._model.ainvoke(messages)
        return result.model_dump()
