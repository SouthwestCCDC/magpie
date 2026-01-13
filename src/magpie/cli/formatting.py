"""CLI output formatting utilities for JSON and human-readable output.

This module provides a unified output formatting system that supports multiple
output formats (human-readable, JSON) across all CLI commands.

Usage:
    from magpie.cli.formatting import format_option, output_result, output_error, CommandResult

    @cli.command()
    @format_option
    def my_command():
        try:
            # Do work...
            output_result(CommandResult(
                data={"key": "value"},
                human_output="Human readable output"
            ))
        except SomeError as e:
            output_error("ERROR_CODE", str(e))

Note: This module was generated with AI assistance (Claude Code w/ Opus 4.5).
This content was AI-generated.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, TypeVar

import click

if TYPE_CHECKING:
    from collections.abc import Mapping

F = TypeVar("F", bound=Callable[..., object])


class OutputFormat(str, Enum):
    """Supported output formats."""

    HUMAN = "human"
    JSON = "json"
    # Future: TABLE = "table", XML = "xml"


def format_option(func: F) -> F:
    """Decorator that adds --format option to a command.

    Stores the selected format in the Click context for use by output_result
    and output_error.

    Usage:
        @cli.command()
        @format_option
        def my_command():
            ...
    """

    @click.option(
        "--format",
        "output_format",
        type=click.Choice([f.value for f in OutputFormat], case_sensitive=False),
        default=OutputFormat.HUMAN.value,
        help="Output format (default: human)",
    )
    @wraps(func)
    def wrapper(*args: object, output_format: str, **kwargs: object) -> object:
        ctx = click.get_current_context()
        ctx.ensure_object(dict)
        ctx.obj["output_format"] = OutputFormat(output_format)
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def get_output_format() -> OutputFormat:
    """Get the current output format from Click context.

    Looks for output_format attribute on the context object (CLIContext or CTLContext).

    Returns:
        The current output format, defaulting to HUMAN if not set.
    """
    ctx = click.get_current_context(silent=True)
    if ctx is None or ctx.obj is None:
        return OutputFormat.HUMAN

    # Check if ctx.obj has output_format attribute (CLIContext or CTLContext)
    if hasattr(ctx.obj, "output_format"):
        return ctx.obj.output_format

    # Fall back to dict-style access for format_option decorator usage
    if isinstance(ctx.obj, dict):
        return ctx.obj.get("output_format", OutputFormat.HUMAN)

    return OutputFormat.HUMAN


def is_json_output() -> bool:
    """Check if JSON output format is currently active.

    Returns:
        True if JSON output is enabled, False otherwise.
    """
    return get_output_format() == OutputFormat.JSON


@dataclass
class CommandResult:
    """Standardized command result container.

    Attributes:
        data: Structured data for JSON output. Should be JSON-serializable.
        human_output: Pre-formatted human-readable string for terminal display.
    """

    data: Any
    human_output: str


def _json_serializer(obj: object) -> str:
    """Custom JSON serializer for non-standard types.

    Args:
        obj: Object to serialize.

    Returns:
        String representation of the object.

    Raises:
        TypeError: If object is not serializable.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def output_result(result: CommandResult) -> None:
    """Output command result in the appropriate format.

    For JSON format, wraps data in the standard envelope:
        {"status": "ok", "data": ...}

    For human format, outputs the pre-formatted string directly.

    Args:
        result: CommandResult containing both structured data and human output.
    """
    output_format = get_output_format()

    if output_format == OutputFormat.JSON:
        envelope = {"status": "ok", "data": result.data}
        click.echo(json.dumps(envelope, indent=2, default=_json_serializer))
    else:
        click.echo(result.human_output)


def output_error(code: str, message: str, exit_code: int = 1) -> None:
    """Output error in the appropriate format and exit.

    For JSON format, outputs to stderr:
        {"status": "error", "error": {"code": "...", "message": "..."}}

    For human format, outputs "Error: {message}" to stderr.

    Args:
        code: Error code (e.g., "NOT_FOUND", "UNAUTHORIZED").
        message: Human-readable error message.
        exit_code: Exit code to use (default: 1).

    Note:
        This function always exits the program.
    """
    output_format = get_output_format()

    if output_format == OutputFormat.JSON:
        envelope = {"status": "error", "error": {"code": code, "message": message}}
        click.echo(json.dumps(envelope, indent=2), err=True)
    else:
        click.echo(f"Error: {message}", err=True)

    sys.exit(exit_code)


# Standard error codes for consistency across commands
class ErrorCode:
    """Standard error codes for JSON output."""

    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    IO_ERROR = "IO_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"


def http_status_to_error_code(status_code: int) -> str:
    """Map HTTP status code to standard error code.

    Args:
        status_code: HTTP response status code.

    Returns:
        Corresponding error code string.
    """
    mapping: Mapping[int, str] = {
        400: ErrorCode.VALIDATION_ERROR,
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.CONFLICT,
    }
    if status_code in mapping:
        return mapping[status_code]
    if status_code >= 500:
        return ErrorCode.SERVER_ERROR
    return ErrorCode.VALIDATION_ERROR
