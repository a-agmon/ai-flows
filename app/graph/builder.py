"""Compile a :class:`FlowConfig` into an executable LangGraph graph.

Topology
--------
The public schema is stage-based. Each stage is given a hidden *entry* node that
fans out to the stage's real nodes:

    START -> entry(s1) -> [nodes of s1] -> entry(s2) -> [nodes of s2] -> ... -> END

Nodes within a stage share the entry node as their sole predecessor, so
LangGraph runs them concurrently (fan-out); the next stage's entry waits for all
of them (join). The entry node exists so that conditional routing always targets
a single node -- LangGraph's conditional edges cannot fan out to a list of nodes.

Early termination (``end_if``) adds a hidden *router* node after a stage whose
conditional edges go either to ``END`` or to the next stage's entry. Stages
without ``end_if`` are wired with plain edges. ``when`` is handled inside the
node functions (see ``nodes.py``) and never changes the topology here.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config.models import FlowConfig, StageConfig
from app.graph.conditions import evaluate_condition
from app.graph.nodes import create_node_fn
from app.graph.state import STATE_KEY, FlowState


async def _passthrough(graph_state: dict[str, Any]) -> dict[str, Any]:
    """A hidden entry node: contributes nothing, just provides a join point."""
    return {STATE_KEY: {}}


def _make_router(stage: StageConfig):
    """Return ``(router_node_fn, route_fn)`` for a stage that has ``end_if``."""
    condition = stage.end_if

    async def router_node(graph_state: dict[str, Any]) -> dict[str, Any]:
        state = graph_state[STATE_KEY]
        if evaluate_condition(condition, state):
            return {STATE_KEY: {"_flow_status": "ended",
                               "_completion_reason": condition.reason}}
        return {STATE_KEY: {}}

    def route(graph_state: dict[str, Any]) -> str:
        ended = graph_state[STATE_KEY].get("_flow_status") == "ended"
        return "end" if ended else "continue"

    return router_node, route


def build_graph(config: FlowConfig):
    """Build and compile the LangGraph graph for a flow."""
    graph = StateGraph(FlowState)

    # ``exit_`` describes how the previously added stage hands off to the next
    # target: either ("plain", [node ids]) or ("router", router_id, route_fn).
    # ``None`` means we are still at the graph entrypoint.
    exit_: tuple | None = None

    for stage in config.stages:
        entry_id = f"__entry_{stage.id}"
        graph.add_node(entry_id, _passthrough)

        node_ids: list[str] = []
        for node in stage.nodes:
            graph.add_node(node.id, create_node_fn(node))
            graph.add_edge(entry_id, node.id)
            node_ids.append(node.id)

        _connect(graph, exit_, entry_id)

        if stage.end_if is not None:
            router_id = f"__router_{stage.id}"
            router_node, route_fn = _make_router(stage)
            graph.add_node(router_id, router_node)
            for node_id in node_ids:
                graph.add_edge(node_id, router_id)
            exit_ = ("router", router_id, route_fn)
        else:
            exit_ = ("plain", node_ids)

    _connect(graph, exit_, END)
    return graph.compile()


def _connect(graph: StateGraph, exit_: tuple | None, target: str) -> None:
    """Wire the previous stage's exit to ``target`` (a node id or ``END``)."""
    if exit_ is None:
        graph.add_edge(START, target)
    elif exit_[0] == "plain":
        for source in exit_[1]:
            graph.add_edge(source, target)
    else:  # ("router", router_id, route_fn)
        _, router_id, route_fn = exit_
        # Both branches resolve to a single node (or END), as LangGraph requires.
        graph.add_conditional_edges(
            router_id, route_fn, {"end": END, "continue": target}
        )
