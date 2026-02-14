"""Tests for version checking middleware."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from magpie.server.middleware import VersionCheckMiddleware, get_min_client_version


class TestGetMinClientVersion:
    """Tests for get_min_client_version helper function."""

    def test_standard_version(self) -> None:
        """Test that standard versions return minor floor."""
        assert get_min_client_version("0.1.3") == "0.1.0"
        assert get_min_client_version("1.2.5") == "1.2.0"
        assert get_min_client_version("2.0.1") == "2.0.0"

    def test_dev_version_preserves_dev_suffix(self) -> None:
        """Test that dev versions preserve .dev0 suffix for correct PEP 440 ordering."""
        # PEP 440: 0.0.0.dev0 < 0.0.0, so we need to preserve dev suffix
        assert get_min_client_version("0.0.0-dev") == "0.0.0.dev0"
        assert get_min_client_version("0.1.2-dev") == "0.1.0.dev0"

    def test_pre_release_version_preserves_suffix(self) -> None:
        """Test that pre-release versions preserve their suffix for correct PEP 440 ordering."""
        # Alpha releases
        assert get_min_client_version("0.1.0a1") == "0.1.0a1"
        assert get_min_client_version("0.1.5a3") == "0.1.0a3"
        # Beta releases
        assert get_min_client_version("0.2.0b1") == "0.2.0b1"
        assert get_min_client_version("0.2.3b2") == "0.2.0b2"
        # RC releases
        assert get_min_client_version("1.0.0rc1") == "1.0.0rc1"
        assert get_min_client_version("1.0.2rc3") == "1.0.0rc3"


@pytest.fixture
def app_with_version_middleware(monkeypatch) -> FastAPI:
    """Create a test FastAPI app with VersionCheckMiddleware and fixed server version.

    Monkeypatches the server version to "1.2.3" for deterministic testing of
    version comparison logic.
    """
    # Fix server version before middleware initialization
    import magpie.server.middleware as mw_module
    monkeypatch.setattr(mw_module, "__version__", "1.2.3")

    app = FastAPI()
    app.add_middleware(VersionCheckMiddleware, min_client_version="1.2.0")

    @app.get("/test")
    async def test_endpoint() -> dict:
        return {"status": "ok"}

    @app.get("/health")
    async def health_endpoint() -> dict:
        return {"status": "ok", "version": "1.2.3"}

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
        assert response.headers["X-Magpie-Server-Version"] == "1.2.3"

    def test_server_version_header_on_health(self, client: TestClient) -> None:
        """Test that X-Magpie-Server-Version header is in /health responses."""
        response = client.get("/health")
        assert "X-Magpie-Server-Version" in response.headers
        assert response.headers["X-Magpie-Server-Version"] == "1.2.3"

    def test_compatible_client_allowed(self, client: TestClient) -> None:
        """Test that compatible client versions are allowed through."""
        response = client.get("/test", headers={"User-Agent": "magpie-cli/1.2.0"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_newer_client_allowed(self, client: TestClient) -> None:
        """Test that newer client versions are allowed through."""
        response = client.get("/test", headers={"User-Agent": "magpie-cli/1.3.0"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_outdated_client_rejected(self, client: TestClient) -> None:
        """Test that outdated client versions get 426 Upgrade Required."""
        response = client.get("/test", headers={"User-Agent": "magpie-cli/1.1.9"})
        assert response.status_code == 426
        assert "X-Magpie-Server-Version" in response.headers
        assert "X-Magpie-Min-Client-Version" in response.headers

        data = response.json()
        assert "error" in data
        assert "Client version too old" in data["error"]
        assert "detail" in data
        assert "pip install --upgrade" in data["detail"]
        assert data["client_version"] == "1.1.9"
        assert data["min_client_version"] == "1.2.0"

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
            headers={"User-Agent": "magpie-cli/1.2.0 python-httpx/0.27.0"},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_endpoint_exempt_from_version_check(self, client: TestClient) -> None:
        """Test that /health endpoint bypasses version checking even with old client."""
        response = client.get("/health", headers={"User-Agent": "magpie-cli/1.0.0"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": "1.2.3"}
        assert "X-Magpie-Server-Version" in response.headers

    def test_health_endpoint_with_trailing_slash_exempt(self, client: TestClient) -> None:
        """Test that /health/ (with trailing slash) bypasses version checking."""
        response = client.get("/health/", headers={"User-Agent": "magpie-cli/1.0.0"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": "1.2.3"}
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

    def test_invalid_min_version_raises_at_init(self) -> None:
        """Test that invalid min_client_version raises ValueError at initialization."""
        app = FastAPI()

        # Direct instantiation should raise ValueError for invalid version
        with pytest.raises(ValueError, match="Failed to parse min_client_version"):
            VersionCheckMiddleware(app, min_client_version="not-a-version")

    def test_dev_server_allows_dev_client(self) -> None:
        """Test that dev server (0.0.0-dev) allows dev client (0.0.0-dev)."""
        app = FastAPI()
        # Simulate dev server with min_client_version="0.0.0.dev0"
        app.add_middleware(VersionCheckMiddleware, min_client_version="0.0.0.dev0")

        @app.get("/test")
        async def test_endpoint() -> dict:
            return {"status": "ok"}

        client = TestClient(app)

        # Dev client should be allowed
        response = client.get("/test", headers={"User-Agent": "magpie-cli/0.0.0-dev"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_dev_server_allows_release_client(self) -> None:
        """Test that dev server allows release client (0.1.0)."""
        app = FastAPI()
        # Simulate dev server with min_client_version="0.0.0.dev0"
        app.add_middleware(VersionCheckMiddleware, min_client_version="0.0.0.dev0")

        @app.get("/test")
        async def test_endpoint() -> dict:
            return {"status": "ok"}

        client = TestClient(app)

        # Release client 0.1.0 should be allowed (0.1.0 > 0.0.0.dev0)
        response = client.get("/test", headers={"User-Agent": "magpie-cli/0.1.0"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_pre_release_server_allows_pre_release_client(self) -> None:
        """Test that pre-release server allows matching pre-release client."""
        app = FastAPI()
        # Simulate alpha server with min_client_version="0.1.0a1"
        app.add_middleware(VersionCheckMiddleware, min_client_version="0.1.0a1")

        @app.get("/test")
        async def test_endpoint() -> dict:
            return {"status": "ok"}

        client = TestClient(app)

        # Alpha client should be allowed
        response = client.get("/test", headers={"User-Agent": "magpie-cli/0.1.0a1"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        # Newer alpha should be allowed
        response = client.get("/test", headers={"User-Agent": "magpie-cli/0.1.0a2"})
        assert response.status_code == 200

        # Beta should be allowed (beta > alpha)
        response = client.get("/test", headers={"User-Agent": "magpie-cli/0.1.0b1"})
        assert response.status_code == 200

    def test_pre_release_server_rejects_older_client(self) -> None:
        """Test that pre-release server rejects older dev client."""
        app = FastAPI()
        # Simulate alpha server with min_client_version="0.1.0a1"
        app.add_middleware(VersionCheckMiddleware, min_client_version="0.1.0a1")

        @app.get("/test")
        async def test_endpoint() -> dict:
            return {"status": "ok"}

        client = TestClient(app)

        # Dev client should be rejected (0.0.0.dev0 < 0.1.0a1)
        response = client.get("/test", headers={"User-Agent": "magpie-cli/0.0.0-dev"})
        assert response.status_code == 426

    def test_upgrade_available_header_for_outdated_compatible_client(
        self, client: TestClient
    ) -> None:
        """Test that X-Magpie-Upgrade-Available header is added for compatible but outdated clients."""
        # Server is 1.2.3, min is 1.2.0
        # Client 1.2.0 is compatible but outdated
        response = client.get("/test", headers={"User-Agent": "magpie-cli/1.2.0"})
        assert response.status_code == 200
        assert "X-Magpie-Upgrade-Available" in response.headers
        assert response.headers["X-Magpie-Upgrade-Available"] == "1.2.3"

    def test_no_upgrade_header_for_current_client(self, client: TestClient) -> None:
        """Test that X-Magpie-Upgrade-Available header is NOT added when client matches server."""
        # Client version matches server version (1.2.3)
        response = client.get("/test", headers={"User-Agent": "magpie-cli/1.2.3"})
        assert response.status_code == 200
        assert "X-Magpie-Upgrade-Available" not in response.headers

    def test_no_upgrade_header_for_newer_client(self, client: TestClient) -> None:
        """Test that X-Magpie-Upgrade-Available header is NOT added for newer clients."""
        # Client version is newer than server (future-proof scenario)
        response = client.get("/test", headers={"User-Agent": "magpie-cli/99.99.99"})
        assert response.status_code == 200
        assert "X-Magpie-Upgrade-Available" not in response.headers

    def test_no_upgrade_header_for_non_magpie_user_agent(self, client: TestClient) -> None:
        """Test that X-Magpie-Upgrade-Available header is NOT added for non-magpie clients."""
        response = client.get("/test", headers={"User-Agent": "Mozilla/5.0"})
        assert response.status_code == 200
        assert "X-Magpie-Upgrade-Available" not in response.headers

    def test_no_upgrade_header_for_missing_user_agent(self, client: TestClient) -> None:
        """Test that X-Magpie-Upgrade-Available header is NOT added when User-Agent is missing."""
        response = client.get("/test")
        assert response.status_code == 200
        assert "X-Magpie-Upgrade-Available" not in response.headers

    def test_upgrade_available_header_for_patch_version_behind(self, client: TestClient) -> None:
        """Test upgrade header when client is one patch version behind."""
        # Server is 1.2.3, client is 1.2.2
        response = client.get("/test", headers={"User-Agent": "magpie-cli/1.2.2"})
        assert response.status_code == 200
        assert "X-Magpie-Upgrade-Available" in response.headers
        assert response.headers["X-Magpie-Upgrade-Available"] == "1.2.3"

    def test_no_upgrade_header_on_426_response(self, client: TestClient) -> None:
        """Test that X-Magpie-Upgrade-Available header is NOT added on 426 responses."""
        # Incompatible client should get 426, not upgrade-available header
        response = client.get("/test", headers={"User-Agent": "magpie-cli/1.1.9"})
        assert response.status_code == 426
        assert "X-Magpie-Upgrade-Available" not in response.headers

    def test_no_upgrade_header_on_health_endpoint(self, client: TestClient) -> None:
        """Test that X-Magpie-Upgrade-Available header is NOT added on /health endpoint."""
        # Even though client is outdated, /health should not add upgrade header
        response = client.get("/health", headers={"User-Agent": "magpie-cli/1.2.0"})
        assert response.status_code == 200
        assert "X-Magpie-Upgrade-Available" not in response.headers

    def test_upgrade_header_with_dev_versions(self, monkeypatch) -> None:
        """Test upgrade header with dev versions.

        Dev client (1.2.3.dev0) talking to stable server (1.2.3) IS outdated per PEP 440,
        so should get the upgrade header.
        """
        # Monkeypatch server version to stable 1.2.3
        import magpie.server.middleware as mw_module
        monkeypatch.setattr(mw_module, "__version__", "1.2.3")

        app = FastAPI()
        app.add_middleware(VersionCheckMiddleware, min_client_version="1.2.0")

        @app.get("/test")
        async def test_endpoint() -> dict:
            return {"status": "ok"}

        client = TestClient(app)

        # Dev client (1.2.3.dev0) < stable server (1.2.3) per PEP 440
        response = client.get("/test", headers={"User-Agent": "magpie-cli/1.2.3-dev"})
        assert response.status_code == 200
        assert "X-Magpie-Upgrade-Available" in response.headers
        assert response.headers["X-Magpie-Upgrade-Available"] == "1.2.3"

    def test_upgrade_header_with_pre_release_versions(self, monkeypatch) -> None:
        """Test upgrade header with pre-release versions.

        Pre-release client (1.2.3a1) talking to stable server (1.2.3) IS outdated per PEP 440,
        so should get the upgrade header.
        """
        # Monkeypatch server version to stable 1.2.3
        import magpie.server.middleware as mw_module
        monkeypatch.setattr(mw_module, "__version__", "1.2.3")

        app = FastAPI()
        app.add_middleware(VersionCheckMiddleware, min_client_version="1.2.0")

        @app.get("/test")
        async def test_endpoint() -> dict:
            return {"status": "ok"}

        client = TestClient(app)

        # Alpha client (1.2.3a1) < stable server (1.2.3) per PEP 440
        response = client.get("/test", headers={"User-Agent": "magpie-cli/1.2.3a1"})
        assert response.status_code == 200
        assert "X-Magpie-Upgrade-Available" in response.headers
        assert response.headers["X-Magpie-Upgrade-Available"] == "1.2.3"
