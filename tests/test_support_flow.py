"""End-to-end tests for the `support_reply` example flow (LLM mocked).

Reads the real shipped config from ``configs/support_reply.yaml`` -- including
its folder-nested prompts and logging module -- and runs it through the runner.
"""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

import app.graph.nodes as nodes
from app.config.loader import load_flow_file
from app.config.validator import validate_flow
from app.graph.builder import build_graph
from app.graph.registry import RegisteredFlow
from app.graph.runner import run_flow

CONFIG = Path(__file__).parent.parent / "configs" / "support_reply.yaml"


def _respond(prompt: str) -> str:
    p = prompt.lower()
    if "triaging" in p:  # support/classify.md
        if "sue" in p or "legal action" in p:
            return json.dumps({
                "category": "other", "urgency": "high",
                "can_handle": False, "rejection_reason": "needs legal review",
            })
        return json.dumps({
            "category": "billing", "urgency": "high",
            "can_handle": True, "rejection_reason": None,
        })
    if "polish this customer support reply" in p:  # support/polish.md
        return "FINAL REPLY"
    if "write a helpful reply" in p:  # support/draft.md
        return "DRAFT BODY"
    if "disclaimer" in p:  # support/disclaimer.md
        return "DISCLAIMER LINE"
    return "UNMATCHED"


class ScriptedLLM:
    async def ainvoke(self, prompt: str) -> AIMessage:
        return AIMessage(content=_respond(prompt))


@pytest.fixture
def mock_llm(monkeypatch):
    monkeypatch.setattr(nodes, "create_llm", lambda **kw: ScriptedLLM())


def _build() -> RegisteredFlow:
    config = load_flow_file(CONFIG)
    validate_flow(config)
    return RegisteredFlow(config=config, graph=build_graph(config), version="test")


async def test_happy_path(mock_llm):
    entry = _build()
    result = await run_flow(
        entry,
        {"customer_message": "I was double charged and want a refund"},
        include_state=True,
    )
    assert result["status"] == "completed"
    assert result["output"]["category"] == "billing"
    assert result["output"]["final_reply"] == "FINAL REPLY"
    # Optional disclaimer node is skipped by default (include_disclaimer=false).
    assert "disclaimer" not in result["state"]


async def test_ends_early_when_needs_human(mock_llm):
    entry = _build()
    result = await run_flow(
        entry, {"customer_message": "I will take legal action and sue you"}
    )
    assert result["status"] == "ended"
    assert result["completion_reason"] == "needs_human"
    assert result["output"]["category"] == "other"
    assert "final_reply" not in result["output"]


async def test_optional_disclaimer_node_runs_when_requested(mock_llm):
    entry = _build()
    result = await run_flow(
        entry,
        {"customer_message": "a billing question", "include_disclaimer": True},
        include_state=True,
    )
    assert result["state"]["disclaimer"] == "DISCLAIMER LINE"
    assert result["output"]["final_reply"] == "FINAL REPLY"
