"""Executes a registered flow for one request and shapes the response."""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from app.config.models import FlowConfig
from app.graph.registry import RegisteredFlow
from app.graph.state import unwrap, wrap

logger = structlog.get_logger("ai_flows.run")

# State keys reserved for flow-control bookkeeping. Hidden from normal output.
_INTERNAL_PREFIX = "_"


class InputValidationError(Exception):
    """Raised when a request payload does not satisfy the flow's inputs."""


def build_initial_state(
    config: FlowConfig, payload: dict[str, Any], run_id: str
) -> dict[str, Any]:
    """Validate the payload and merge it with declared defaults.

    Raises:
        InputValidationError: if a required input is missing.
    """
    if not isinstance(payload, dict):
        raise InputValidationError("request body must be a JSON object")

    missing = [
        name
        for name, spec in config.inputs.items()
        if spec.required and name not in payload
    ]
    if missing:
        raise InputValidationError(f"missing required input(s): {', '.join(missing)}")

    state: dict[str, Any] = {}
    for name, spec in config.inputs.items():
        if spec.default is not None:
            state[name] = spec.default
    # Caller-provided values win over defaults; unknown keys pass through so
    # flows can accept ad-hoc context without re-declaring every field.
    state.update(payload)

    state["_run_id"] = run_id
    state["_agent_id"] = config.id
    state["_flow_status"] = "running"
    state["_completion_reason"] = None
    return state


def _extract_outputs(config: FlowConfig, state: dict[str, Any]) -> dict[str, Any]:
    """Return the configured outputs that are present in final state.

    Missing outputs are simply omitted -- important for early termination, where
    later stages never ran and their outputs do not exist.
    """
    return {key: state[key] for key in config.outputs if key in state}


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in state.items() if not k.startswith(_INTERNAL_PREFIX)}


async def run_flow(
    entry: RegisteredFlow, payload: dict[str, Any], include_state: bool = False
) -> dict[str, Any]:
    """Run a flow end to end and build the API response body."""
    config = entry.config
    run_id = str(uuid.uuid4())
    initial_state = build_initial_state(config, payload, run_id)

    start = time.perf_counter()
    logger.info(
        "run started",
        run_id=run_id, agent_id=config.id, version=entry.version,
    )

    final_state: dict[str, Any] = unwrap(await entry.graph.ainvoke(wrap(initial_state)))

    ended = final_state.get("_flow_status") == "ended"
    response: dict[str, Any] = {
        "agent_id": config.id,
        "run_id": run_id,
        "status": "ended" if ended else "completed",
        "completion_reason": (
            final_state.get("_completion_reason") if ended else "end_reached"
        ),
        "output": _extract_outputs(config, final_state),
    }
    if include_state:
        response["state"] = _public_state(final_state)

    logger.info(
        "run finished",
        run_id=run_id,
        agent_id=config.id,
        status=response["status"],
        duration_ms=round((time.perf_counter() - start) * 1000, 2),
    )
    return response
