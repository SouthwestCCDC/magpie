"""Tests for request correlation and logging middleware."""

from __future__ import annotations

import re

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from magpie.server.middleware import RequestLoggingMiddleware


@pytest.fixture
def app_with_middleware() -> FastAPI:
    """Create a test FastAPI app with RequestLoggingMiddleware."""
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/test")
    async def test_endpoint() -> dict:
        return {"status": "ok"}

    @app.get("/error")
    async def error_endpoint() -> dict:
        raise ValueError("Test error")

    return app


@pytest.fixture
def client(app_with_middleware: FastAPI) -> TestClient:
    """Create a test client for the app."""
    return TestClient(app_with_middleware, raise_server_exceptions=False)


class TestRequestLoggingMiddleware:
    """Tests for RequestLoggingMiddleware."""

    def test_request_id_generation(self, client: TestClient) -> None:
        """Test that each request gets a unique UUID request_id."""
        response1 = client.get("/test")
        response2 = client.get("/test")

        request_id1 = response1.headers.get("X-Request-ID")
        request_id2 = response2.headers.get("X-Request-ID")

        # Both should have request IDs
        assert request_id1 is not None
        assert request_id2 is not None

        # Request IDs should be valid UUIDs
        uuid_pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        assert uuid_pattern.match(request_id1)
        assert uuid_pattern.match(request_id2)

        # Request IDs should be unique
        assert request_id1 != request_id2

    def test_x_request_id_header_in_response(self, client: TestClient) -> None:
        """Test that X-Request-ID header is present in responses."""
        response = client.get("/test")

        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        assert len(response.headers["X-Request-ID"]) == 36  # UUID length

    def test_successful_request_returns_200(self, client: TestClient) -> None:
        """Test that successful requests return 200 with request ID."""
        response = client.get("/test")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert "X-Request-ID" in response.headers

    def test_error_request_returns_500(self, client: TestClient) -> None:
        """Test that error requests return 500.

        Note: When an unhandled exception occurs, FastAPI's default exception
        handler creates the response, so the X-Request-ID header from our
        middleware is not included. The request_failed event is still logged
        with the request_id for correlation.
        """
        response = client.get("/error")

        assert response.status_code == 500

    def test_context_cleanup_after_request(self, client: TestClient) -> None:
        """Test that structlog context is cleaned up after request."""
        # Make a request
        client.get("/test")

        # Context should be cleared after request
        # Get current context vars - should be empty or not contain request_id
        ctx = structlog.contextvars.get_contextvars()
        assert "request_id" not in ctx

    def test_multiple_concurrent_requests_have_unique_ids(self, client: TestClient) -> None:
        """Test that multiple requests get unique request IDs."""
        request_ids = set()

        for _ in range(10):
            response = client.get("/test")
            request_id = response.headers.get("X-Request-ID")
            assert request_id is not None
            assert request_id not in request_ids, "Request ID should be unique"
            request_ids.add(request_id)

        assert len(request_ids) == 10
