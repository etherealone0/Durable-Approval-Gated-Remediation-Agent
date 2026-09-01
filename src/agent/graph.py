"""Wires the state machine from PROJECT_SPEC.md section 4 into a LangGraph
StateGraph, and the two entrypoints (start/resume) that always drive it
with durability="sync" so an approval-gate transition is persisted before
the process can die (section 5).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from src.agent import nodes
from src.agent.state import AgentState


def build_graph(checkpointer) -> CompiledStateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("diagnose", nodes.diagnose)
    graph.add_node("propose_action", nodes.propose_action)
    graph.add_node("classify_risk", nodes.classify_risk)
    graph.add_node("mark_awaiting_approval", nodes.mark_awaiting_approval)
    graph.add_node("await_approval", nodes.await_approval)
    graph.add_node("revalidate", nodes.revalidate)
    graph.add_node("execute", nodes.execute)
    graph.add_node("verify", nodes.verify)
    graph.add_node("rolling_back", nodes.rolling_back)
    graph.add_node("rolled_back", nodes.rolled_back)
    graph.add_node("replan", nodes.replan)
    graph.add_node("escalate", nodes.escalate)
    graph.add_node("completed", nodes.completed)

    graph.add_edge(START, "diagnose")
    graph.add_edge("diagnose", "propose_action")
    graph.add_edge("propose_action", "classify_risk")
    graph.add_conditional_edges(
        "classify_risk", nodes.route_after_risk_classification, ["execute", "mark_awaiting_approval"]
    )
    graph.add_edge("mark_awaiting_approval", "await_approval")
    graph.add_conditional_edges(
        "await_approval", nodes.route_after_approval, ["revalidate", "replan", "escalate"]
    )
    graph.add_conditional_edges("revalidate", nodes.route_after_revalidation, ["replan", "execute"])
    graph.add_edge("execute", "verify")
    graph.add_conditional_edges("verify", nodes.route_after_verification, ["completed", "rolling_back"])
    graph.add_edge("rolling_back", "rolled_back")
    graph.add_edge("rolled_back", "replan")
    graph.add_conditional_edges("replan", nodes.route_after_replan, ["propose_action", "escalate"])
    graph.add_edge("completed", END)
    graph.add_edge("escalate", END)

    return graph.compile(checkpointer=checkpointer)


async def start_workflow(graph: CompiledStateGraph, run_id: str, initial_state: dict[str, Any]) -> dict[str, Any]:
    """Starts a new run on thread_id=run_id and drives it up to either a
    terminal state or the approval interrupt."""
    config = {"configurable": {"thread_id": run_id}}
    return await graph.ainvoke({"run_id": run_id, **initial_state}, config, durability="sync")


async def resume_workflow(graph: CompiledStateGraph, run_id: str, decision: dict[str, Any]) -> dict[str, Any]:
    """Resumes a suspended run purely by thread_id; the caller need not
    have any in-memory state from when the run was started (see
    tests/test_graph_cross_process.py)."""
    config = {"configurable": {"thread_id": run_id}}
    return await graph.ainvoke(Command(resume=decision), config, durability="sync")
