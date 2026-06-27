"""FastAPI application exposing YAML-defined LangGraph flows over HTTP."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.bootstrap import build_registry
from app.errors import ConfigError, NodeExecutionError
from app.graph.registry import GraphRegistry, RegisteredFlow
from app.graph.runner import InputValidationError, run_flow
from app.logging_config import configure_logging

logger = structlog.get_logger("ai_flows.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Configure logging first so startup (and any config errors) are structured.
    configure_logging()
    # Compiling all flows at startup means a bad config fails fast and loudly
    # rather than at first request.
    app.state.registry = build_registry()
    yield


app = FastAPI(title="AI Flows", version="0.1.0", lifespan=lifespan)


def _registry(request: Request) -> GraphRegistry:
    return request.app.state.registry


def _require_flow(request: Request, agent_id: str) -> RegisteredFlow:
    entry = _registry(request).get(agent_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown agent '{agent_id}'")
    return entry


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/agents")
async def list_agents(request: Request) -> dict[str, Any]:
    agents = [
        {
            "id": e.config.id,
            "name": e.config.name,
            "route": e.config.route,
            "description": e.config.description,
            "version": e.version,
        }
        for e in _registry(request).list()
    ]
    return {"agents": agents}


@app.get("/agents/{agent_id}/schema")
async def agent_schema(request: Request, agent_id: str) -> dict[str, Any]:
    entry = _require_flow(request, agent_id)
    config = entry.config
    return {
        "id": config.id,
        "name": config.name,
        "route": config.route,
        "description": config.description,
        "version": entry.version,
        "inputs": {
            name: spec.model_dump() for name, spec in config.inputs.items()
        },
        "query": config.query,
        "source": config.source.model_dump() if config.source else None,
        "outputs": config.outputs,
        "stages": [
            {"id": s.id, "nodes": [n.id for n in s.nodes]} for s in config.stages
        ],
    }


@app.post("/agents/{agent_id}/invoke")
async def invoke_agent(
    request: Request,
    agent_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    include_state: bool = Query(default=False),
) -> dict[str, Any]:
    entry = _require_flow(request, agent_id)
    return await run_flow(entry, payload, include_state=include_state)


# --- Error handling: map domain errors to clean HTTP responses ---------------


@app.exception_handler(InputValidationError)
async def _on_input_error(_: Request, exc: InputValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"status": "failed", "error": {"type": "invalid_input",
                                                "message": str(exc)}},
    )


@app.exception_handler(NodeExecutionError)
async def _on_node_error(_: Request, exc: NodeExecutionError) -> JSONResponse:
    logger.error("node execution failed", node_id=exc.node_id,
                 message=exc.message, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "status": "failed",
            "error": {
                "type": "node_execution_failed",
                "node_id": exc.node_id,
                "message": exc.message,
            },
        },
    )


@app.exception_handler(ConfigError)
async def _on_config_error(_: Request, exc: ConfigError) -> JSONResponse:
    # Should not normally happen post-startup, but surface it clearly if it does.
    return JSONResponse(
        status_code=500,
        content={"status": "failed", "error": {"type": "config_error",
                                                "message": str(exc)}},
    )
