"""Creates chat-model clients from YAML model parameters.

A thin seam so the YAML schema is not coupled to a single provider. Each provider
is a small branch that constructs a LangChain ``BaseChatModel``; ``params`` from
the YAML are forwarded verbatim to the client constructor, so provider-specific
options (including the OpenAI **Responses API** via ``use_responses_api: true``)
need no code changes here.

Adding a provider: add a branch below that imports its LangChain integration
lazily and returns the chat model. Keep the import inside the branch so the
dependency is optional. See ``docs/LLM_PROVIDERS.md``.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.errors import ConfigError


def create_llm(
    *,
    provider: str,
    model: str,
    temperature: float,
    params: dict[str, Any] | None = None,
) -> BaseChatModel:
    """Build a LangChain chat model for the given parameters.

    Args:
        provider: ``openai`` (default) or ``anthropic``.
        model: provider model id, e.g. ``gpt-4.1-mini`` or ``claude-...``.
        temperature: sampling temperature.
        params: extra keyword args forwarded to the client constructor (e.g.
            ``max_tokens``, ``use_responses_api``, ``base_url``, ``timeout``).

    Raises:
        ConfigError: if the provider is unknown or its package is not installed.
    """
    params = params or {}

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ConfigError(
                "provider 'openai' requires the 'langchain-openai' package"
            ) from exc
        return ChatOpenAI(model=model, temperature=temperature, **params)

    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ConfigError(
                "provider 'anthropic' requires the 'langchain-anthropic' package "
                "(uv pip install langchain-anthropic) and ANTHROPIC_API_KEY"
            ) from exc
        return ChatAnthropic(model=model, temperature=temperature, **params)

    raise ConfigError(
        f"unsupported llm provider: '{provider}' (supported: openai, anthropic)"
    )
