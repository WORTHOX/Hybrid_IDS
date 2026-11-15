"""Structured logging. Production code logs through :func:`get_logger`, never ``print``."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    """Configure structlog + stdlib logging once per process.

    Args:
        level: Minimum log level name.
        json: Emit one JSON object per line (Kaggle, CI, services). ``False`` gives a
            human-readable console renderer for local development.
    """
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"unknown log level: {level}")

    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str, **initial: object) -> structlog.stdlib.BoundLogger:
    """Return a bound logger tagged with the component name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name).bind(
        component=name, **initial
    )
    return logger
