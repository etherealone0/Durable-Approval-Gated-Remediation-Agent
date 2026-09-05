"""Minimal client for a locally-served Ollama model's /api/chat endpoint,
used as a free, open-weight stand-in for AnthropicDiagnosisReasoner /
AnthropicRiskClassifier when no ANTHROPIC_API_KEY is available. Talks to
Ollama's native API directly (json-schema `format` constraint) rather than
pulling in langchain-ollama/langchain-openai as a dependency, since httpx
is already used elsewhere in this project.
"""

from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_BASE_URL = "http://localhost:11434"


async def structured_chat(
    model: str,
    system: str,
    user: str,
    output_type: type[T],
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 1800.0,
) -> T:
    """Runs one chat completion constrained to `output_type`'s JSON schema
    and parses the result into it. Raises if Ollama isn't reachable or the
    model hasn't been pulled (httpx.HTTPStatusError / ConnectError)."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": output_type.model_json_schema(),
        "stream": False,
        "options": {"temperature": 0},
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        resp = await client.post("/api/chat", json=payload)
        resp.raise_for_status()
    content = resp.json()["message"]["content"]
    return output_type.model_validate(json.loads(content))
