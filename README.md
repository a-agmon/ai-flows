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
A flow can load its own data from a `query` via a `source` (else data comes in as request params).
```

There are no edges to wire by hand: data moves only through shared state.

## Quick start

> New here? **[QUICKSTART.md](QUICKSTART.md)** is a step-by-step walkthrough:
> install, run, call the built-in flows, and author your own. The block below is
> the short version.

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

Example (the `ticket_triage` flow runs without an API key):

```bash
curl -X POST localhost:8000/agents/ticket_triage/invoke \
  -H 'content-type: application/json' \
  -d '{"ticket_id": "T-100"}'
```

Response:

```json
{
  "agent_id": "ticket_triage",
  "run_id": "…",
  "status": "completed",
  "completion_reason": "end_reached",
  "output": {
    "subject": "Refund for a delayed order",
    "priority": "high",
    "triage": { "priority": "high", "queue": "urgent" }
  }
}
```

The caller sent only a ticket id — `subject` and `priority` were pulled in by the
flow's data **source** (see [Data sources](#data-sources-query--source) below).

If a stage's `end_if` fires, `status` is `"ended"`, `completion_reason` is the
configured reason, and only the outputs produced so far are returned.

## Authoring a flow

Drop a `*.yaml` file in `configs/`.
[`configs/support_reply.yaml`](configs/support_reply.yaml) is the fullest
example — sequential + parallel stages, LLM + module nodes, `when`, `end_if`,
folder-nested prompts, and a logging module. **[EXAMPLE.md](EXAMPLE.md) builds
that flow step by step** and is the best way to learn how the pieces fit. Schema
summary:

```yaml
id: my_flow                 # unique flow id
route: /agents/my-flow      # unique route
inputs:
  some_field: { type: string, required: true }
  tone:       { type: string, required: false, default: professional }
# Optional: load data into the flow instead of requiring it all as params.
# query: "..."              # see "Data sources" below
# source: { module: datasource, function: fetch_ticket }
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
`prompt:` (inline) or `prompt_file:` (a file under `app/prompts/`). Prompt files
may be organised in **sub-folders**, referenced with a relative path
(`prompt_file: support/draft.md`); paths that escape the prompts directory are
rejected at startup. Pick the provider/model per node and use the OpenAI
Responses API or other vendors — see [docs/LLM_PROVIDERS.md](docs/LLM_PROVIDERS.md).

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

Modules can log with structlog — just grab a logger:

```python
import structlog
log = structlog.get_logger("ai_flows.module.support")

async def unpack_triage(inputs, state, config):
    log.info("triage parsed", category="billing")   # see app/modules/support.py
    ...
```

The runtime binds `run_id`, `agent_id`, `node_id` and `node_type` into
structlog's contextvars around each node, so module log lines are automatically
correlated with the run without passing any of that in.

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

### Data sources (`query` + `source`)

Two ways to get data into a flow:

1. **As request params.** The caller sends everything in the request body; the
   values land in state under their input names. This is the default and is all
   an external system that already holds the data needs.
2. **Via a data source.** The flow declares a `query` and a flow-level `source`
   module that runs the query and **injects the result into state before the
   graph starts**. The caller then sends only a key (an id, a search term) and
   the flow fetches the rest itself — handy for no-code flows and for keeping
   large records out of the request.

```yaml
inputs:
  ticket_id: { type: string, required: true }

query: >                       # a Jinja2 template, rendered over the params
  SELECT subject, body, priority FROM tickets WHERE id = '{{ ticket_id }}'

source:
  module: datasource           # -> app/modules/datasource.py
  function: fetch_ticket
  config: { }                  # optional static config (dsn, index name, ...)
  outputs: [subject, body, priority]   # optional: keys it injects (see below)
  # when: { field: subject, exists: false }   # optional: skip the fetch (below)
```

A source function has its own contract (it gets the rendered `query`, not an
`inputs` map) and returns a dict that is merged into state:

```python
async def fetch_ticket(query: str, params: dict, config: dict) -> dict:
    ...
    return {"subject": "...", "priority": "high"}   # merged into state
```

**The two modes compose.** State is layered so explicit caller params always win:

```
defaults  <  source-injected data  <  request payload
```

So a flow that *has* a source can still accept the same data directly as a param
— the caller's value overrides what the source fetched. By default **the source
runs on every request** and the payload simply wins on the merge.

**Bypassing the fetch entirely.** If you want a caller-supplied payload to *skip*
the source — no fetch latency, no dependency on the backing store — give the
source a `when` guard (the same condition syntax as nodes/stages), evaluated
against the request params. When it is false the source is not called at all:

```yaml
source:
  module: datasource
  function: fetch_ticket
  when: { field: subject, exists: false }   # only fetch when subject wasn't supplied
```

**Declaring `outputs`.** A source's keys are dynamic, so by default a flow with a
source is exempt from the startup "outputs are producible" check. List the keys
the source injects under `source.outputs` to keep that check on (node-produced
outputs and typos are still caught) and to let no-code UIs introspect the source.

A `query` without a `source` to run it is a config error; a `source` may omit
`query` if the function works from `params`/`config` alone. See
[`configs/ticket_triage.yaml`](configs/ticket_triage.yaml).

## State & data flow

Everything in a flow communicates through one shared `state` dict. There are no
hand-wired edges; a later node sees an earlier node's output simply because that
output was written into state under some key. The read/write rules below are most
of what there is to understand.

**Reading state — every node can read all of it.** There is no access
declaration that limits what a node may read.

- **LLM nodes** have no `inputs` field. The prompt is a Jinja2 template rendered
  against the *whole* state, so `{{ some_key }}` pulls `some_key` directly. A
  missing variable raises an error (strict Jinja) rather than rendering blank.
- **Module nodes** receive the whole state as their `state` argument and may read
  anything from it. The `inputs:` map is *ergonomic, not an access boundary*: it
  (a) lets one generic function be reused against different state keys, and
  (b) fails loudly if a declared key is missing. A function may also reach into
  `state` directly for ad-hoc reads.

So a node can use a previous node's output **without declaring it** — LLM nodes
always do (whole-state templating); module nodes can via the `state` arg.
Declaring `inputs` just buys clarity and a nicer "missing key" error.

**Writing state — the return value is the only sanctioned way to change it.**

- Return a single value → written under the node's `output_key`.
- Return a dict with `merge_output: true` → each dict key becomes a state key.
  This is the only way for one node to produce *several* keys. (An `llm` node
  always produces exactly one string, so multi-key producers must be `module`
  nodes — or have the LLM emit JSON and a module unpack it, as
  `unpack_classification` in [`transforms.py`](app/modules/transforms.py) does.)
- **Do not mutate the `state` dict in place.** It is shared — siblings in a
  parallel stage see the same object — and in-place writes bypass the reducer, so
  they do not reliably propagate. Treat `state` as read-only context and express
  every change through the return value.

**The module function contract is fixed.** A module function is *always* called
with three dict keyword arguments and returns a single value or a dict:

```python
async def my_fn(inputs: dict, state: dict, config: dict) -> str | dict:
    file_url = inputs["file_url"]      # mapped via the YAML `inputs:` map
    top_k    = config.get("top_k", 3)  # from the node's static `config:` block
    ...
```

`inputs` is the mapped `{arg: value}` dict, `state` is the full state, `config`
is the node's static `config:` block (defaults to `{}`). The engine never unpacks
scalars for you — you pull individual values out of these dicts yourself. Sync
functions are supported and run in a thread pool.

**Stage boundaries are barriers.** Every node in a stage completes before any
node in the next stage starts, and the next stage sees everything prior stages
wrote. This join is what you rely on to pass data forward.

**Overriding keys is last-writer-wins.** Across stages this is deterministic
(stages are ordered), so a later stage can overwrite an earlier key. **Within a
single `parallel: true` stage, nodes must write distinct keys** — sibling updates
merge in an unspecified order, so two nodes writing the same key is a race. For
the same reason, don't read a key inside a parallel stage that a sibling in that
*same* stage is writing; it's only reliably present in the next stage.

**Internal keys.** Keys beginning with `_` (e.g. `_run_id`, `_flow_status`) are
reserved for flow-control bookkeeping and are stripped from API output. Avoid the
`_` prefix for your own keys.

## Patterns

**Fan out a task to several nodes.** Put the workers in one `parallel: true`
stage; its hidden entry fans out to all of them and the next stage joins on all:

```yaml
- id: draft
  parallel: true
  nodes:
    - { id: write_intro,   type: llm, model: gpt-4.1-mini, prompt_file: intro.md,   output_key: intro_text }
    - { id: write_body,    type: llm, model: gpt-4.1-mini, prompt_file: body.md,    output_key: body_text }
    - { id: write_closing, type: llm, model: gpt-4.1-mini, prompt_file: closing.md, output_key: closing_text }
```

**Pre-process before each fan-out branch.** There is no per-branch chain *inside*
a parallel stage, and `parallel: false` would serialize the work. Instead use two
parallel stages paired by key naming — the stage boundary guarantees each
worker's input exists before it runs:

```yaml
- id: preprocess          # phase 1: prep every item concurrently
  parallel: true
  nodes:
    - { id: ocr_a, type: module, module: ocr, function: extract_text,
        output_key: a_text, inputs: { file_url: a_url } }
    - { id: ocr_b, type: module, module: ocr, function: extract_text,
        output_key: b_text, inputs: { file_url: b_url } }

- id: summarize           # phase 2: the real fan-out; each reads its own input
  parallel: true
  nodes:
    - { id: sum_a, type: llm, model: gpt-4.1-mini, output_key: a_summary, prompt: "Summarize:\n{{ a_text }}" }
    - { id: sum_b, type: llm, model: gpt-4.1-mini, output_key: b_summary, prompt: "Summarize:\n{{ b_text }}" }
```

The `ocr_a → sum_a` link is not an edge — it is just `output_key: a_text`
matching `{{ a_text }}`.

**One node produces N fields, then fan out on them.** Use a `module` node with
`merge_output: true` to emit several keys, then a parallel stage whose nodes each
read one:

```yaml
- id: split
  nodes:
    - { id: make_parts, type: module, module: transforms,
        function: make_three_parts, merge_output: true }   # -> {part_a, part_b, part_c}

- id: work
  parallel: true
  nodes:
    - { id: work_a, type: llm, model: gpt-4.1-mini, output_key: a, prompt: "...{{ part_a }}..." }
    - { id: work_b, type: llm, model: gpt-4.1-mini, output_key: b, prompt: "...{{ part_b }}..." }
    - { id: work_c, type: llm, model: gpt-4.1-mini, output_key: c, prompt: "...{{ part_c }}..." }
```

> **Fan-out is static.** You declare a fixed set of nodes in YAML; there is no
> dynamic "map over a list of unknown length" (LangGraph's `Send`). A
> variable-width fan-out would be a framework change, not a config change.

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
  llm/factory.py     provider-agnostic chat-model factory (OpenAI, Anthropic)
  modules/           user-defined module-node + data-source functions (support.py, datasource.py)
  prompts/           Jinja2 prompt templates (may be nested, e.g. support/)
configs/             flow YAML files (letter_generation, ocr_summary, support_reply, ticket_triage)
docs/                LLM_PROVIDERS.md and other guides
tests/               unit + API + end-to-end tests
  configs/           YAML flow used by the e2e tests
```

## Notes & v1 scope

- Bad configs fail **startup**, not requests (duplicate ids/routes, missing
  prompt files or modules, prompt paths escaping the prompts dir, etc.).
- `when` never alters topology: a node-level `when` is checked inside the node
  function, a stage-level `when` in the stage's entry node. Only `end_if` adds a
  hidden router node, keeping the builder simple.
- Using `merge_output: true` on any node disables the startup check that every
  declared `output` is producible — those keys are only known at runtime. A
  flow-level `source` does the same *unless* it declares `source.outputs`, which
  re-enables the check using the declared keys.
- A `source` runs once before the graph (unless its `when` guard is false). It is
  validated at startup (module and function must import) and its failures surface
  like a node failure, tagged with the synthetic id `__source__`.
- `route` is metadata (it must be unique and is returned by `/schema`), but flows
  are always invoked at `/agents/{id}/invoke` — the `route` value does not change
  the HTTP path today.
- Input `type` is declared for documentation and tooling; the runtime enforces
  `required` but does not yet coerce or validate values against `type`.
- Not in v1: arbitrary conditional edges, loops, persistence, streaming,
  per-node retries, runtime-uploaded modules.
