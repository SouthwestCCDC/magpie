"""CLI error handling utilities.

This module provides unified error handling for all CLI commands, ensuring
consistent error messages and exit codes across the application.
"""

from __future__ import annotations

import errno
import json
import socket
import ssl
from functools import wraps
from typing import Callable, TypeVar
from urllib.parse import urlparse

import click
import httpx

F = TypeVar("F", bound=Callable[..., object])

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


def error_detail(response: "httpx.Response") -> str:
    """Extract the human-readable text from an error response.

    Magpie's server errors carry their explanation in ``message``, while some
    framework-generated errors use FastAPI's ``detail``; prefer whichever is
    populated before falling back to the raw body.

    Args:
        response: The HTTP response object.

    Returns:
        Error text for display.
    """
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return response.text

    if not isinstance(body, dict):
        return response.text

    for key in ("detail", "message"):
        value = body.get(key)
        if value:
            return str(value)

    return response.text


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
    base_msg = f"{operation} failed ({response.status_code}): {error_detail(response)}"

    # Add masked token for auth errors to help debug wrong-token issues
    if response.status_code in (401, 403) and token:
        base_msg += f" (token: {mask_token(token)})"

    return base_msg


def handle_http_error(
    response: "httpx.Response",
    operation: str,
    token: str | None = None,
) -> None:
    """Handle HTTP error responses, raising ClickException with appropriate exit code.

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
    # Import here to avoid circular imports
    from magpie.cli.formatting import http_status_to_exit_code

    error_msg = format_auth_error(response, operation, token)
    exit_code = http_status_to_exit_code(response.status_code)

    # ClickException defaults to exit code 1, but we want specific codes
    # So we raise ClickException (which formats the error) then exit with our code
    exc = click.ClickException(error_msg)
    exc.exit_code = exit_code
    raise exc


def handle_response_error(
    response: "httpx.Response",
    operation: str,
    token: str | None = None,
) -> None:
    """Handle HTTP error responses for both JSON and human-readable output modes.

    This is the unified error handler that replaces duplicated error handling
    code across CLI commands. It:
    - Checks if JSON output mode is active
    - Extracts error detail from response (trying JSON first, falling back to text)
    - In JSON mode: calls output_error() with appropriate error code
    - In human mode: raises click.ClickException via handle_http_error()

    Args:
        response: The HTTP response object.
        operation: Description of the operation that failed (e.g., "Upload", "Download").
        token: Optional token to include (masked) for auth errors in human mode.

    Note:
        This function never returns - it always raises an exception or calls sys.exit().
    """
    # Import here to avoid circular imports
    from magpie.cli.formatting import (
        http_status_to_error_code,
        http_status_to_exit_code,
        is_json_output,
        output_error,
    )

    if is_json_output():
        output_error(
            http_status_to_error_code(response.status_code),
            error_detail(response),
            exit_code=http_status_to_exit_code(response.status_code),
        )
        # output_error never returns (calls sys.exit), but this makes it explicit
        return  # pragma: no cover

    handle_http_error(response, operation, token)


def is_path_prefix(client: "httpx.Client", path: str) -> bool:
    """Check whether a path is a browsable prefix rather than an artifact.

    Used after an artifact lookup 404s, to distinguish "this path doesn't
    exist at all" from "this path is a directory-like prefix with children,
    not an artifact" -- e.g. `magpie info smoke` when only `smoke/hello`
    exists.

    The listing endpoint returns the path itself when it IS an artifact (e.g.
    `magpie info existing/artifact:badtag`, where the artifact exists but the
    ref doesn't), so only entries strictly under `<path>/` count as children --
    the exact path is excluded to avoid misreporting a real artifact with a
    bad ref as a path prefix.

    Args:
        client: HTTP client.
        path: Normalized artifact path that failed to resolve as an artifact.

    Returns:
        True if the server reports at least one path under this prefix,
        False otherwise (including if the check itself fails).
    """
    try:
        response = client.get("/api/v1/artifacts", params={"prefix": path, "recursive": False})
    except httpx.RequestError:
        return False

    if response.status_code != 200:
        return False

    try:
        paths = response.json().get("paths", [])
    except (ValueError, AttributeError):
        # ValueError covers json.JSONDecodeError (its base class); AttributeError
        # covers a response body that's valid JSON but not a dict (e.g. a list).
        return False

    prefix_marker = path.rstrip("/") + "/"
    return any(p.startswith(prefix_marker) for p in paths)


def format_prefix_not_artifact_error(path: str) -> str:
    """Format the error message shown when a path resolves to a prefix, not an artifact.

    Args:
        path: The path that was requested.

    Returns:
        User-facing message pointing the user to `magpie ls <path>/`.
    """
    return f'"{path}" is a path prefix, not an artifact -- try: magpie ls {path}/'


def _extract_hostname_safely(url: str) -> str:
    """Extract hostname (and optionally port) from URL, never credentials.

    This uses urllib.parse.urlparse to safely extract only the hostname and port,
    ensuring that credentials embedded in URLs (e.g., https://user:pass@host) are
    never included in user-facing error messages.

    Handles IPv6 addresses by wrapping them in brackets when a port is present.
    Returns a safe placeholder instead of the original URL if parsing fails,
    preventing credential leaks in error cases.

    Args:
        url: The URL to extract the hostname from.

    Returns:
        Hostname and port (if non-default), or "server" if parsing fails.

    Examples:
        >>> _extract_hostname_safely("https://artifacts.example.com:8443/api")
        'artifacts.example.com:8443'
        >>> _extract_hostname_safely("https://user:pass@artifacts.example.com")
        'artifacts.example.com'
        >>> _extract_hostname_safely("https://[::1]:8443/api")
        '[::1]:8443'
        >>> _extract_hostname_safely("https://user:pass@host:invalid")
        'host'
    """
    try:
        parsed = urlparse(url)
        if parsed.hostname:
            # Try to get port, but handle invalid port gracefully
            try:
                port = parsed.port
                if port:
                    host = parsed.hostname
                    # For IPv6 literals, urlparse.hostname omits brackets
                    # Add them when a port is present for standard notation
                    if ":" in host and not host.startswith("["):
                        host = f"[{host}]"
                    return f"{host}:{port}"
            except ValueError:
                # Invalid port - just return hostname without port
                pass
            return parsed.hostname
    except (ValueError, AttributeError):
        # If parsing fails completely, return safe placeholder
        pass
    # Never return the original URL as it may contain credentials
    return "server"


def format_network_error(exc: Exception, operation: str, server: str | None = None) -> str:
    """Format a network exception into a user-friendly error message.

    Args:
        exc: The network exception to format.
        operation: Description of the operation that failed.
        server: Optional server URL for context.

    Returns:
        User-friendly error message with optional hint.
    """
    # Extract hostname from server URL for clearer messages
    # Use safe extraction to avoid leaking credentials in error messages
    hostname = _extract_hostname_safely(server) if server else "server"

    # Handle different network error types
    if isinstance(exc, httpx.ConnectError):
        # Check if it's a DNS resolution error
        if isinstance(exc.__cause__, socket.gaierror):
            msg = f"{operation} failed: Could not connect to server '{hostname}': DNS resolution failed"
            hint = "Check that the server hostname is correct and your network is connected."
            return f"{msg}\nHint: {hint}"

        # Check for specific connection failure types by inspecting the cause
        cause = exc.__cause__
        if isinstance(cause, ConnectionRefusedError):
            msg = (
                f"{operation} failed: Could not connect to server '{hostname}': Connection refused"
            )
            hint = "Check that the server is running and the URL is correct."
            return f"{msg}\nHint: {hint}"
        if isinstance(cause, ConnectionResetError):
            msg = f"{operation} failed: Could not connect to server '{hostname}': Connection reset by peer"
            hint = "The server closed the connection. Check server logs or try again."
            return f"{msg}\nHint: {hint}"
        # Note: ssl.SSLError inherits from OSError, so it must be checked before OSError
        if isinstance(cause, ssl.SSLError):
            msg = f"{operation} failed: Could not connect to server '{hostname}': TLS handshake failed"
            hint = (
                "Check server certificate configuration or try using http:// instead of https://."
            )
            return f"{msg}\nHint: {hint}"
        if isinstance(cause, OSError):
            # Check errno for common network conditions
            errno_val = getattr(cause, "errno", None)
            if errno_val == errno.ENETUNREACH:
                msg = f"{operation} failed: Could not connect to server '{hostname}': Network unreachable"
                hint = "Check your network connection and routing configuration."
                return f"{msg}\nHint: {hint}"
            # Fall through to generic connection failed for other OSErrors

        # Generic connection failure for unrecognized causes
        msg = f"{operation} failed: Could not connect to server '{hostname}': Connection failed"
        if cause:
            msg += f" ({cause})"
        hint = "Check that the server is running and the URL is correct."
        return f"{msg}\nHint: {hint}"

    if isinstance(exc, httpx.TimeoutException):
        msg = f"{operation} failed: Request to server '{hostname}' timed out"
        hint = "Check your network connection or try increasing the timeout with --timeout."
        return f"{msg}\nHint: {hint}"

    if isinstance(exc, httpx.ProxyError):
        msg = f"{operation} failed: Proxy error connecting to '{hostname}': {exc}"
        hint = "Check your proxy configuration and that the proxy is accessible."
        return f"{msg}\nHint: {hint}"

    if isinstance(exc, httpx.UnsupportedProtocol):
        msg = f"{operation} failed: Unsupported protocol connecting to '{hostname}': {exc}"
        hint = "Check that the server URL uses a supported protocol (http/https)."
        return f"{msg}\nHint: {hint}"

    if isinstance(exc, httpx.ProtocolError):
        msg = f"{operation} failed: Protocol error connecting to '{hostname}': {exc}"
        hint = "The server sent an invalid response. Check server logs or try again."
        return f"{msg}\nHint: {hint}"

    if isinstance(exc, httpx.RequestError):
        # Generic request error (catches all other RequestError subclasses)
        msg = f"{operation} failed: Network error connecting to '{hostname}': {exc}"
        hint = "Check your network connection and server configuration."
        return f"{msg}\nHint: {hint}"

    if isinstance(exc, socket.gaierror):
        # Standalone DNS resolution error (not wrapped in httpx exception)
        msg = f"{operation} failed: Could not connect to server '{hostname}': DNS resolution failed"
        hint = "Check that the server hostname is correct and your network is connected."
        return f"{msg}\nHint: {hint}"

    # Fallback for unexpected network errors
    return f"{operation} failed: Network error: {exc}"


def handle_network_error(exc: Exception, operation: str, server: str | None = None) -> None:
    """Handle network exceptions with user-friendly messages and appropriate exit codes.

    Args:
        exc: The network exception to handle.
        operation: Description of the operation that failed.
        server: Optional server URL for context in error messages.

    Raises:
        click.ClickException: Always raised with formatted error message.
        SystemExit: Exits with appropriate network error code.
    """
    # Import here to avoid circular imports
    from magpie.cli.formatting import ErrorCode, ExitCode, is_json_output, output_error

    error_msg = format_network_error(exc, operation, server)

    if is_json_output():
        output_error(ErrorCode.NETWORK_ERROR, error_msg, exit_code=ExitCode.NETWORK_ERROR)
        return  # pragma: no cover - output_error never returns

    # For human output, raise ClickException which will be caught by Click
    # and will exit with the code we specify
    click_exc = click.ClickException(error_msg)
    click_exc.exit_code = ExitCode.NETWORK_ERROR
    raise click_exc


def with_network_error_handling(func: F) -> F:
    """Decorator to wrap CLI commands with network and response parsing error handling.

    This decorator catches network-related exceptions (RequestError and all subclasses
    including ConnectError, TimeoutException, ProxyError, ProtocolError, etc.) and
    response parsing errors, converting them to user-friendly error messages with
    appropriate exit codes.

    Coverage includes:
    - httpx.RequestError: Base class for all request errors
      - httpx.TransportError: All transport-level errors
        - httpx.ConnectError: Connection failures, DNS errors
        - httpx.TimeoutException: Request timeouts
        - httpx.ProxyError: Proxy configuration or connectivity issues
        - httpx.UnsupportedProtocol: Invalid protocol in URL
        - httpx.ProtocolError: HTTP protocol violations
      - httpx.DecodingError: Response decoding failures
      - httpx.TooManyRedirects: Redirect loop detection
    - httpx.HTTPStatusError: HTTP error status codes (4xx, 5xx) raised by raise_for_status()
    - json.JSONDecodeError: Response JSON parsing errors
    - socket.gaierror: DNS errors that occur outside httpx

    Unexpected exceptions (AttributeError, KeyError, etc.) are allowed to propagate
    so they're visible in debug mode and captured by Sentry for diagnosis.

    Usage:
        @click.command()
        @with_network_error_handling
        def my_command(ctx):
            # Command implementation that makes HTTP calls
            ...
    """

    @wraps(func)
    def wrapper(*args: object, **kwargs: object) -> object:
        # Import here to avoid circular imports
        from magpie.cli.formatting import (
            ErrorCode,
            ExitCode,
            http_status_to_exit_code,
            is_json_output,
            output_error,
        )

        try:
            return func(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            # HTTP error responses (4xx, 5xx) raised by raise_for_status()
            # NOTE: This handler is defensive/future-proofing. Currently no CLI code
            # calls raise_for_status() - all code manually checks response.status_code
            # and calls handle_response_error() directly. If someone adds
            # raise_for_status() in the future, this will handle it correctly.

            # Extract token from context if available
            ctx = click.get_current_context(silent=True)
            token = None
            if ctx and ctx.obj and hasattr(ctx.obj, "token"):
                token = ctx.obj.token

            # Use handle_response_error for consistent error formatting
            handle_response_error(exc.response, operation=func.__name__, token=token)
            # Unreachable: handle_response_error never returns
            raise SystemExit(http_status_to_exit_code(exc.response.status_code))  # pragma: no cover
        except httpx.RequestError as exc:
            # Catch all RequestError subclasses (TransportError, DecodingError, TooManyRedirects, etc.)
            # format_network_error() handles specific types with custom messages
            ctx = click.get_current_context(silent=True)
            server = None
            if ctx and ctx.obj and hasattr(ctx.obj, "server"):
                server = ctx.obj.server

            handle_network_error(exc, operation=func.__name__, server=server)
            # handle_network_error raises ClickException, but for type checker:
            raise SystemExit(ExitCode.NETWORK_ERROR)  # pragma: no cover
        except json.JSONDecodeError as exc:
            # Response parsing errors - server returned invalid JSON
            error_msg = f"Invalid JSON response from server: {exc.msg} at line {exc.lineno} column {exc.colno}"
            hint = "The server may be misconfigured or returned an error page. Check server logs."
            full_msg = f"{error_msg}\nHint: {hint}"

            if is_json_output():
                output_error(ErrorCode.NETWORK_ERROR, full_msg, exit_code=ExitCode.NETWORK_ERROR)
                return  # pragma: no cover - output_error never returns

            click_exc = click.ClickException(full_msg)
            click_exc.exit_code = ExitCode.NETWORK_ERROR
            raise click_exc
        except socket.gaierror as exc:
            # DNS resolution errors can occur outside of httpx
            ctx = click.get_current_context(silent=True)
            server = None
            if ctx and ctx.obj and hasattr(ctx.obj, "server"):
                server = ctx.obj.server

            handle_network_error(exc, operation=func.__name__, server=server)
            raise SystemExit(ExitCode.NETWORK_ERROR)  # pragma: no cover

    return wrapper  # type: ignore[return-value]
