"""In-memory registry of compiled flows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.config.models import FlowConfig
from app.errors import ConfigError


@dataclass(frozen=True)
class RegisteredFlow:
    """A flow config paired with its compiled, runnable graph."""

    config: FlowConfig
    graph: Any  # langgraph compiled graph
    version: str  # short hash of the config, for observability


class GraphRegistry:
    """Holds compiled flows, indexed by both flow id and route."""

    def __init__(self) -> None:
        self._by_id: dict[str, RegisteredFlow] = {}
        self._route_to_id: dict[str, str] = {}

    def register(self, config: FlowConfig, graph: Any) -> RegisteredFlow:
        if config.id in self._by_id:
            raise ConfigError(f"duplicate flow id: '{config.id}'")
        if config.route in self._route_to_id:
            raise ConfigError(
                f"duplicate route '{config.route}' "
                f"(already used by flow '{self._route_to_id[config.route]}')"
            )
        version = _config_hash(config)
        entry = RegisteredFlow(config=config, graph=graph, version=version)
        self._by_id[config.id] = entry
        self._route_to_id[config.route] = config.id
        return entry

    def get(self, agent_id: str) -> RegisteredFlow | None:
        return self._by_id.get(agent_id)

    def list(self) -> list[RegisteredFlow]:
        return list(self._by_id.values())


def _config_hash(config: FlowConfig) -> str:
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:12]
