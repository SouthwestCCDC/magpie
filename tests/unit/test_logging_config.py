"""Unit tests for structured logging configuration."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest
import structlog

from magpie.config import MagpieSettings
from magpie.logging_config import configure_logging


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset structlog and logging state between tests for isolation."""
    # Reset before test
    structlog.reset_defaults()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)  # Reset to default level
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    yield
    # Reset after test
    structlog.reset_defaults()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)


def test_configure_logging_json_format(monkeypatch):
    """Test that JSON format configures structlog with JSON renderer."""
    # Create settings with JSON format
    settings = MagpieSettings(log_format="json", debug=False)

    # Capture logging output (logs go to stderr, not stdout)
    log_stream = StringIO()
    monkeypatch.setattr("sys.stderr", log_stream)

    # Configure logging
    configure_logging(settings)

    # Get a logger and log a message
    logger = structlog.get_logger("test_logger")
    logger.info("test_message", field1="value1", field2=42)

    # Get the output
    output = log_stream.getvalue()

    # Parse as JSON
    log_entry = json.loads(output.strip())

    # Verify JSON structure
    assert log_entry["event"] == "test_message"
    assert log_entry["field1"] == "value1"
    assert log_entry["field2"] == 42
    assert "timestamp" in log_entry
    assert log_entry["level"] == "info"
    assert log_entry["logger"] == "test_logger"


def test_configure_logging_console_format():
    """Test that console format configures structlog with console renderer."""
    # Create settings with console format
    settings = MagpieSettings(log_format="console", debug=True)

    # Configure logging (console output is harder to test, just verify no errors)
    configure_logging(settings)

    # Get a logger and log a message (should not raise)
    logger = structlog.get_logger("test_logger")
    logger.info("test_message", field1="value1")


def test_configure_logging_debug_level():
    """Test that debug=True sets logging level to DEBUG."""
    settings = MagpieSettings(log_format="json", debug=True)
    configure_logging(settings)

    # Verify root logger level is DEBUG
    root_logger = logging.getLogger()
    assert root_logger.level == logging.DEBUG


def test_configure_logging_info_level():
    """Test that debug=False sets logging level to INFO."""
    settings = MagpieSettings(log_format="json", debug=False)
    configure_logging(settings)

    # Verify root logger level is INFO
    root_logger = logging.getLogger()
    assert root_logger.level == logging.INFO


def test_structured_logging_with_context(monkeypatch):
    """Test that context variables work with structlog."""
    settings = MagpieSettings(log_format="json", debug=False)

    # Capture logging output (logs go to stderr, not stdout)
    log_stream = StringIO()
    monkeypatch.setattr("sys.stderr", log_stream)

    configure_logging(settings)

    # Bind context variables
    structlog.contextvars.bind_contextvars(request_id="test-request-123")

    # Log a message
    logger = structlog.get_logger("test_logger")
    logger.info("test_with_context", extra_field="value")

    # Get the output
    output = log_stream.getvalue()
    log_entry = json.loads(output.strip())

    # Verify context is included
    assert log_entry["request_id"] == "test-request-123"
    assert log_entry["event"] == "test_with_context"
    assert log_entry["extra_field"] == "value"

    # Clear context
    structlog.contextvars.clear_contextvars()


def test_otel_context_processor_without_otel():
    """Test that OTEL processor gracefully handles missing OTEL."""
    from magpie.logging_config import _add_otel_context

    # Should not raise even when OTEL is not available
    event_dict = {"event": "test"}
    logger = logging.getLogger("test")

    result = _add_otel_context(logger, "info", event_dict)

    # Should return event_dict unchanged
    assert result == event_dict


def test_noop_processor():
    """Test that noop processor returns event_dict unchanged."""
    from magpie.logging_config import _noop_processor

    event_dict = {"event": "test", "field": "value"}
    logger = logging.getLogger("test")

    result = _noop_processor(logger, "info", event_dict)

    assert result == event_dict
