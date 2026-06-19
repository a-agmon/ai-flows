"""Structured logging via structlog, integrated with the stdlib logging.

Application code logs through ``structlog.get_logger(...)``. Logs emitted by
third-party libraries through the stdlib :mod:`logging` (uvicorn, langchain,
etc.) are routed through the same renderer so output is uniform.

The output format and level are controlled by :class:`app.settings.Settings`
(``AI_FLOWS_LOG_FORMAT`` = ``console`` | ``json``, ``AI_FLOWS_LOG_LEVEL``).
"""

from __future__ import annotations

import logging
import sys

import structlog

from app.settings import Settings, settings as default_settings

# Processors shared by structlog-native and stdlib ("foreign") log records.
_SHARED_PROCESSORS: list = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
]


def configure_logging(settings: Settings = default_settings) -> None:
    """Configure structlog + stdlib logging. Idempotent and safe to re-call."""
    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            # Hand the event dict to the stdlib formatter below for rendering.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Applied to records coming from the stdlib logging (foreign records).
        foreign_pre_chain=[*_SHARED_PROCESSORS, structlog.stdlib.ExtraAdder()],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
