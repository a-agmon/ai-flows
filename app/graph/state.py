"""The LangGraph state container.

The flow's shared state is a free-form ``dict`` whose keys are defined by the
YAML (inputs, node ``output_key``s, dynamic ``merge_output`` keys). LangGraph
needs a declared schema with merge semantics to *accumulate* updates across
steps, so we hold the entire flow state inside a single reducer-merged channel
(:data:`STATE_KEY`) and merge partial updates into it.

Node functions never see this wrapper -- the node wrapper in ``nodes.py`` and
the runner unwrap/rewrap it -- so authors still think in terms of a plain dict.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

# The single channel that holds the whole flow state.
STATE_KEY = "_data"


def _merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Reducer: merge a node's partial update into the accumulated state.

    Also used to combine the concurrent updates of nodes in a parallel stage;
    since those write distinct keys, the merge is order-independent.
    """
    return {**left, **right}


class FlowState(TypedDict):
    _data: Annotated[dict[str, Any], _merge]


def wrap(state: dict[str, Any]) -> FlowState:
    return {STATE_KEY: state}


def unwrap(graph_state: dict[str, Any]) -> dict[str, Any]:
    return graph_state[STATE_KEY]
