"""Creates chat-model clients from YAML model parameters.

A thin seam so the YAML schema is not coupled to a single provider. v1 supports
OpenAI only; additional providers can be added as new branches without touching
the flow configs.
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

    Raises:
        ConfigError: if the provider is unknown or its package is missing.
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

    raise ConfigError(f"unsupported llm provider: '{provider}'")
