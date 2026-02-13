"""Unit tests for test-only memory tracking endpoints.

These tests verify that test endpoints are properly disabled by default
and correctly enforce admin scope requirements when enabled.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenInfo
from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_magpie_settings, require_admin_scope


def _mock_admin_scope() -> TokenInfo:
    """Mock admin scope dependency for tests."""
    return TokenInfo(name="test_admin", scope=TokenScope.ADMIN)


def _mock_read_scope() -> TokenInfo:
    """Mock read scope dependency for tests."""
    return TokenInfo(name="test_user", scope=TokenScope.READ)


class TestMemoryEndpointsDisabled:
    """Tests for test memory endpoints when MAGPIE_ENABLE_TEST_ENDPOINTS=false."""

    @pytest.fixture
    def test_config_disabled(self, tmp_path: Path) -> MagpieSettings:
        """Create test configuration with test endpoints disabled (default)."""
        config = MagpieSettings(
            storage_path=tmp_path,
            database_path=tmp_path / "magpie.db",
            enable_test_endpoints=False,
        )
        config.temp_path.mkdir(parents=True, exist_ok=True)
        return config

    @pytest.fixture
    def client_disabled(self, test_config_disabled: MagpieSettings) -> TestClient:
        """Create test client with test endpoints disabled."""

        def override_settings() -> MagpieSettings:
            return test_config_disabled

        app.dependency_overrides[get_magpie_settings] = override_settings
        app.dependency_overrides[require_admin_scope] = _mock_admin_scope
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_reset_endpoint_returns_403_when_disabled(
        self, client_disabled: TestClient
    ) -> None:
        """POST /api/v1/_test/memory/reset returns 403 when test endpoints disabled."""
        response = client_disabled.post("/api/v1/_test/memory/reset")

        assert response.status_code == 403
        data = response.json()
        assert "Test endpoints are disabled" in data["detail"]

    def test_stats_endpoint_returns_403_when_disabled(
        self, client_disabled: TestClient
    ) -> None:
        """GET /api/v1/_test/memory/stats returns 403 when test endpoints disabled."""
        response = client_disabled.get("/api/v1/_test/memory/stats")

        assert response.status_code == 403
        data = response.json()
        assert "Test endpoints are disabled" in data["detail"]

    def test_stop_endpoint_returns_403_when_disabled(
        self, client_disabled: TestClient
    ) -> None:
        """POST /api/v1/_test/memory/stop returns 403 when test endpoints disabled."""
        response = client_disabled.post("/api/v1/_test/memory/stop")

        assert response.status_code == 403
        data = response.json()
        assert "Test endpoints are disabled" in data["detail"]


class TestMemoryEndpointsEnabled:
    """Tests for test memory endpoints when MAGPIE_ENABLE_TEST_ENDPOINTS=true."""

    @pytest.fixture
    def test_config_enabled(self, tmp_path: Path) -> MagpieSettings:
        """Create test configuration with test endpoints enabled."""
        config = MagpieSettings(
            storage_path=tmp_path,
            database_path=tmp_path / "magpie.db",
            enable_test_endpoints=True,
        )
        config.temp_path.mkdir(parents=True, exist_ok=True)
        return config

    @pytest.fixture
    def client_enabled(self, test_config_enabled: MagpieSettings) -> TestClient:
        """Create test client with test endpoints enabled and admin scope."""

        def override_settings() -> MagpieSettings:
            return test_config_enabled

        app.dependency_overrides[get_magpie_settings] = override_settings
        app.dependency_overrides[require_admin_scope] = _mock_admin_scope
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_reset_endpoint_succeeds_when_enabled(self, client_enabled: TestClient) -> None:
        """POST /api/v1/_test/memory/reset succeeds when test endpoints enabled."""
        response = client_enabled.post("/api/v1/_test/memory/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "reset"
        assert "baseline" in data["message"].lower()

    def test_stop_endpoint_succeeds_when_enabled(self, client_enabled: TestClient) -> None:
        """POST /api/v1/_test/memory/stop succeeds when test endpoints enabled."""
        response = client_enabled.post("/api/v1/_test/memory/stop")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"

    def test_stats_endpoint_returns_400_before_reset(
        self, client_enabled: TestClient
    ) -> None:
        """GET /api/v1/_test/memory/stats returns 400 if reset not called first."""
        # Ensure tracking is stopped
        client_enabled.post("/api/v1/_test/memory/stop")

        response = client_enabled.get("/api/v1/_test/memory/stats")

        assert response.status_code == 400
        data = response.json()
        assert "not initialized" in data["detail"]

    def test_stats_endpoint_succeeds_after_reset(self, client_enabled: TestClient) -> None:
        """GET /api/v1/_test/memory/stats succeeds after calling reset."""
        # Initialize tracking
        reset_response = client_enabled.post("/api/v1/_test/memory/reset")
        assert reset_response.status_code == 200

        # Get stats
        response = client_enabled.get("/api/v1/_test/memory/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["tracking_active"] is True
        assert "peak_delta_bytes" in data
        assert "current_delta_bytes" in data
        assert isinstance(data["peak_delta_bytes"], int)
        assert isinstance(data["current_delta_bytes"], int)

        # Cleanup
        client_enabled.post("/api/v1/_test/memory/stop")
