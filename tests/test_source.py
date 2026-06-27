"""Tests for the flow-level data source (`query` + `source`).

Covers the three things that make the feature work: data is injected into state,
explicit request params override injected data (so a flow can also accept the data
as a param), and bad source configs fail at validation time.
"""

import pytest

from app.config.models import FlowConfig
from app.config.validator import validate_flow
from app.errors import ConfigError, NodeExecutionError
from app.graph.builder import build_graph
from app.graph.registry import RegisteredFlow
from app.graph.runner import build_initial_state, run_flow

FLOW = {
    "id": "ticket_flow",
    "route": "/agents/ticket-flow",
    "inputs": {"ticket_id": {"type": "string", "required": True}},
    "query": "SELECT * FROM tickets WHERE id = '{{ ticket_id }}'",
    "source": {"module": "datasource", "function": "fetch_ticket"},
    "outputs": ["subject", "priority", "triage"],
    "stages": [
        {
            "id": "route",
            "nodes": [
                {
                    "id": "triage",
                    "type": "module",
                    "module": "datasource",
                    "function": "triage_ticket",
                    "merge_output": True,
                    "inputs": {"found": "ticket_found"},
                }
            ],
        }
    ],
}


def _build(flow=FLOW) -> RegisteredFlow:
    config = FlowConfig.model_validate(flow)
    validate_flow(config)
    return RegisteredFlow(config=config, graph=build_graph(config), version="test")


async def test_source_injects_data_into_state():
    entry = _build()
    result = await run_flow(entry, {"ticket_id": "T-100"}, include_state=True)

    # Fields the caller never sent -- the source fetched them.
    assert result["output"]["subject"] == "Refund for a delayed order"
    assert result["output"]["priority"] == "high"
    assert result["output"]["triage"] == {"priority": "high", "queue": "urgent"}


async def test_explicit_param_overrides_source():
    """A caller can pass the data directly to override what the source fetches."""
    entry = _build()
    result = await run_flow(
        entry, {"ticket_id": "T-100", "priority": "low"}, include_state=True
    )
    # Source returned priority=high for T-100, but the explicit param wins.
    assert result["output"]["priority"] == "low"
    assert result["output"]["triage"]["queue"] == "standard"


async def test_source_not_found_path():
    entry = _build()
    result = await run_flow(entry, {"ticket_id": "missing"})
    assert result["output"]["triage"] == {"queue": "not_found"}


async def test_query_is_rendered_over_params():
    """The query template is rendered against params before reaching the source."""
    config = FlowConfig.model_validate(FLOW)
    captured = {}

    async def fake_fetch(query, params, config):
        captured["query"] = query
        return {"ticket_found": True, "priority": "low"}

    import app.graph.nodes as nodes

    # Patch the resolver so the source uses our spy instead of datasource.fetch_ticket.
    original = nodes.import_module_function
    nodes.import_module_function = lambda module, function: fake_fetch
    try:
        state = await build_initial_state(config, {"ticket_id": "T-100"}, "run-1")
    finally:
        nodes.import_module_function = original

    assert "T-100" in captured["query"]
    assert state["priority"] == "low"


def test_query_without_source_is_rejected():
    bad = {**FLOW}
    bad.pop("source")
    with pytest.raises(ValueError, match="no 'source'"):
        FlowConfig.model_validate(bad)


def test_unknown_source_function_fails_validation():
    bad = {**FLOW, "source": {"module": "datasource", "function": "nope"}}
    with pytest.raises(ConfigError, match="no callable"):
        validate_flow(FlowConfig.model_validate(bad))


async def test_source_must_return_dict():
    bad = {**FLOW, "source": {"module": "datasource", "function": "triage_ticket"}}
    # triage_ticket expects node-style kwargs, so calling it as a source raises;
    # the runtime normalizes that into a NodeExecutionError tagged __source__.
    entry = _build(bad)
    with pytest.raises(NodeExecutionError):
        await run_flow(entry, {"ticket_id": "T-100"})
