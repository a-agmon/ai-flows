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
# lists the built-in flows: letter_generation, ocr_summary, support_reply, ticket_triage
```

`support_reply` is the fullest example flow (folder-nested prompts, a logging
module, parallel + sequential stages, `when` and `end_if`) — a good one to read.

## 5. Call a built-in flow

Inspect a flow's contract first:

```bash
curl localhost:8000/agents/ocr_summary/schema
```

`ticket_triage` runs entirely on module nodes, so it works **without an API
key**. It also shows a flow-level **data source**: you send only a ticket id and
the flow fetches the ticket itself.

```bash
curl -X POST localhost:8000/agents/ticket_triage/invoke \
  -H 'content-type: application/json' \
  -d '{"ticket_id": "T-100"}'
# output includes subject/priority (fetched by the source) and a triage decision
```

`letter_generation` is a richer LLM flow (parallel paragraphs → merge →
guardrail) and needs a key. It takes a `discharge` object:

```bash
curl -X POST localhost:8000/agents/letter_generation/invoke \
  -H 'content-type: application/json' \
  -d '{"discharge": {"patient_name": "Jane Doe", "diagnosis": "...", "hospital_course": "...", "medications": [], "follow_up": [], "attending_physician": "Dr. Smith"}}'
```

Add `?include_state=true` to any `invoke` call to see the full final state
(useful while developing):

```bash
curl -X POST 'localhost:8000/agents/ticket_triage/invoke?include_state=true' \
  -H 'content-type: application/json' -d '{"ticket_id": "T-100"}'
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

### Let the flow load its own data

Instead of making the caller send everything, a flow can declare a `query` and a
`source` module that fetches data and injects it into state before the graph runs.
The caller then sends only a key:

```yaml
inputs:
  ticket_id: { type: string, required: true }

query: "SELECT subject, priority FROM tickets WHERE id = '{{ ticket_id }}'"

source:
  module: datasource          # -> app/modules/datasource.py
  function: fetch_ticket      # async def fetch_ticket(query, params, config) -> dict
```

The dict the source returns is merged into state, so later nodes (and prompts) see
`subject`, `priority`, etc. as if the caller had sent them. By default the source
runs every request and explicit params win on the merge; add a `when` guard to the
source to skip the fetch entirely when the caller supplies the data directly. See
[`configs/ticket_triage.yaml`](configs/ticket_triage.yaml) and the
[Data sources](README.md#data-sources-query--source) section.

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
