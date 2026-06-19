# AI Flows

A FastAPI service that loads **YAML-defined agent flows** at startup, compiles
each into a [LangGraph](https://langchain-ai.github.io/langgraph/) graph, and
serves it over HTTP. Flows are authored in YAML so that non-engineers can build
and edit them.

## Mental model

```
YAML defines stages.
Stages contain nodes.
Nodes read and write a shared state dict.
Nodes in a stage run in parallel (or in order with `parallel: false`); the next stage sees everything written before it.
A node or stage can be skipped with `when`.
A flow can end early with `end_if`.
```

There are no edges to wire by hand: data moves only through shared state.

## Quick start

```bash
uv venv && source .venv/bin/activate
uv pip install fastapi "uvicorn[standard]" pydantic pydantic-settings structlog \
    pyyaml jinja2 langgraph langchain-openai langchain-core
cp .env.example .env        # then set OPENAI_API_KEY
uvicorn app.main:app --reload
```

Run the tests (no API key required):

```bash
uv pip install pytest pytest-asyncio httpx
pytest
```

The suite includes true end-to-end tests ([`tests/test_e2e.py`](tests/test_e2e.py))
that read a flow from a YAML file, compile it, and run it through both the runner
and the HTTP endpoint. LLM nodes are mocked by injecting a scripted stub through
the LLM factory seam (`app.graph.nodes.create_llm`), so flows containing LLM
nodes run deterministically with no model or API key.

## HTTP API

| Method & path                          | Description                          |
| -------------------------------------- | ------------------------------------ |
| `GET  /health`                         | Liveness check.                      |
| `GET  /agents`                         | List registered flows.               |
| `GET  /agents/{id}/schema`             | Inputs, outputs and stages of a flow.|
| `POST /agents/{id}/invoke`             | Run a flow. Body = the input payload.|
| `POST /agents/{id}/invoke?include_state=true` | Also return the full final state. |

Example:

```bash
curl -X POST localhost:8000/agents/letter_generation/invoke \
  -H 'content-type: application/json' \
  -d '{"user_request": "I want a refund for my delayed order", "tone": "friendly"}'
```

Response:

```json
{
  "agent_id": "letter_generation",
  "run_id": "…",
  "status": "completed",
  "completion_reason": "end_reached",
  "output": { "final_letter": "…", "request_status": "supported", "rejection_reason": null }
}
```

If a stage's `end_if` fires, `status` is `"ended"`, `completion_reason` is the
configured reason, and only the outputs produced so far are returned.

## Authoring a flow

Drop a `*.yaml` file in `configs/`. See
[`configs/letter_generation.yaml`](configs/letter_generation.yaml) for a full
example. Schema summary:

```yaml
id: my_flow                 # unique flow id
route: /agents/my-flow      # unique route
inputs:
  some_field: { type: string, required: true }
  tone:       { type: string, required: false, default: professional }
outputs: [final_text]       # keys returned to the caller (if present)
stages:
  - id: draft
    parallel: true          # true = nodes run concurrently; false = in order
    nodes:
      - id: write
        type: llm
        model: gpt-4.1-mini
        temperature: 0.2
        prompt_file: write.md          # OR an inline `prompt: |`
        output_key: final_text
```

### Node types

**LLM node** renders a Jinja2 prompt over the current state and calls a model.
The whole state is available to the template (`{{ some_field }}`). Use either
`prompt:` (inline) or `prompt_file:` (a file under `app/prompts/`).

**Module node** calls a Python function from `app/modules/`:

```yaml
- id: assemble
  type: module
  module: transforms          # app/modules/transforms.py
  function: assemble_letter
  inputs: { intro: intro_text, closing: closing_text }   # arg -> state key
  output_key: draft_letter     # OR `merge_output: true` to merge a returned dict
```

Function contract:

```python
async def assemble_letter(inputs: dict, state: dict, config: dict) -> dict | str:
    ...
```

Return a string (written to `output_key`) or a dict (written to `output_key`,
or merged into state with `merge_output: true`). Sync functions are supported
and run in a thread pool.

### Conditions

A condition is a single, code-free comparison against state:

```yaml
when:                  # on a node or stage: run only if true (else skipped)
  field: include_legal_disclaimer
  equals: true

end_if:                # on a stage: stop the flow after the stage if true
  field: request_status
  equals: unsupported
  reason: unsupported_request
```

Operators: `equals`, `not_equals`, `exists`, `contains`, `in`. `field` may use
dotted paths (`classification.request_status`). Exactly one operator per
condition.

## Configuration & logging

All runtime configuration is a Pydantic `Settings` object
([`app/settings.py`](app/settings.py)) read from environment variables (and an
optional `.env`). App settings use the `AI_FLOWS_` prefix:

| Variable                   | Default        | Purpose                                  |
| -------------------------- | -------------- | ---------------------------------------- |
| `AI_FLOWS_LOG_LEVEL`       | `INFO`         | Root log level.                          |
| `AI_FLOWS_LOG_FORMAT`      | `console`      | `console` (human) or `json` (production).|
| `AI_FLOWS_CONFIGS_DIR`     | `./configs`    | Where flow YAML files are loaded from.   |
| `AI_FLOWS_PROMPTS_DIR`     | `./app/prompts`| Where `prompt_file` templates live.      |
| `AI_FLOWS_MODULES_PACKAGE` | `app.modules`  | Package that `module` nodes import from. |

`OPENAI_API_KEY` is read separately by the OpenAI client (no prefix).

Logging uses [structlog](https://www.structlog.org/)
([`app/logging_config.py`](app/logging_config.py)). Application code and
stdlib/third-party logs are rendered through the same pipeline. Each flow run
emits structured events with `run_id`, `agent_id`, `node_id`, `node_type`,
`duration_ms`, and `status`. Example (`AI_FLOWS_LOG_FORMAT=json`):

```json
{"event": "node executed", "run_id": "…", "agent_id": "letter_generation",
 "node_id": "write_intro", "node_type": "llm", "duration_ms": 812.4,
 "status": "ok", "level": "info", "timestamp": "…"}
```

## Project layout

```
app/
  main.py            FastAPI app + endpoints + error handlers
  bootstrap.py       startup: load -> validate -> build -> register
  settings.py        Pydantic Settings (env / .env)
  logging_config.py  structlog + stdlib logging setup
  errors.py          ConfigError, NodeExecutionError
  config/            Pydantic schema, YAML loader, semantic validator
  graph/             builder, registry, runner, nodes, conditions, state
  llm/factory.py     provider-agnostic chat-model factory (OpenAI in v1)
  modules/           user-defined module-node functions
  prompts/           Jinja2 prompt templates
configs/             flow YAML files
tests/               unit + API + end-to-end tests
  configs/           YAML flow used by the e2e tests
```

## Notes & v1 scope

- Bad configs fail **startup**, not requests.
- `when` is handled inside node functions; only `end_if` alters graph topology
  (via a hidden router node), keeping the builder simple.
- Not in v1: arbitrary conditional edges, loops, persistence, streaming,
  per-node retries, runtime-uploaded modules.
