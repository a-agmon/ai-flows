"""Bad configs must fail loudly at load/validate time, not at request time."""

import pytest

from app.config.models import FlowConfig
from app.config.validator import validate_flow
from app.errors import ConfigError
from app.graph.builder import build_graph
from app.graph.registry import GraphRegistry


def _module_node(node_id, output_key="out"):
    return {
        "id": node_id,
        "type": "module",
        "module": "ocr",
        "function": "extract_text",
        "output_key": output_key,
        "inputs": {"file_url": "file_url"},
    }


def _flow(**overrides):
    base = {
        "id": "f",
        "route": "/agents/f",
        "inputs": {"file_url": {"type": "string", "required": True}},
        "outputs": ["out"],
        "stages": [{"id": "s", "nodes": [_module_node("n")]}],
    }
    base.update(overrides)
    return FlowConfig.model_validate(base)


def test_unknown_field_is_rejected():
    with pytest.raises(Exception):
        FlowConfig.model_validate(
            {"id": "f", "route": "/x", "stages": [], "bogus": 1}
        )


def test_missing_prompt_file_fails_validation():
    flow = _flow(
        outputs=["text"],
        stages=[{
            "id": "s",
            "nodes": [{
                "id": "n", "type": "llm", "model": "m",
                "prompt_file": "does_not_exist.md", "output_key": "text",
            }],
        }],
    )
    with pytest.raises(ConfigError, match="does not exist"):
        validate_flow(flow)


def test_unknown_module_function_fails_validation():
    flow = _flow(stages=[{"id": "s", "nodes": [{
        "id": "n", "type": "module", "module": "ocr",
        "function": "no_such_fn", "output_key": "out",
        "inputs": {"file_url": "file_url"},
    }]}])
    with pytest.raises(ConfigError, match="no callable"):
        validate_flow(flow)


def test_duplicate_node_ids_fail_validation():
    flow = _flow(stages=[{"id": "s", "nodes": [_module_node("dup"), _module_node("dup", "out2")]}])
    with pytest.raises(ConfigError, match="duplicate node id"):
        validate_flow(flow)


def test_undeclared_output_fails_validation():
    flow = _flow(outputs=["nonexistent"])
    with pytest.raises(ConfigError, match="not produced"):
        validate_flow(flow)


def test_duplicate_route_rejected_by_registry():
    reg = GraphRegistry()
    a = _flow(id="a", route="/agents/dup")
    b = _flow(id="b", route="/agents/dup")
    reg.register(a, build_graph(a))
    with pytest.raises(ConfigError, match="duplicate route"):
        reg.register(b, build_graph(b))
