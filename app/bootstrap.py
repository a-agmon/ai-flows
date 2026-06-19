"""Startup wiring: load all flow configs and compile them into the registry."""

from __future__ import annotations

from pathlib import Path

import structlog

from app.config.loader import load_flow_dir
from app.config.validator import validate_flow
from app.graph.builder import build_graph
from app.graph.registry import GraphRegistry
from app.settings import settings

logger = structlog.get_logger("ai_flows.bootstrap")


def build_registry(configs_dir: Path = settings.configs_dir) -> GraphRegistry:
    """Load, validate, compile and register every flow.

    Any invalid config raises ``ConfigError`` and aborts startup, by design --
    we never want to serve a half-broken set of flows.
    """
    registry = GraphRegistry()
    configs = load_flow_dir(configs_dir)
    logger.info("loading flow configs", count=len(configs), configs_dir=str(configs_dir))

    for config in configs:
        validate_flow(config)
        graph = build_graph(config)
        entry = registry.register(config, graph)
        logger.info(
            "registered flow",
            flow_id=config.id,
            route=config.route,
            version=entry.version,
        )
    return registry
