"""Compile a :class:`FlowConfig` into an executable LangGraph graph.

Topology
--------
The public schema is stage-based. Each stage is given a hidden *entry* node that
evaluates the stage ``when`` guard and fans out to the stage's real nodes:

    START -> entry(s1) -> [nodes of s1] -> entry(s2) -> [nodes of s2] -> ... -> END

In a ``parallel: true`` stage the nodes share the entry node as their sole
predecessor, so LangGraph runs them concurrently (fan-out) and the next stage's
entry waits for all of them (join). In a ``parallel: false`` stage the nodes are
chained entry -> n1 -> n2 -> ... so each sees the previous node's output (e.g. a
producer node followed by one that reads what it wrote). The entry node also
ensures conditional routing always targets a single node -- LangGraph's
conditional edges cannot fan out to a list of nodes.

Early termination (``end_if``) adds a hidden *router* node after a stage whose
conditional edges go either to ``END`` or to the next stage's entry. Stages
without ``end_if`` are wired with plain edges. Node ``when`` guards are handled
inside the node functions (see ``nodes.py``) and never change the topology here.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config.models import FlowConfig, StageConfig
from app.graph.conditions import evaluate_condition
from app.graph.nodes import create_node_fn
from app.graph.state import STATE_KEY, FlowState


def _stage_skip_key(stage: StageConfig) -> str:
    return f"_stage_skipped_{stage.id}"


def _make_entry(stage: StageConfig):
    """Return a hidden entry node that marks whether its stage is skipped."""

    async def entry(graph_state: dict[str, Any]) -> dict[str, Any]:
        skipped = False
        if stage.when is not None:
            skipped = not evaluate_condition(stage.when, graph_state[STATE_KEY])
        return {STATE_KEY: {_stage_skip_key(stage): skipped}}

    return entry


def _make_router(stage: StageConfig):
    """Return ``(router_node_fn, route_fn)`` for a stage that has ``end_if``."""
    condition = stage.end_if
    skip_key = _stage_skip_key(stage)

    async def router_node(graph_state: dict[str, Any]) -> dict[str, Any]:
        state = graph_state[STATE_KEY]
        if not state.get(skip_key) and evaluate_condition(condition, state):
            return {
                STATE_KEY: {
                    "_flow_status": "ended",
                    "_completion_reason": condition.reason,
                }
            }
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
        graph.add_node(entry_id, _make_entry(stage))

        node_ids: list[str] = []
        for node in stage.nodes:
            graph.add_node(
                node.id, create_node_fn(node, stage_skip_key=_stage_skip_key(stage))
            )
            node_ids.append(node.id)

        _connect(graph, exit_, entry_id)

        # Wire the nodes within the stage according to ``parallel``.
        if stage.parallel:
            # Fan out: all nodes run concurrently from the entry; the next
            # stage joins on all of them.
            for node_id in node_ids:
                graph.add_edge(entry_id, node_id)
            leaf_node_ids = node_ids
        else:
            # Sequential: chain the nodes so each sees the previous one's
            # output (e.g. a producer node followed by a node that reads it).
            prev_node = entry_id
            for node_id in node_ids:
                graph.add_edge(prev_node, node_id)
                prev_node = node_id
            leaf_node_ids = [node_ids[-1]]

        if stage.end_if is not None:
            router_id = f"__router_{stage.id}"
            router_node, route_fn = _make_router(stage)
            graph.add_node(router_id, router_node)
            for node_id in leaf_node_ids:
                graph.add_edge(node_id, router_id)
            exit_ = ("router", router_id, route_fn)
        else:
            exit_ = ("plain", leaf_node_ids)

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
