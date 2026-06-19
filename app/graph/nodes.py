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
from typing import Any, Awaitable, Callable

import jinja2
import structlog

from app.config.models import LLMNodeConfig, ModuleNodeConfig, NodeConfig
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


def _load_prompt_text(node: LLMNodeConfig) -> str:
    """Return the prompt template text for an LLM node (inline or from file)."""
    if node.prompt is not None:
        return node.prompt
    path = (settings.prompts_dir / node.prompt_file).resolve()
    # Guard against path traversal via a crafted ``prompt_file``.
    if settings.prompts_dir.resolve() not in path.parents:
        raise ConfigError(
            f"node '{node.id}': prompt_file '{node.prompt_file}' escapes the prompts directory"
        )
    return path.read_text(encoding="utf-8")


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
        content = response.content
        if not isinstance(content, str):
            content = str(content)
        return {node.output_key: content}

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


def _build_inner(node: NodeConfig) -> NodeFn:
    if isinstance(node, LLMNodeConfig):
        return _create_llm_node(node)
    if isinstance(node, ModuleNodeConfig):
        return _create_module_node(node)
    raise ConfigError(f"unsupported node type: {node.type}")  # pragma: no cover


def create_node_fn(node: NodeConfig) -> NodeFn:
    """Build the wrapped, runnable node function for a node config."""
    inner = _build_inner(node)

    @functools.wraps(inner)
    async def wrapped(graph_state: dict[str, Any]) -> dict[str, Any]:
        # Unwrap the shared flow state; node authors only ever see this dict.
        state = graph_state[STATE_KEY]
        run_id = state.get("_run_id")
        agent_id = state.get("_agent_id")

        if node.when is not None and not evaluate_condition(node.when, state):
            logger.info(
                "node skipped",
                run_id=run_id, agent_id=agent_id,
                node_id=node.id, node_type=node.type, status="skipped",
            )
            return {STATE_KEY: {}}

        start = time.perf_counter()
        try:
            update = await inner(state)
        except NodeExecutionError as exc:
            _log_node(run_id, agent_id, node, start, "failed", exc.message)
            raise
        except Exception as exc:  # noqa: BLE001 - normalize any node failure
            _log_node(run_id, agent_id, node, start, "failed", repr(exc))
            raise NodeExecutionError(node.id, str(exc) or repr(exc)) from exc

        _log_node(run_id, agent_id, node, start, "ok", None)
        return {STATE_KEY: update}

    return wrapped


def _log_node(run_id, agent_id, node: NodeConfig, start: float,
              status: str, error: str | None) -> None:
    logger.info(
        "node executed",
        run_id=run_id,
        agent_id=agent_id,
        node_id=node.id,
        node_type=node.type,
        duration_ms=round((time.perf_counter() - start) * 1000, 2),
        status=status,
        error=error,
    )
