"""Run-scoped dependencies injected into graph nodes via LangGraph's
context mechanism (langgraph.runtime.get_runtime) rather than stashed in
the persisted state, since things like HTTP clients and LLM clients
aren't checkpoint-serializable and shouldn't be anyway.

"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.agent.diagnosis import DiagnosisReasoner
from src.audit.store import AuditStore, InMemoryAuditStore
from src.risk.classifier import RiskClassifier
from src.tools.context import ToolContext


@dataclass
class AgentRuntimeContext:
    tool_ctx: ToolContext
    reasoner: DiagnosisReasoner
    risk_classifier: RiskClassifier
    audit_store: AuditStore = field(default_factory=InMemoryAuditStore)
