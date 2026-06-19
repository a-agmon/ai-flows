"""Domain-specific exception types."""

from __future__ import annotations


class ConfigError(Exception):
    """Raised when a flow config is invalid. Fails service startup."""


class NodeExecutionError(Exception):
    """Raised when a node fails at runtime.

    Carries the node id so the API layer can return a structured error that
    points the caller at the exact node that failed.
    """

    def __init__(self, node_id: str, message: str) -> None:
        self.node_id = node_id
        self.message = message
        super().__init__(f"node '{node_id}' failed: {message}")
