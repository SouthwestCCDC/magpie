"""Middleware for request correlation and logging context."""

from __future__ import annotations

import time
import uuid
from typing import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

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
            structlog.contextvars.clear_contextvars()
