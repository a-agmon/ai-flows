"""Flow-level data-source functions (placeholder implementations).

A *source* differs from a module *node*. It runs once, before the graph, turning
a flow's ``query`` into data that is merged into the initial state. Its contract
is therefore different: it receives the rendered ``query``, the request
``params``, and its static ``config`` -- and returns a dict whose keys are
injected into state.

    async def load(query: str, params: dict, config: dict) -> dict: ...

These placeholders stand in for a real datastore (a database, an HTTP API, a
vector store...). A real ``fetch_ticket`` would run ``query`` against that store;
here it just looks the id up in an in-memory table so the example flow runs with
no external dependency and no API key.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger("ai_flows.source.datasource")

# Stand-in for a real datastore. A production source would run the rendered
# ``query`` against a DB/API instead of indexing this dict.
_TICKETS: dict[str, dict] = {
    "T-100": {
        "subject": "Refund for a delayed order",
        "body": "My order arrived two weeks late and I would like a refund.",
        "priority": "high",
    },
    "T-200": {
        "subject": "How do I change my email?",
        "body": "I can't find where to update the email on my account.",
        "priority": "low",
    },
}


async def fetch_ticket(query: str, params: dict, config: dict) -> dict:
    """Load a support ticket by id and inject its fields into the flow state.

    The caller passes only ``ticket_id``; the source fetches the rest. Because
    source data is overridden by explicit request params, a caller may also pass
    ``subject``/``priority``/... directly to bypass the lookup.
    """
    ticket_id = params.get("ticket_id")
    log.info("source fetch", query=query.strip(), ticket_id=ticket_id)

    ticket = _TICKETS.get(ticket_id)
    if ticket is None:
        return {"ticket_found": False}
    return {"ticket_found": True, **ticket}


def triage_ticket(inputs: dict, state: dict, config: dict) -> dict:
    """Deterministically triage a loaded ticket by its priority.

    A plain module node (not a source): it reads the data the source injected and
    returns a routing decision. Kept sync to show sync module functions work.
    """
    if not inputs.get("found"):
        return {"triage": {"queue": "not_found"}}
    # Read priority straight off state: the source may inject it, so it isn't
    # mapped through `inputs` (which would fail loudly when absent).
    priority = state.get("priority", "normal")
    queue = "urgent" if priority == "high" else "standard"
    return {"triage": {"priority": priority, "queue": queue}}
