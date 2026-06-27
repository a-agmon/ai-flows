"""Node factories: turn a node config into an async LangGraph node function.

Every node function has the signature ``async (state: dict) -> dict`` and returns
the partial state update to merge back. A shared wrapper handles the concerns
common to all node types: the ``when`` guard, timing/structured logging, and
converting failures into :class:`NodeExecutionError` tagged with the node id.
"""

from __future__ import annotations

import asyncio
import functools
import importlib
import inspect
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import jinja2
import structlog

from app.config.models import (
    LLMNodeConfig,
    ModuleNodeConfig,
    NodeConfig,
    SourceConfig,
)
from app.errors import ConfigError, NodeExecutionError
from app.graph.conditions import evaluate_condition
from app.graph.state import STATE_KEY
from app.llm.factory import create_llm
from app.settings import settings

logger = structlog.get_logger("ai_flows.node")

NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

# StrictUndefined turns a missing prompt variable into a clear error instead of
# silently rendering an empty string.
_jinja_env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)


def render_template(template_text: str, state: dict[str, Any]) -> str:
    """Render a Jinja2 template string against the current state."""
    return _jinja_env.from_string(template_text).render(**state)


def message_text(message: Any) -> str:
    """Extract plain text from a chat-model response.

    Handles both the Chat Completions shape (``content`` is a string) and the
    Responses API / multimodal shape (``content`` is a list of content blocks),
    so swapping providers or enabling the Responses API doesn't change behaviour.
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content)


def resolve_prompt_path(node_id: str, prompt_file: str) -> Path:
    """Resolve ``prompt_file`` against the prompts dir, rejecting path traversal.

    Shared by the loader (to read the template) and the validator (to check it
    exists) so the traversal guard lives in exactly one place.
    """
    prompts_dir = settings.prompts_dir.resolve()
    path = (settings.prompts_dir / prompt_file).resolve()
    if prompts_dir not in path.parents:
        raise ConfigError(
            f"node '{node_id}': prompt_file '{prompt_file}' is outside the prompts directory"
        )
    return path


def _load_prompt_text(node: LLMNodeConfig) -> str:
    """Return the prompt template text for an LLM node (inline or from file)."""
    if node.prompt is not None:
        return node.prompt
    return resolve_prompt_path(node.id, node.prompt_file).read_text(encoding="utf-8")


def import_module_function(module: str, function: str) -> Callable[..., Any]:
    """Import ``function`` from ``<MODULES_PACKAGE>.<module>``."""
    qualified = f"{settings.modules_package}.{module}"
    try:
        mod = importlib.import_module(qualified)
    except ImportError as exc:
        raise ConfigError(f"cannot import module '{qualified}': {exc}") from exc
    fn = getattr(mod, function, None)
    if not callable(fn):
        raise ConfigError(f"'{qualified}' has no callable '{function}'")
    return fn


def _create_llm_node(node: LLMNodeConfig) -> NodeFn:
    # Build the client once at compile time; reuse it across requests.
    llm = create_llm(
        provider=node.provider,
        model=node.model,
        temperature=node.temperature,
        params=node.params,
    )
    prompt_text = _load_prompt_text(node)

    async def run(state: dict[str, Any]) -> dict[str, Any]:
        prompt = render_template(prompt_text, state)
        response = await llm.ainvoke(prompt)
        return {node.output_key: message_text(response)}

    return run


async def _call_module_fn(
    fn: Callable[..., Any], *, inputs: dict, state: dict, config: dict
) -> Any:
    """Call a module function, awaiting coroutines and off-loading sync work."""
    if inspect.iscoroutinefunction(fn):
        return await fn(inputs=inputs, state=state, config=config)
    return await asyncio.to_thread(fn, inputs=inputs, state=state, config=config)


def _create_module_node(node: ModuleNodeConfig) -> NodeFn:
    fn = import_module_function(node.module, node.function)

    async def run(state: dict[str, Any]) -> dict[str, Any]:
        try:
            mapped_inputs = {arg: state[key] for arg, key in node.inputs.items()}
        except KeyError as exc:
            raise NodeExecutionError(
                node.id, f"missing state key for input: {exc.args[0]}"
            ) from exc

        result = await _call_module_fn(
            fn, inputs=mapped_inputs, state=state, config=node.config
        )

        if node.merge_output:
            if not isinstance(result, dict):
                raise NodeExecutionError(
                    node.id, "merge_output requires the function to return a dict"
                )
            return result
        return {node.output_key: result}

    return run


async def run_source(
    source: SourceConfig, query: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Execute a flow-level data source and return the dict it injects into state.

    Runs once before the graph. Mirrors the node wrapper's observability (timing,
    bound contextvars, error normalization) so a source failure reads like any
    other runtime failure -- tagged with the synthetic node id ``__source__``.
    """
    fn = import_module_function(source.module, source.function)
    with structlog.contextvars.bound_contextvars(node_id="__source__", node_type="source"):
        start = time.perf_counter()
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(query=query, params=params, config=source.config)
            else:
                result = await asyncio.to_thread(
                    fn, query=query, params=params, config=source.config
                )
            if not isinstance(result, dict):
                raise NodeExecutionError(
                    "__source__", "source function must return a dict"
                )
        except NodeExecutionError as exc:
            _log_node(start, "failed", exc.message)
            raise
        except Exception as exc:  # noqa: BLE001 - normalize any source failure
            _log_node(start, "failed", repr(exc))
            raise NodeExecutionError("__source__", str(exc) or repr(exc)) from exc

        _log_node(start, "ok", None)
        return result


def _build_inner(node: NodeConfig) -> NodeFn:
    if isinstance(node, LLMNodeConfig):
        return _create_llm_node(node)
    if isinstance(node, ModuleNodeConfig):
        return _create_module_node(node)
    raise ConfigError(f"unsupported node type: {node.type}")  # pragma: no cover


def create_node_fn(node: NodeConfig, *, stage_skip_key: str | None = None) -> NodeFn:
    """Build the wrapped, runnable node function for a node config.

    ``stage_skip_key`` is the state key the stage's entry node sets when the
    stage's ``when`` guard is false; if present and truthy the node is skipped,
    just as it is when the node's own ``when`` guard is false.
    """
    inner = _build_inner(node)

    @functools.wraps(inner)
    async def wrapped(graph_state: dict[str, Any]) -> dict[str, Any]:
        # Unwrap the shared flow state; node authors only ever see this dict.
        state = graph_state[STATE_KEY]

        # Bind node identity into structlog's contextvars for the duration of the
        # node. Combined with the run context bound by the runner, every log line
        # emitted here -- including from inside a module function -- carries
        # run_id, agent_id, node_id and node_type without passing them around.
        with structlog.contextvars.bound_contextvars(
            node_id=node.id, node_type=node.type
        ):
            stage_skipped = bool(stage_skip_key and state.get(stage_skip_key))
            node_skipped = node.when is not None and not evaluate_condition(
                node.when, state
            )
            if stage_skipped or node_skipped:
                logger.info("node skipped", status="skipped")
                return {STATE_KEY: {}}

            start = time.perf_counter()
            try:
                update = await inner(state)
            except NodeExecutionError as exc:
                _log_node(start, "failed", exc.message)
                raise
            except Exception as exc:  # noqa: BLE001 - normalize any node failure
                _log_node(start, "failed", repr(exc))
                raise NodeExecutionError(node.id, str(exc) or repr(exc)) from exc

            _log_node(start, "ok", None)
            return {STATE_KEY: update}

    return wrapped


def _log_node(start: float, status: str, error: str | None) -> None:
    # node_id / node_type / run_id / agent_id come from bound contextvars.
    logger.info(
        "node executed",
        duration_ms=round((time.perf_counter() - start) * 1000, 2),
        status=status,
        error=error,
    )
