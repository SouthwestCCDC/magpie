"""Tests for version checking middleware."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from magpie import __version__
from magpie.server.middleware import VersionCheckMiddleware, get_min_client_version


class TestGetMinClientVersion:
    """Tests for get_min_client_version helper function."""

    def test_standard_version(self) -> None:
        """Test that standard versions return minor floor."""
        assert get_min_client_version("0.1.3") == "0.1.0"
        assert get_min_client_version("1.2.5") == "1.2.0"
        assert get_min_client_version("2.0.1") == "2.0.0"

    def test_dev_version_returns_minor_floor(self) -> None:
        """Test that dev versions also return minor floor (packaging parses them as Version)."""
        assert get_min_client_version("0.0.0-dev") == "0.0.0"
        assert get_min_client_version("0.1.2-dev") == "0.1.0"


@pytest.fixture
def app_with_version_middleware() -> FastAPI:
    """Create a test FastAPI app with VersionCheckMiddleware."""
    app = FastAPI()
    app.add_middleware(VersionCheckMiddleware, min_client_version="0.1.0")

    @app.get("/test")
    async def test_endpoint() -> dict:
        return {"status": "ok"}

    @app.get("/health")
    async def health_endpoint() -> dict:
        return {"status": "ok", "version": __version__}

    return app


@pytest.fixture
def client(app_with_version_middleware: FastAPI) -> TestClient:
    """Create a test client for the app."""
    return TestClient(app_with_version_middleware)


class TestVersionCheckMiddleware:
    """Tests for VersionCheckMiddleware."""

    def test_server_version_header_present(self, client: TestClient) -> None:
        """Test that X-Magpie-Server-Version header is in all responses."""
        response = client.get("/test")
        assert "X-Magpie-Server-Version" in response.headers
        assert response.headers["X-Magpie-Server-Version"] == __version__

    def test_server_version_header_on_health(self, client: TestClient) -> None:
        """Test that X-Magpie-Server-Version header is in /health responses."""
        response = client.get("/health")
        assert "X-Magpie-Server-Version" in response.headers
        assert response.headers["X-Magpie-Server-Version"] == __version__

    def test_compatible_client_allowed(self, client: TestClient) -> None:
        """Test that compatible client versions are allowed through."""
        response = client.get("/test", headers={"User-Agent": "magpie-cli/0.1.0"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_newer_client_allowed(self, client: TestClient) -> None:
        """Test that newer client versions are allowed through."""
        response = client.get("/test", headers={"User-Agent": "magpie-cli/0.2.0"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_outdated_client_rejected(self, client: TestClient) -> None:
        """Test that outdated client versions get 426 Upgrade Required."""
        response = client.get("/test", headers={"User-Agent": "magpie-cli/0.0.5"})
        assert response.status_code == 426
        assert "X-Magpie-Server-Version" in response.headers
        assert "X-Magpie-Min-Client-Version" in response.headers

        data = response.json()
        assert "error" in data
        assert "Client version too old" in data["error"]
        assert "detail" in data
        assert "pip install --upgrade" in data["detail"]
        assert data["client_version"] == "0.0.5"
        assert data["min_client_version"] == "0.1.0"

    def test_missing_user_agent_allowed(self, client: TestClient) -> None:
        """Test that requests without User-Agent are allowed (don't block non-CLI clients)."""
        response = client.get("/test")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_non_magpie_user_agent_allowed(self, client: TestClient) -> None:
        """Test that non-magpie User-Agents are allowed (browsers, curl, etc.)."""
        response = client.get("/test", headers={"User-Agent": "Mozilla/5.0"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_malformed_version_allowed(self, client: TestClient) -> None:
        """Test that malformed version strings don't block the request."""
        response = client.get("/test", headers={"User-Agent": "magpie-cli/garbage"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_empty_version_string_allowed(self, client: TestClient) -> None:
        """Test that magpie-cli/ with no version doesn't block the request."""
        response = client.get("/test", headers={"User-Agent": "magpie-cli/"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_user_agent_with_additional_info(self, client: TestClient) -> None:
        """Test that User-Agent with additional info is parsed correctly."""
        response = client.get(
            "/test",
            headers={"User-Agent": "magpie-cli/0.1.0 python-httpx/0.27.0"},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_endpoint_exempt_from_version_check(self, client: TestClient) -> None:
        """Test that /health endpoint bypasses version checking even with old client."""
        response = client.get("/health", headers={"User-Agent": "magpie-cli/0.0.1"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": __version__}
        assert "X-Magpie-Server-Version" in response.headers

    def test_custom_min_version_enforcement(self) -> None:
        """Test that custom minimum version can be configured."""
        app = FastAPI()
        app.add_middleware(VersionCheckMiddleware, min_client_version="1.0.0")

        @app.get("/test")
        async def test_endpoint() -> dict:
            return {"status": "ok"}

        client = TestClient(app)

        # Client 0.9.9 should be rejected
        response = client.get("/test", headers={"User-Agent": "magpie-cli/0.9.9"})
        assert response.status_code == 426

        # Client 1.0.0 should be allowed
        response = client.get("/test", headers={"User-Agent": "magpie-cli/1.0.0"})
        assert response.status_code == 200
