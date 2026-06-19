# Quick start

This walks you from a clean checkout to running flows and authoring your own, in
a few minutes. For the full schema and reference, see the [README](README.md).

## 1. Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (or plain `pip`)
- An `OPENAI_API_KEY` **only if** you run flows that contain `llm` nodes. The
  steps below start with a module-only flow that needs no key.

## 2. Install

```bash
uv venv && source .venv/bin/activate
uv pip install fastapi "uvicorn[standard]" pydantic pydantic-settings structlog \
    pyyaml jinja2 langgraph langchain-openai langchain-core
```

## 3. Configure

```bash
cp .env.example .env
```

Open `.env` and set `OPENAI_API_KEY` if you'll use LLM nodes. You can also tune
logging and paths (all optional, defaults shown):

```bash
AI_FLOWS_LOG_FORMAT=console     # or `json` for production
AI_FLOWS_LOG_LEVEL=INFO
```

## 4. Run the server

```bash
uvicorn app.main:app --reload
```

On startup the service loads every `*.yaml` in `configs/`, compiles each into a
graph, and registers it. A **bad config fails startup** with a clear error —
that's intentional.

In another terminal:

```bash
curl localhost:8000/health
# {"status":"ok"}

curl localhost:8000/agents
# lists the built-in flows: letter_generation, ocr_summary
```

## 5. Call a built-in flow

Inspect a flow's contract first:

```bash
curl localhost:8000/agents/ocr_summary/schema
```

`ocr_summary` uses a placeholder OCR module node, so it runs **without an API
key** (its summarize stage is an LLM node — see the note below):

```bash
curl -X POST localhost:8000/agents/ocr_summary/invoke \
  -H 'content-type: application/json' \
  -d '{"file_url": "doc://invoice.pdf"}'
```

`letter_generation` is a richer LLM flow (classify → draft → polish) and needs a
key:

```bash
curl -X POST localhost:8000/agents/letter_generation/invoke \
  -H 'content-type: application/json' \
  -d '{"user_request": "I want a refund for my delayed order", "tone": "friendly"}'
```

Add `?include_state=true` to any `invoke` call to see the full final state
(useful while developing):

```bash
curl -X POST 'localhost:8000/agents/letter_generation/invoke?include_state=true' ...
```

## 6. Author your first flow (no API key needed)

Flows are just YAML. Create `configs/quickstart.yaml`:

```yaml
id: quickstart
name: Quickstart
route: /agents/quickstart

inputs:
  file_url:
    type: string
    required: true

outputs:
  - extracted_text

stages:
  - id: extract
    nodes:
      - id: run_ocr
        type: module
        module: ocr             # -> app/modules/ocr.py
        function: extract_text  # async def extract_text(inputs, state, config)
        inputs:
          file_url: file_url    # function input <- state key
        output_key: extracted_text
```

Restart the server (configs are loaded at startup), then:

```bash
curl -X POST localhost:8000/agents/quickstart/invoke \
  -H 'content-type: application/json' \
  -d '{"file_url": "doc://contract.pdf"}'
```

```json
{
  "agent_id": "quickstart",
  "run_id": "…",
  "status": "completed",
  "completion_reason": "end_reached",
  "output": { "extracted_text": "[extracted text from doc://contract.pdf]" }
}
```

### Add a second stage

Append an LLM stage that reads what the first stage wrote (this one needs
`OPENAI_API_KEY`). The next stage sees everything earlier stages produced:

```yaml
outputs:
  - extracted_text
  - summary

stages:
  - id: extract
    nodes:
      - id: run_ocr
        type: module
        module: ocr
        function: extract_text
        inputs: { file_url: file_url }
        output_key: extracted_text

  - id: summarize
    nodes:
      - id: summarize_text
        type: llm
        model: gpt-4.1-mini
        temperature: 0.2
        output_key: summary
        prompt: |
          Summarize the following in one sentence:
          {{ extracted_text }}
```

The prompt is a Jinja2 template rendered over the whole state, so
`{{ extracted_text }}` is the value the OCR node just wrote.

That's the core loop: **drop a YAML file in `configs/`, restart, call the
endpoint.** Add `when` to skip a node/stage and `end_if` to stop early — see the
[README](README.md#conditions) for conditions and the module-node contract.

## 7. Run the tests

No API key required — LLM nodes are mocked:

```bash
uv pip install pytest pytest-asyncio httpx
pytest
```

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Startup error mentioning a config file | A flow YAML is invalid; the message names the file, flow, and problem. |
| `404 unknown agent '…'` | The flow id in the URL doesn't match any `id:` in `configs/`. Use `GET /agents`. |
| `400 invalid_input` | A required input is missing from the request body. Check `GET /agents/{id}/schema`. |
| `500 node_execution_failed` | A node raised at runtime; the response includes the `node_id` and message (e.g. a prompt referencing a state key that doesn't exist). |
| LLM calls fail with auth errors | `OPENAI_API_KEY` is unset or invalid. Module-only flows don't need it. |
| A new flow isn't showing up | Configs load at **startup** — restart the server after adding/editing YAML. |
