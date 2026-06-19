"""End-to-end test of the graph builder + runner using module-only nodes.

This exercises the riskiest wiring without needing an LLM/API key: parallel
fan-out within a stage, the join into the next stage, ``end_if`` early
termination, ``when`` node skipping, and ``merge_output``.
"""

import pytest

from app.config.models import FlowConfig
from app.config.validator import validate_flow
from app.graph.builder import build_graph
from app.graph.registry import RegisteredFlow
from app.graph.runner import run_flow

FLOW = {
    "id": "test_flow",
    "route": "/agents/test-flow",
    "inputs": {
        "file_url": {"type": "string", "required": True},
        "classification": {"type": "string", "required": True},
    },
    "outputs": ["request_status", "rejection_reason", "a", "b", "combined", "skipped"],
    "stages": [
        {
            "id": "classify",
            "parallel": False,
            "nodes": [
                {
                    "id": "unpack",
                    "type": "module",
                    "module": "transforms",
                    "function": "unpack_classification",
                    "merge_output": True,
                    "inputs": {"classification": "classification"},
                }
            ],
            "end_if": {
                "field": "request_status",
                "equals": "unsupported",
                "reason": "rejected",
            },
        },
        {
            "id": "fanout",
            "parallel": True,
            "nodes": [
                {
                    "id": "node_a",
                    "type": "module",
                    "module": "ocr",
                    "function": "extract_text",
                    "output_key": "a",
                    "inputs": {"file_url": "file_url"},
                },
                {
                    "id": "node_b",
                    "type": "module",
                    "module": "ocr",
                    "function": "extract_text",
                    "output_key": "b",
                    "inputs": {"file_url": "file_url"},
                },
                {
                    "id": "node_skip",
                    "type": "module",
                    "module": "ocr",
                    "function": "extract_text",
                    "output_key": "skipped",
                    "inputs": {"file_url": "file_url"},
                    "when": {"field": "file_url", "equals": "__never__"},
                },
            ],
        },
        {
            "id": "join",
            "parallel": False,
            "nodes": [
                {
                    "id": "assemble",
                    "type": "module",
                    "module": "transforms",
                    "function": "assemble_letter",
                    "output_key": "combined",
                    "inputs": {"intro": "a", "closing": "b"},
                }
            ],
        },
    ],
}


def _build() -> RegisteredFlow:
    config = FlowConfig.model_validate(FLOW)
    validate_flow(config)
    graph = build_graph(config)
    return RegisteredFlow(config=config, graph=graph, version="test")


async def test_completes_when_supported():
    entry = _build()
    result = await run_flow(
        entry,
        {"file_url": "doc://1", "classification": '{"request_status": "supported"}'},
        include_state=True,
    )
    assert result["status"] == "completed"
    assert result["completion_reason"] == "end_reached"
    # Parallel stage ran and joined.
    assert result["output"]["a"] == "[extracted text from doc://1]"
    assert result["output"]["b"] == "[extracted text from doc://1]"
    assert "doc://1" in result["output"]["combined"]
    # `when`-guarded node was skipped, so its key is absent.
    assert "skipped" not in result["output"]
    assert "skipped" not in result["state"]


async def test_ends_early_when_unsupported():
    entry = _build()
    result = await run_flow(
        entry,
        {
            "file_url": "doc://1",
            "classification": '{"request_status": "unsupported", "rejection_reason": "nope"}',
        },
    )
    assert result["status"] == "ended"
    assert result["completion_reason"] == "rejected"
    assert result["output"]["request_status"] == "unsupported"
    assert result["output"]["rejection_reason"] == "nope"
    # Downstream stages never ran.
    assert "a" not in result["output"]
    assert "combined" not in result["output"]


async def test_missing_required_input_rejected():
    from app.graph.runner import InputValidationError

    entry = _build()
    with pytest.raises(InputValidationError):
        await run_flow(entry, {"file_url": "doc://1"})  # no classification


async def test_stage_when_skips_all_nodes_and_continues():
    """A stage whose `when` is false must skip every node and must NOT fire its
    `end_if` router; the flow then continues to the next stage."""
    flow = FLOW.copy()
    flow["stages"] = [
        {
            "id": "skipped_stage",
            "when": {"field": "run_optional", "equals": True},
            "nodes": [
                {
                    "id": "skipped_stage_node",
                    "type": "module",
                    "module": "ocr",
                    "function": "extract_text",
                    "output_key": "a",
                    "inputs": {"file_url": "file_url"},
                }
            ],
            # Would end the flow if the (skipped) stage actually ran.
            "end_if": {
                "field": "request_status",
                "exists": False,
                "reason": "would_end_if_stage_ran",
            },
        },
        {
            "id": "next_stage",
            "nodes": [
                {
                    "id": "next_node",
                    "type": "module",
                    "module": "ocr",
                    "function": "extract_text",
                    "output_key": "b",
                    "inputs": {"file_url": "file_url"},
                }
            ],
        },
    ]
    flow["outputs"] = ["a", "b"]

    config = FlowConfig.model_validate(flow)
    validate_flow(config)
    entry = RegisteredFlow(config=config, graph=build_graph(config), version="test")

    # `run_optional` is absent, so the first stage is skipped.
    result = await run_flow(entry, {"file_url": "doc://1", "classification": "{}"})

    assert result["status"] == "completed"
    assert "a" not in result["output"]  # skipped stage produced nothing
    assert result["output"]["b"] == "[extracted text from doc://1]"
