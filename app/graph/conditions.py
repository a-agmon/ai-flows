"""Evaluation of the simple, side-effect-free conditions used by flows."""

from __future__ import annotations

from typing import Any

from app.config.models import ConditionConfig


def get_nested_value(state: dict[str, Any], path: str) -> Any:
    """Resolve a possibly dotted ``path`` against ``state``.

    Returns ``None`` if any segment is missing, so conditions can safely test
    fields that a node may not have produced (e.g. when a stage was skipped).
    """
    current: Any = state
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def evaluate_condition(condition: ConditionConfig, state: dict[str, Any]) -> bool:
    """Return whether ``condition`` holds for the current ``state``.

    Exactly one operator is guaranteed to be set by the model validator, so the
    first matching branch is the operative one.
    """
    value = get_nested_value(state, condition.field)
    fields = condition.model_fields_set

    if "exists" in fields:
        return (value is not None) if condition.exists else (value is None)
    if "equals" in fields:
        return value == condition.equals
    if "not_equals" in fields:
        return value != condition.not_equals
    if "contains" in fields:
        try:
            return condition.contains in value  # type: ignore[operator]
        except TypeError:
            return False
    if "in_" in fields:
        return value in (condition.in_ or [])

    # Unreachable: ConditionConfig validates that one operator is present.
    raise ValueError(f"condition on '{condition.field}' has no operator")
