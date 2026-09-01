"""Run-scoped dependencies injected into graph nodes via LangGraph's
context mechanism (langgraph.runtime.get_runtime) rather than stashed in
the persisted state, since things like HTTP clients and LLM clients
aren't checkpoint-serializable and shouldn't be anyway.

Grows in later prompts as more nodes stop being stubs (e.g. a risk
classifier in Prompt 7, tool executors in Prompt 9).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agent.diagnosis import DiagnosisReasoner
from src.tools.context import ToolContext


@dataclass
class AgentRuntimeContext:
    tool_ctx: ToolContext
    reasoner: DiagnosisReasoner
