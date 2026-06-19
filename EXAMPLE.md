# Anatomy of an example flow

This is a step-by-step build of the shipped
[`configs/support_reply.yaml`](configs/support_reply.yaml) flow. We start from an
empty file and add one piece at a time, explaining *why* at each step. By the end
you'll understand how every part of a flow fits together and maps onto the graph.

The flow we're building: **take a customer support message, triage it, stop early
if a human is needed, otherwise gather context, draft a reply, optionally add a
disclaimer, and polish the result.**

> The one rule to keep in mind the whole way through:
> **nodes read the shared state, write the shared state, and the next stage sees
> everything written before it.**

---

## Step 1 — The skeleton: identity, inputs, outputs

Every flow starts with an id, a route, the inputs it accepts, and the outputs it
returns. Drop this in `configs/support_reply.yaml`:

```yaml
id: support_reply                 # unique id; used in the URL and logs
name: Support Reply Assistant
route: /agents/support-reply      # unique HTTP route

inputs:
  customer_message:
    type: string
    required: true                # missing -> 400 before the flow runs
  tone:
    type: string
    required: false
    default: empathetic           # default merged into state if caller omits it
  include_disclaimer:
    type: boolean
    required: false
    default: false

outputs:                          # keys returned to the caller (if present)
  - category
  - urgency
  - final_reply
  - rejection_reason

stages: []                        # we'll fill these in
```

The request body becomes the **initial state**. With the defaults above, a request
of `{"customer_message": "..."}` starts the flow with state:

```python
{"customer_message": "...", "tone": "empathetic", "include_disclaimer": False}
```

Outputs are just state keys we choose to surface at the end. If the flow ends
early and some aren't produced, they're simply omitted — not an error.

---

## Step 2 — First stage: classify with an LLM node

The first real work is to triage the message. That's an `llm` node: it renders a
Jinja2 prompt over the current state, calls the model, and writes the response to
its `output_key`.

```yaml
stages:
  - id: triage
    parallel: false
    nodes:
      - id: classify
        type: llm
        model: gpt-4.1-mini
        temperature: 0
        prompt_file: support/classify.md     # lives in app/prompts/support/
        output_key: classification
```

Two things worth noting:

- **The prompt is a separate file**, organised in a sub-folder
  (`app/prompts/support/classify.md`). Prompt files can be nested; the path is
  relative to the prompts directory.
- **The whole state is available to the template.** `app/prompts/support/classify.md`
  uses `{{ customer_message }}` and asks the model to return strict JSON:

  ```jinja
  You are triaging an inbound customer support message.

  Return ONLY JSON of this exact shape:
  { "category": ..., "urgency": ..., "can_handle": true|false, "rejection_reason": ... }

  Customer message:
  {{ customer_message }}
  ```

After this node, state has a new key `classification` holding the model's raw JSON
string.

---

## Step 3 — Turn the JSON into real fields: a module node (with logging)

`classification` is just a string. We want individual fields (`category`,
`urgency`, `can_handle`, ...) in state. A pure LLM can't reliably do that — this is
plain Python, so it's a `module` node. Add it to the **same stage**, after
`classify`:

```yaml
      - id: unpack_triage
        type: module
        module: support              # -> app/modules/support.py
        function: unpack_triage      # async def unpack_triage(inputs, state, config)
        merge_output: true           # return a dict; merge all keys into state
        inputs:
          classification: classification   # function arg <- state key
```

Because `triage` is `parallel: false`, its nodes run **in order**: `classify`
writes `classification`, *then* `unpack_triage` reads it. (In a `parallel: true`
stage they'd run concurrently and `unpack_triage` wouldn't see it.)

The function ([`app/modules/support.py`](app/modules/support.py)) parses the JSON
and returns a dict — and shows how a module logs:

```python
import structlog
log = structlog.get_logger("ai_flows.module.support")

async def unpack_triage(inputs: dict, state: dict, config: dict) -> dict:
    data = _extract_json(inputs["classification"])
    log.info("triage parsed", category=data["category"], can_handle=data["can_handle"])
    return {"category": ..., "urgency": ..., "can_handle": ..., "rejection_reason": ...}
```

You don't pass any run context to the logger. The runtime binds `run_id`,
`agent_id`, `node_id` and `node_type` into structlog's contextvars around the
node, so this log line is automatically correlated with the run.

`merge_output: true` means the returned dict's keys are merged straight into state,
so `category`, `urgency`, `can_handle` and `rejection_reason` are now first-class
state keys.

---

## Step 4 — Stop early when a human is needed: `end_if`

If the classifier decided the message can't be auto-handled, there's no point
drafting a reply. A stage can end the whole flow with `end_if` — a code-free
condition checked after the stage finishes:

```yaml
  - id: triage
    parallel: false
    nodes: [ ... classify, unpack_triage ... ]
    end_if:
      field: can_handle
      equals: false
      reason: needs_human         # surfaced as completion_reason
```

If `can_handle` is `false`, the flow stops here. The response is:

```json
{ "status": "ended", "completion_reason": "needs_human",
  "output": { "category": "...", "urgency": "...", "rejection_reason": "..." } }
```

`final_reply` was never produced, so it's just left out of `output`.

---

## Step 5 — Gather context in parallel

If we got past triage, we draft a reply — but first gather anything the draft
needs. This is a `parallel: true` stage: a fan-out point. (We have one node here,
but you'd add more retrieval/enrichment nodes and they'd all run concurrently.)

```yaml
  - id: gather
    parallel: true
    nodes:
      - id: retrieve_policy
        type: module
        module: retrieval
        function: search
        inputs:
          query: customer_message
        config:                    # static per-node settings -> the `config` arg
          top_k: 3
        output_key: policy_context
```

Here the module returns a **string**, so it's written under `output_key`
(`policy_context`) rather than merged. Note `config:` — static options that reach
the function's `config` argument, separate from the dynamic `inputs`.

---

## Step 6 — Draft the reply

A straightforward `llm` node whose prompt pulls together everything earlier stages
wrote — the message, the triage fields, and the retrieved context:

```yaml
  - id: draft
    parallel: false
    nodes:
      - id: draft_reply
        type: llm
        model: gpt-4.1-mini
        temperature: 0.3
        prompt_file: support/draft.md
        output_key: draft
```

`app/prompts/support/draft.md` references `{{ tone }}`, `{{ category }}`,
`{{ urgency }}`, `{{ policy_context }}` and `{{ customer_message }}` — all already
in state. This is the shared-state rule paying off: no wiring, the draft node just
reads what it needs.

---

## Step 7 — An optional step: `when`

The disclaimer should only be generated if the caller asked for it. A node (or a
whole stage) can carry a `when` guard; if it's false the node is skipped and
writes nothing.

```yaml
  - id: disclaimer
    parallel: false
    nodes:
      - id: add_disclaimer
        type: llm
        when:
          field: include_disclaimer
          equals: true
        model: gpt-4.1-mini
        prompt_file: support/disclaimer.md
        output_key: disclaimer
```

We give it its own stage (rather than putting it next to `polish`) on purpose: the
next stage must be able to *see* `disclaimer`, and a stage only sees what earlier
stages wrote. The polish prompt then uses it defensively (`{{ disclaimer |
default('') }}`), so it works whether or not the node ran.

---

## Step 8 — Polish and finish

The last stage produces the configured `final_reply` output:

```yaml
  - id: finalize
    parallel: false
    nodes:
      - id: polish_reply
        type: llm
        model: gpt-4.1-mini
        temperature: 0.2
        prompt_file: support/polish.md
        output_key: final_reply
```

That's the complete flow. The full file is
[`configs/support_reply.yaml`](configs/support_reply.yaml).

---

## How state flows through it

```
request {customer_message, tone, include_disclaimer}
   │
triage (sequential)
   ├─ classify       → classification
   └─ unpack_triage  → category, urgency, can_handle, rejection_reason
        └─ end_if can_handle == false → STOP (status "ended")
   │
gather (parallel)
   └─ retrieve_policy → policy_context
   │
draft
   └─ draft_reply    → draft
   │
disclaimer (optional)
   └─ add_disclaimer  → disclaimer        (only if include_disclaimer)
   │
finalize
   └─ polish_reply   → final_reply
   │
response { output: chosen keys present in final state }
```

## How it compiles to a graph

You write stages; the builder ([`app/graph/builder.py`](app/graph/builder.py))
turns each stage into LangGraph nodes:

- Each stage gets a hidden **entry node** that evaluates a stage-level `when` and
  leads into the stage's nodes.
- `parallel: true` fans the entry out to all nodes (they run together); the next
  stage joins on all of them. `parallel: false` chains them in order.
- A stage with `end_if` gets a hidden **router node** that either routes to `END`
  or on to the next stage.

So `support_reply` becomes roughly:

```
START → entry(triage) → classify → unpack_triage → router(triage) ─┬─ END (needs_human)
                                                                    └─ entry(gather) → retrieve_policy
        → entry(draft) → draft_reply → entry(disclaimer) → add_disclaimer
        → entry(finalize) → polish_reply → END
```

You never write any of that — it's derived from the stage list.

## Run it

The LLM nodes need `OPENAI_API_KEY` (see
[docs/LLM_PROVIDERS.md](docs/LLM_PROVIDERS.md) to use other providers). Then:

```bash
curl -X POST localhost:8000/agents/support_reply/invoke \
  -H 'content-type: application/json' \
  -d '{"customer_message": "I was double charged on my last invoice!", "tone": "empathetic"}'
```

```json
{
  "agent_id": "support_reply",
  "run_id": "…",
  "status": "completed",
  "completion_reason": "end_reached",
  "output": { "category": "billing", "urgency": "high", "final_reply": "…", "rejection_reason": null }
}
```

Add `?include_state=true` to see every key the flow wrote.

## Where each file lives

| Piece | File |
| --- | --- |
| The flow definition | `configs/support_reply.yaml` |
| Prompts (nested folder) | `app/prompts/support/{classify,draft,disclaimer,polish}.md` |
| The logging module | `app/modules/support.py` |
| Retrieval module | `app/modules/retrieval.py` |
| End-to-end tests (LLM mocked) | `tests/test_support_flow.py` |

## Next steps

- Conditions reference and operators: [README → Conditions](README.md#conditions)
- Other models / the Responses API: [docs/LLM_PROVIDERS.md](docs/LLM_PROVIDERS.md)
- Build your own from scratch: [QUICKSTART.md](QUICKSTART.md)
