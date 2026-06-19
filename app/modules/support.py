"""Module nodes for the `support_reply` example flow.

Demonstrates how a module function uses structured logging: just grab a
structlog logger. The runtime binds ``run_id``/``agent_id``/``node_id`` into
structlog's contextvars around each node, so these log lines are automatically
correlated with the flow run -- you don't pass any of that in yourself.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger("ai_flows.module.support")


async def unpack_triage(inputs: dict, state: dict, config: dict) -> dict:
    """Parse the triage classifier's JSON into individual state fields.

    Tolerates models that wrap JSON in prose/code fences. On a parse failure it
    marks the request as not handleable so the stage's ``end_if`` can stop early.
    """
    data = _extract_json(inputs["classification"])
    if not isinstance(data, dict):
        log.warning("triage parse failed", raw_preview=str(inputs["classification"])[:120])
        return {
            "category": "unknown",
            "urgency": "unknown",
            "can_handle": False,
            "rejection_reason": "could not parse triage output",
        }

    result = {
        "category": data.get("category", "unknown"),
        "urgency": data.get("urgency", "normal"),
        "can_handle": bool(data.get("can_handle", True)),
        "rejection_reason": data.get("rejection_reason"),
    }
    log.info(
        "triage parsed",
        category=result["category"],
        urgency=result["urgency"],
        can_handle=result["can_handle"],
    )
    return result


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
