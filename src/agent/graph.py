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
from src.agent.runtime import AgentRuntimeContext
from src.agent.state import AgentState
from src.audit.hook import with_audit

NODES = {
    "diagnose": nodes.diagnose,
    "propose_action": nodes.propose_action,
    "classify_risk": nodes.classify_risk,
    "mark_awaiting_approval": nodes.mark_awaiting_approval,
    "await_approval": nodes.await_approval,
    "revalidate": nodes.revalidate,
    "prepare_execution": nodes.prepare_execution,
    "execute": nodes.execute,
    "verify": nodes.verify,
    "rolling_back": nodes.rolling_back,
    "rolled_back": nodes.rolled_back,
    "replan": nodes.replan,
    "escalate": nodes.escalate,
    "completed": nodes.completed,
}


def build_graph(checkpointer, *, revalidate: bool = True) -> CompiledStateGraph:
    """`revalidate=False` builds the --no-revalidation ablation (PROJECT_SPEC.md
    section 11): the revalidate node is left out of the graph entirely and
    an approved/edited decision routes straight to prepare_execution, so
    an approved action is executed blindly against whatever the world
    looks like at resume time. See src/eval/runner.py."""
    graph = StateGraph(AgentState, context_schema=AgentRuntimeContext)

    node_names = dict(NODES)
    if not revalidate:
        del node_names["revalidate"]

    for name, fn in node_names.items():
        graph.add_node(name, with_audit(name, fn))

    graph.add_edge(START, "diagnose")
    graph.add_edge("diagnose", "propose_action")
    graph.add_edge("propose_action", "classify_risk")
    graph.add_conditional_edges(
        "classify_risk", nodes.route_after_risk_classification, ["prepare_execution", "mark_awaiting_approval"]
    )
    graph.add_edge("mark_awaiting_approval", "await_approval")

    if revalidate:
        graph.add_conditional_edges(
            "await_approval", nodes.route_after_approval, ["revalidate", "replan", "escalate"]
        )
        graph.add_conditional_edges(
            "revalidate", nodes.route_after_revalidation, ["replan", "prepare_execution"]
        )
    else:
        graph.add_conditional_edges(
            "await_approval",
            nodes.route_after_approval_no_revalidation,
            ["prepare_execution", "replan", "escalate"],
        )

    graph.add_edge("prepare_execution", "execute")
    graph.add_edge("execute", "verify")
    graph.add_conditional_edges("verify", nodes.route_after_verification, ["completed", "rolling_back"])
    graph.add_edge("rolling_back", "rolled_back")
    graph.add_edge("rolled_back", "replan")
    graph.add_conditional_edges("replan", nodes.route_after_replan, ["propose_action", "escalate"])
    graph.add_edge("completed", END)
    graph.add_edge("escalate", END)

    return graph.compile(checkpointer=checkpointer)


async def start_workflow(
    graph: CompiledStateGraph,
    run_id: str,
    initial_state: dict[str, Any],
    context: AgentRuntimeContext,
) -> dict[str, Any]:
    """Starts a new run on thread_id=run_id and drives it up to either a
    terminal state or the approval interrupt."""
    config = {"configurable": {"thread_id": run_id}}
    return await graph.ainvoke(
        {"run_id": run_id, **initial_state}, config, context=context, durability="sync"
    )


async def resume_workflow(
    graph: CompiledStateGraph,
    run_id: str,
    decision: dict[str, Any],
    context: AgentRuntimeContext,
) -> dict[str, Any]:
    """Resumes a suspended run purely by thread_id; the caller need not
    have any in-memory state from when the run was started (see
    tests/test_graph_cross_process.py)."""
    config = {"configurable": {"thread_id": run_id}}
    return await graph.ainvoke(Command(resume=decision), config, context=context, durability="sync")
