"""Cross-cutting semantic validation, beyond what the Pydantic schema enforces.

Pydantic already guarantees per-node shape (e.g. an LLM node has a prompt and an
output_key). This module checks things that need the whole flow in view, and
that referenced resources -- prompt files, modules, functions -- actually exist.
Anything invalid raises :class:`ConfigError` and fails startup.
"""

from __future__ import annotations

from typing import NoReturn

from app.config.models import (
    FlowConfig,
    LLMNodeConfig,
    ModuleNodeConfig,
)
from app.errors import ConfigError
from app.graph.nodes import import_module_function, resolve_prompt_path


def validate_flow(config: FlowConfig) -> None:
    """Validate a single flow. Raises ConfigError on the first problem."""
    _check_unique_stage_ids(config)
    _check_unique_node_ids(config)
    _check_resources_exist(config)
    _check_source_exists(config)
    _check_outputs_are_producible(config)


def _fail(config: FlowConfig, message: str) -> NoReturn:
    raise ConfigError(f"flow '{config.id}': {message}")


def _check_unique_stage_ids(config: FlowConfig) -> None:
    # Stage ids name the hidden entry/router nodes in the graph, so duplicates
    # would otherwise surface as a cryptic "node already exists" error at build.
    seen: set[str] = set()
    for stage in config.stages:
        if stage.id in seen:
            _fail(config, f"duplicate stage id '{stage.id}'")
        seen.add(stage.id)


def _check_unique_node_ids(config: FlowConfig) -> None:
    seen: set[str] = set()
    for _, node in config.iter_nodes():
        if node.id in seen:
            _fail(config, f"duplicate node id '{node.id}'")
        seen.add(node.id)


def _check_resources_exist(config: FlowConfig) -> None:
    # The shared helpers raise bare ConfigErrors (no flow context); re-raise them
    # through ``_fail`` so every validation error consistently names the flow.
    for _, node in config.iter_nodes():
        if isinstance(node, LLMNodeConfig) and node.prompt_file is not None:
            try:
                # Resolves and rejects path traversal outside the prompts dir.
                path = resolve_prompt_path(node.id, node.prompt_file)
            except ConfigError as exc:
                _fail(config, str(exc))
            if not path.is_file():
                _fail(
                    config,
                    f"node '{node.id}' references prompt_file "
                    f"'{node.prompt_file}' but {path} does not exist",
                )
        elif isinstance(node, ModuleNodeConfig):
            try:
                import_module_function(node.module, node.function)
            except ConfigError as exc:
                _fail(config, str(exc))


def _check_source_exists(config: FlowConfig) -> None:
    # Resolve the source module/function the same way module nodes are checked,
    # so a typo in a source fails at startup rather than on the first request.
    if config.source is None:
        return
    try:
        import_module_function(config.source.module, config.source.function)
    except ConfigError as exc:
        _fail(config, str(exc))


def _check_outputs_are_producible(config: FlowConfig) -> None:
    """Every declared output should come from a node or a request input.

    ``merge_output`` nodes and a flow-level ``source`` both produce keys that
    cannot be known statically, so if either is present we skip this check rather
    than raise false positives.
    """
    if config.source is not None or any(
        node.merge_output for _, node in config.iter_nodes()
    ):
        return

    produced = {node.output_key for _, node in config.iter_nodes() if node.output_key}
    available = produced | set(config.inputs)
    for output in config.outputs:
        if output not in available:
            _fail(
                config,
                f"declared output '{output}' is not produced by any node "
                "and is not a request input",
            )
