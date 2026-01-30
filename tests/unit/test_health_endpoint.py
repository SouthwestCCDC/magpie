"""Tests for /health endpoint version field."""

from __future__ import annotations

from fastapi.testclient import TestClient

from magpie import __version__
from magpie.server.app import app


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_ok_status(self) -> None:
        """Health endpoint returns status: ok."""
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_health_returns_version(self) -> None:
        """Health endpoint returns version field."""
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert data["version"] == __version__

    def test_health_endpoint_is_public(self) -> None:
        """Health endpoint does not require authentication."""
        client = TestClient(app)
        # No auth headers
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data
