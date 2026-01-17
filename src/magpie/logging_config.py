"""Logging configuration with structlog for structured JSON logging."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from magpie.config import MagpieSettings


def configure_logging(settings: MagpieSettings) -> None:
    """Configure structlog with JSON or console output based on settings.

    Sets up structlog with:
    - JSON or console formatting based on MAGPIE_LOG_FORMAT
    - OTEL correlation (trace_id, span_id) when OTEL is enabled
    - Standard fields: timestamp, level, logger, message
    - Request correlation via request_id (added by middleware)

    Args:
        settings: MagpieSettings instance with log_format configuration.
    """
    # Determine processors based on log format
    if settings.log_format == "json":
        processors: list[Any] = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            # Add OTEL context if enabled
            _add_otel_context if settings.otel_enabled else _noop_processor,
            structlog.processors.JSONRenderer(),
        ]
    else:  # console format for development
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            # Add OTEL context if enabled
            _add_otel_context if settings.otel_enabled else _noop_processor,
            structlog.dev.ConsoleRenderer(),
        ]

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to work with structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if settings.debug else logging.INFO,
    )


def _add_otel_context(
    logger: logging.Logger, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Add OpenTelemetry trace_id and span_id to log events.

    Args:
        logger: Logger instance.
        method_name: Name of the log method.
        event_dict: Event dictionary to modify.

    Returns:
        Modified event dictionary with trace_id and span_id if available.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
    except (ImportError, AttributeError):
        # OTEL not available or no active span
        pass

    return event_dict


def _noop_processor(
    logger: logging.Logger, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """No-op processor that returns event_dict unchanged.

    Args:
        logger: Logger instance.
        method_name: Name of the log method.
        event_dict: Event dictionary.

    Returns:
        Unmodified event dictionary.
    """
    return event_dict
