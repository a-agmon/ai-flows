"""Pure data-shaping module nodes."""

from __future__ import annotations

import json
from typing import Any


async def assemble_letter(inputs: dict, state: dict, config: dict) -> str:
    """Join the independently generated sections into one draft letter."""
    intro = inputs["intro"]
    policy = inputs.get("policy", "")
    closing = inputs["closing"]
    parts = [p for p in (intro, policy, closing) if p]
    return "\n\n".join(parts)


async def guardrail_check(inputs: dict, state: dict, config: dict) -> dict:
    """Deterministic safety checks over the finished discharge letter.

    Returns two state keys (hence ``merge_output: true`` on the node): a boolean
    pass/fail and the list of issues found, so downstream consumers or the API
    response can react without re-running the model.
    """
    letter = inputs["letter"] or ""
    issues: list[str] = []
    if len(letter.strip()) < 100:
        issues.append("letter is suspiciously short")
    if "{{" in letter or "}}" in letter:
        issues.append("unrendered template markers left in output")
    patient_name = state.get("discharge", {}).get("patient_name")
    if patient_name and patient_name not in letter:
        issues.append("patient name is missing from the letter")
    return {"guardrail_passed": not issues, "guardrail_issues": issues}


async def unpack_classification(inputs: dict, state: dict, config: dict) -> dict:
    """Parse the classifier's JSON output into individual state fields.

    Tolerates models that wrap JSON in prose or code fences. On parse failure it
    treats the request as unsupported rather than raising, so the ``end_if``
    guard downstream can reject it cleanly.
    """
    raw = inputs["classification"]
    data = _extract_json(raw)
    if not isinstance(data, dict):
        return {
            "request_status": "unsupported",
            "rejection_reason": "could not parse classification output",
        }
    return {
        "request_status": data.get("request_status", "unsupported"),
        "rejection_reason": data.get("rejection_reason"),
    }


def _extract_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first {...} block, e.g. when fenced in ```json.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
