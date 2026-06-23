"""API-level tests: startup compiles the real configs and the read endpoints
behave. LLM invocation is not exercised here (no live model)."""

import os

import pytest
from fastapi.testclient import TestClient

# ChatOpenAI validates that a key exists at construction time (startup), though
# it is only used on a real call. A dummy value lets the graphs compile.
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_lists_both_flows(client):
    ids = {a["id"] for a in client.get("/agents").json()["agents"]}
    assert {"letter_generation", "ocr_summary"} <= ids


def test_schema_endpoint(client):
    schema = client.get("/agents/letter_generation/schema").json()
    assert schema["route"] == "/agents/letter-generation"
    assert "discharge" in schema["inputs"]
    assert schema["inputs"]["discharge"]["required"] is True
    assert "final_letter" in schema["outputs"]
    stage_ids = [s["id"] for s in schema["stages"]]
    assert stage_ids[0] == "write_paragraphs"


def test_unknown_agent_404(client):
    assert client.get("/agents/nope/schema").status_code == 404


def test_invoke_missing_required_input_returns_400(client):
    resp = client.post("/agents/letter_generation/invoke", json={})
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error"]["type"] == "invalid_input"
