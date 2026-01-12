"""CLI error handling utilities.

This module provides unified error handling for all CLI commands, ensuring
consistent error messages and exit codes across the application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    import httpx

# Standard mask for hiding sensitive token values
TOKEN_MASK = "********"  # nosec B105 - display placeholder, not a real password


def mask_token(token: str) -> str:
    """Mask a token for display, showing only last 4 characters.

    Args:
        token: The token to mask.

    Returns:
        Masked token string with last 4 chars visible, or just the mask
        if token is 4 chars or shorter.

    Examples:
        >>> mask_token("mgp_1234567890abcdef")
        '********cdef'
        >>> mask_token("abc")
        '********'
    """
    if len(token) > 4:
        return TOKEN_MASK + token[-4:]
    return TOKEN_MASK


def format_auth_error(
    response: "httpx.Response",
    operation: str,
    token: str | None = None,
) -> str:
    """Format an authentication error message with optional masked token.

    Args:
        response: The HTTP response object.
        operation: Description of the operation that failed (e.g., "Upload", "Download").
        token: Optional token to include (masked) in the error message.

    Returns:
        Formatted error message string.
    """
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text

    base_msg = f"{operation} failed ({response.status_code}): {detail}"

    # Add masked token for auth errors to help debug wrong-token issues
    if response.status_code in (401, 403) and token:
        base_msg += f" (token: {mask_token(token)})"

    return base_msg


def handle_http_error(
    response: "httpx.Response",
    operation: str,
    token: str | None = None,
) -> None:
    """Handle HTTP error responses, raising ClickException.

    This is the unified error handler for all CLI commands. It extracts
    error details from HTTP responses and formats them consistently.

    For authentication errors (401/403), includes the masked token in the
    error message to help users identify which token was used.

    Args:
        response: The HTTP response object.
        operation: Description of the operation that failed.
        token: Optional token to include (masked) for auth errors.

    Raises:
        click.ClickException: Always raised with formatted error message.
    """
    raise click.ClickException(format_auth_error(response, operation, token))
