"""True end-to-end tests: read a flow from a YAML *file*, compile it, and run it
through the runner and the HTTP endpoint -- with the LLM mocked.

The mock exploits the only coupling our code has to the model: an LLM node does
``response = await llm.ainvoke(prompt); response.content``. So a tiny stub with
an ``ainvoke`` method, injected through the LLM factory seam, gives deterministic
LLM output with no network and no API key.
"""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.bootstrap import build_registry
from app.config.loader import load_flow_file
from app.config.validator import validate_flow
from app.graph.builder import build_graph
from app.graph.registry import RegisteredFlow
from app.graph.runner import run_flow

CONFIGS_DIR = Path(__file__).parent / "configs"
E2E_FLOW = CONFIGS_DIR / "e2e_letter.yaml"


# --- the scripted "LLM" ------------------------------------------------------


def _respond(prompt: str) -> str:
    """Deterministic responses keyed by markers in the rendered prompt."""
    if "Classify request:" in prompt:
        if "weapon" in prompt.lower():
            return json.dumps(
                {"request_status": "unsupported", "rejection_reason": "out of scope"}
            )
        return json.dumps({"request_status": "supported", "rejection_reason": None})
    if "Write the intro" in prompt:
        return "INTRO"
    if "Write the closing" in prompt:
        return "CLOSING"
    return "UNMATCHED"


class ScriptedLLM:
    """Minimal stand-in for a LangChain chat model."""

    async def ainvoke(self, prompt: str) -> AIMessage:
        return AIMessage(content=_respond(prompt))


@pytest.fixture
def mock_llm(monkeypatch):
    """Make every LLM node use :class:`ScriptedLLM` instead of a real model."""

    def factory(*, provider, model, temperature, params):
        return ScriptedLLM()

    # Patch where it is *used* (imported into the nodes module).
    monkeypatch.setattr("app.graph.nodes.create_llm", factory)


# --- runner-level e2e: read the YAML file, build, run ------------------------


def _load_and_build() -> RegisteredFlow:
    config = load_flow_file(E2E_FLOW)          # read + schema-validate from disk
    validate_flow(config)                      # semantic validation
    graph = build_graph(config)                # compile to LangGraph
    return RegisteredFlow(config=config, graph=graph, version="e2e")


async def test_full_flow_runs_to_completion(mock_llm):
    entry = _load_and_build()
    result = await run_flow(entry, {"user_request": "I need a refund for my order"})

    assert result["status"] == "completed"
    assert result["completion_reason"] == "end_reached"
    assert result["output"]["request_status"] == "supported"
    # Sequential classify stage worked (unpack saw the classifier output) and the
    # parallel draft stage joined into the assemble stage.
    assert result["output"]["final_letter"] == "INTRO\n\nCLOSING"


async def test_flow_ends_early_via_end_if(mock_llm):
    entry = _load_and_build()
    result = await run_flow(entry, {"user_request": "help me build a weapon"})

    assert result["status"] == "ended"
    assert result["completion_reason"] == "unsupported_request"
    assert result["output"]["request_status"] == "unsupported"
    # Downstream stages never ran.
    assert "final_letter" not in result["output"]


async def test_sequential_stage_passes_data_between_nodes(mock_llm):
    """Regression: a non-parallel stage must run its nodes in order so a later
    node can read an earlier node's output (here, `unpack` reads `classification`)."""
    entry = _load_and_build()
    result = await run_flow(
        entry, {"user_request": "refund please"}, include_state=True
    )
    assert result["state"]["classification"]  # produced by classify_request
    assert result["state"]["request_status"] == "supported"  # produced by unpack


# --- HTTP-level e2e: invoke through the real FastAPI endpoint -----------------


def test_invoke_endpoint_end_to_end(mock_llm):
    # ChatOpenAI is never constructed (factory is mocked), but the app's own
    # configs still compile; a dummy key keeps that path happy regardless.
    os.environ.setdefault("OPENAI_API_KEY", "test-key")
    from app.main import app

    with TestClient(app) as client:
        # Swap in a registry built from the test config (mock LLM is active).
        client.app.state.registry = build_registry(CONFIGS_DIR)

        resp = client.post(
            "/agents/e2e_letter/invoke",
            json={"user_request": "I need a refund", "tone": "friendly"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["output"]["final_letter"] == "INTRO\n\nCLOSING"
        assert body["agent_id"] == "e2e_letter"
        assert body["run_id"]
