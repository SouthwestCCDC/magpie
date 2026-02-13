"""Middleware for request correlation, logging context, and version checking."""

from __future__ import annotations

import time
import uuid
from typing import Callable

import structlog
from fastapi import Request, Response
from packaging.version import Version, parse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from magpie import __version__

logger = structlog.get_logger()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to add request correlation IDs and log request/response.

    Adds a unique request_id to each request and includes it in all logs
    generated during request processing. Also logs request start/completion
    with timing information.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with correlation ID and timing.

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware or route handler.

        Returns:
            HTTP response from handler.
        """
        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Bind request_id to structlog context for this request
        # Note: Python's contextvars are automatically copied when creating child tasks
        # (each request is an async task), so this is isolated per-request. We clear
        # first to ensure clean state, then bind the request_id. This middleware should
        # run early in the middleware chain before other middleware that might set context.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Record start time
        start_time = time.perf_counter()

        # Log request start
        logger.info(
            "request_start",
            method=request.method,
            path=request.url.path,
            client_host=request.client.host if request.client else None,
        )

        try:
            # Process request
            response = await call_next(request)

            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Log request completion
            logger.info(
                "request_complete",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

            # Add request_id to response headers for client correlation
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as exc:
            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Log request failure
            logger.error(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
                error=str(exc),
                exc_info=True,
            )
            raise

        finally:
            # Clear context vars after request
            # Note: This is safe because contextvars are isolated per async task
            structlog.contextvars.clear_contextvars()


def get_min_client_version(server_version: str) -> str:
    """Calculate minimum compatible client version from server version.

    Default policy: Minimum client is the minor floor of server version.
    Example: server 0.1.3 -> min client 0.1.0

    Args:
        server_version: Server version string (e.g., "0.1.3")

    Returns:
        Minimum client version string (e.g., "0.1.0")

    Note:
        packaging.version.parse() always returns a Version object for standard
        and dev versions (e.g., "0.0.0-dev" -> Version('0.0.0.dev0')).
        The minor floor calculation works correctly for both cases.
    """
    parsed = parse(server_version)
    # parse() always returns a Version object for valid semver/PEP440 versions
    if isinstance(parsed, Version):
        return f"{parsed.major}.{parsed.minor}.0"
    # This fallback is unreachable for standard/dev versions but kept for safety
    return server_version


class VersionCheckMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce client version compatibility.

    Stamps every response with X-Magpie-Server-Version.
    Reads User-Agent header to check client version.
    Returns 426 Upgrade Required if client is too old.
    """

    def __init__(self, app, min_client_version: str | None = None):
        """Initialize version check middleware.

        Args:
            app: ASGI application
            min_client_version: Minimum compatible client version (default: minor floor of server)
        """
        super().__init__(app)
        self.server_version = __version__
        self.min_client_version = min_client_version or get_min_client_version(__version__)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Check client version and stamp server version.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware or route handler

        Returns:
            HTTP response (426 if client too old, otherwise normal response)
        """
        # Exempt /health endpoint from version checking
        if request.url.path == "/health":
            response = await call_next(request)
            response.headers["X-Magpie-Server-Version"] = self.server_version
            return response

        # Parse User-Agent header for client version
        user_agent = request.headers.get("User-Agent", "")
        if user_agent.startswith("magpie-cli/"):
            # Extract version string from "magpie-cli/0.1.0" or "magpie-cli/0.1.0 python-httpx/..."
            # Handle edge case: "magpie-cli/" with no version
            version_part = user_agent.split("/", 1)[1].split()
            if not version_part:
                # Empty version string - allow the request
                response = await call_next(request)
                response.headers["X-Magpie-Server-Version"] = self.server_version
                return response

            client_version_str = version_part[0]

            try:
                client_version = parse(client_version_str)
                min_version = parse(self.min_client_version)

                if isinstance(client_version, Version) and isinstance(min_version, Version):
                    if client_version < min_version:
                        return JSONResponse(
                            status_code=426,
                            content={
                                "error": "Client version too old",
                                "detail": f"magpie-cli {client_version_str} is not compatible with server {self.server_version}. "
                                f"Minimum required client version: {self.min_client_version}. "
                                f"Please upgrade: pip install --upgrade magpie",
                                "client_version": client_version_str,
                                "server_version": self.server_version,
                                "min_client_version": self.min_client_version,
                            },
                            headers={
                                "X-Magpie-Server-Version": self.server_version,
                                "X-Magpie-Min-Client-Version": self.min_client_version,
                            },
                        )
            except (ValueError, AttributeError, IndexError):
                # If parsing fails, allow the request (don't block on malformed User-Agent)
                logger.debug(
                    "Failed to parse client version from User-Agent",
                    exc_info=True,
                    user_agent=user_agent,
                    client_version_str=client_version_str,
                )

        # Process request normally
        response = await call_next(request)
        response.headers["X-Magpie-Server-Version"] = self.server_version

        return response
