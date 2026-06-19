# LLM providers, models & the Responses API

LLM nodes are decoupled from any single vendor by a small factory,
[`app/llm/factory.py`](../app/llm/factory.py). It builds a LangChain
`BaseChatModel` from four fields on the node:

```yaml
- id: classify
  type: llm
  provider: openai          # which client to build (default: openai)
  model: gpt-4.1-mini       # provider model id
  temperature: 0.2
  params:                   # forwarded verbatim to the client constructor
    max_tokens: 800
  prompt_file: classify.md
  output_key: classification
```

At runtime a node does `response = await llm.ainvoke(prompt)` and the text is
extracted with `message_text()`, which handles both a plain string `content`
and the Responses API / multimodal **list-of-blocks** shape. So switching
provider or response format needs no node changes.

Each node builds its own client, so **different nodes in the same flow can use
different providers, models, or temperatures** freely.

## `params` is the escape hatch

Anything in `params` is passed straight to the LangChain client constructor.
That means most provider-specific knobs need no code here — for example
`max_tokens`, `timeout`, `max_retries`, `base_url`, `top_p`, `seed`.

## OpenAI (default)

Set `OPENAI_API_KEY`. `provider: openai` uses `langchain_openai.ChatOpenAI`.

### Use the OpenAI Responses API

Pass `use_responses_api: true` through `params` — no code change:

```yaml
- id: answer
  type: llm
  provider: openai
  model: gpt-4.1-mini
  params:
    use_responses_api: true
  prompt_file: answer.md
  output_key: answer
```

`message_text()` already normalises the Responses API's structured content back
to a plain string for `output_key`.

## Anthropic

```bash
uv pip install langchain-anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

```yaml
- id: draft
  type: llm
  provider: anthropic
  model: claude-3-5-sonnet-latest
  temperature: 0.3
  params:
    max_tokens: 1024        # Anthropic requires max_tokens
  prompt_file: draft.md
  output_key: draft
```

## OpenAI-compatible / local endpoints (Ollama, vLLM, LiteLLM, …)

Many local servers speak the OpenAI API. Point `ChatOpenAI` at them with
`base_url` via `params` (use `provider: openai`):

```yaml
- id: summarize
  type: llm
  provider: openai
  model: llama3.1
  params:
    base_url: http://localhost:11434/v1   # e.g. Ollama
    api_key: not-needed                    # some servers ignore it
  prompt_file: summarize.md
  output_key: summary
```

## Adding a new provider

Add one branch to `create_llm()` in [`app/llm/factory.py`](../app/llm/factory.py).
Keep the import inside the branch so the dependency stays optional:

```python
if provider == "google":
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise ConfigError(
            "provider 'google' requires 'langchain-google-genai'"
        ) from exc
    return ChatGoogleGenerativeAI(model=model, temperature=temperature, **params)
```

Then flows can use `provider: google` immediately — no other code changes.

## Notes

- API keys come from the environment (each LangChain integration reads its own
  standard variable, e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). They are not
  part of the flow YAML.
- Clients are built once at startup (graph compile time) and reused per request.
- v1 invokes with a single rendered prompt string; multi-message chat templates,
  tool calling, and streaming are out of scope.
