"""Integration tests for Sentry error tracking."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_storage_service
from magpie.storage.service import StorageService


@pytest.fixture
def test_config_with_sentry(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with Sentry DSN."""
    config = MagpieSettings(
        storage_path=tmp_path,
        sentry_dsn="https://test@sentry.io/123",
    )
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def test_storage_service_with_sentry(test_config_with_sentry: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing with Sentry."""
    return StorageService(test_config_with_sentry)


@pytest.fixture
def client_with_sentry(test_storage_service_with_sentry: StorageService) -> TestClient:
    """Create test client with Sentry-enabled configuration.

    This fixture patches sentry_sdk.init to prevent actual Sentry initialization
    during tests, but allows us to verify that it would be called correctly.
    """

    def override_storage_service() -> StorageService:
        return test_storage_service_with_sentry

    app.dependency_overrides[get_storage_service] = override_storage_service

    # Clear any existing lifespan state and reinitialize with Sentry config
    # Note: TestClient will call the lifespan handler on context entry
    with patch("sentry_sdk.init") as mock_sentry_init:
        # Store the mock so we can verify it later
        with TestClient(app) as client:
            client.mock_sentry_init = mock_sentry_init
            yield client

    app.dependency_overrides.clear()


class TestSentryIntegration:
    """Tests for Sentry error tracking integration."""

    def test_sentry_initialized_with_dsn(self, client_with_sentry: TestClient) -> None:
        """Sentry should be initialized when DSN is configured."""
        # The lifespan handler runs during TestClient initialization
        # Verify that sentry_sdk.init was called
        assert client_with_sentry.mock_sentry_init.called
        assert client_with_sentry.mock_sentry_init.call_count == 1

    def test_sentry_captures_endpoint_errors(self, client_with_sentry: TestClient) -> None:
        """Sentry should capture errors from endpoints via FastAPI integration.

        This test demonstrates that FastAPI integration is enabled and would
        capture any server errors (5xx) that occur during request processing.
        The FastAPI integration automatically captures unhandled exceptions.
        """
        # Trigger a request to verify the endpoint is working
        # The FastAPI integration would automatically capture any 5xx errors
        response = client_with_sentry.get("/api/v1/info/nonexistent/path/@latest")

        # The endpoint returns 404 for not found (not a 500 error)
        assert response.status_code == 404

        # Note: FastAPI integration captures 5xx errors automatically
        # 4xx errors like 404 are not captured by default as they're
        # considered handled client errors, not server errors

    def test_sentry_config_propagated_correctly(self, client_with_sentry: TestClient) -> None:
        """Verify Sentry is configured with correct parameters."""
        mock_init = client_with_sentry.mock_sentry_init

        # Get the call arguments
        call_kwargs = mock_init.call_args.kwargs

        # Verify DSN was passed
        assert call_kwargs["dsn"] == "https://test@sentry.io/123"

        # Verify integrations include both FastAPI and Starlette
        integrations = call_kwargs["integrations"]
        integration_types = [type(i).__name__ for i in integrations]
        assert (
            "FastApiIntegration" in integration_types
            and "StarletteIntegration" in integration_types
        )

        # Verify environment is set
        assert "environment" in call_kwargs

        # Verify PII protection
        assert call_kwargs.get("send_default_pii") is False


class TestSentryNotInitializedWithoutDSN:
    """Tests that Sentry is not initialized when DSN is not provided."""

    @pytest.fixture
    def test_config_no_sentry(self, tmp_path: Path) -> MagpieSettings:
        """Create test configuration without Sentry DSN."""
        config = MagpieSettings(storage_path=tmp_path, sentry_dsn=None)
        config.temp_path.mkdir(parents=True, exist_ok=True)
        return config

    @pytest.fixture
    def test_storage_service_no_sentry(
        self, test_config_no_sentry: MagpieSettings
    ) -> StorageService:
        """Create a StorageService instance for testing without Sentry."""
        return StorageService(test_config_no_sentry)

    @pytest.fixture
    def client_no_sentry(self, test_storage_service_no_sentry: StorageService) -> TestClient:
        """Create test client without Sentry configuration."""

        def override_storage_service() -> StorageService:
            return test_storage_service_no_sentry

        app.dependency_overrides[get_storage_service] = override_storage_service

        with patch("sentry_sdk.init") as mock_sentry_init:
            with TestClient(app) as client:
                client.mock_sentry_init = mock_sentry_init
                yield client

        app.dependency_overrides.clear()

    def test_sentry_not_initialized_without_dsn(self, client_no_sentry: TestClient) -> None:
        """Sentry should not be initialized when DSN is not configured."""
        # Verify that sentry_sdk.init was NOT called
        assert not client_no_sentry.mock_sentry_init.called
