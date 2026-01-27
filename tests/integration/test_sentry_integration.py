"""Integration tests for Sentry error tracking."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from magpie.config import MagpieSettings, get_settings
from magpie.server.app import app
from magpie.server.deps import get_storage_service
from magpie.storage.service import StorageService


@pytest.fixture
def test_config_with_sentry(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with Sentry DSN."""
    config = MagpieSettings(
        storage_path=tmp_path,
        database_path=tmp_path / "magpie.db",
        sentry_dsn="https://test@sentry.io/123",
    )
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def test_storage_service_with_sentry(test_config_with_sentry: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing with Sentry."""
    return StorageService(test_config_with_sentry)


@pytest.fixture
def client_with_sentry(
    test_storage_service_with_sentry: StorageService,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, MagicMock]]:
    """Create test client with Sentry-enabled configuration.

    This fixture patches sentry_sdk.init to prevent actual Sentry initialization
    during tests, but allows us to verify that it would be called correctly.

    Yields:
        Tuple of (TestClient, mock_sentry_init) for testing.
    """

    def override_storage_service() -> StorageService:
        return test_storage_service_with_sentry

    app.dependency_overrides[get_storage_service] = override_storage_service

    # Clear the cached settings and set environment variable for Sentry DSN
    # so the lifespan handler picks up the test configuration
    get_settings.cache_clear()
    monkeypatch.setenv("MAGPIE_SENTRY_DSN", "https://test@sentry.io/123")

    # Patch sentry_sdk.init directly since setup_sentry imports sentry_sdk locally
    with patch("sentry_sdk.init") as mock_sentry_init:
        with TestClient(app) as client:
            yield client, mock_sentry_init

    app.dependency_overrides.clear()
    get_settings.cache_clear()


class TestSentryIntegration:
    """Tests for Sentry error tracking integration."""

    def test_sentry_initialized_with_dsn(
        self, client_with_sentry: tuple[TestClient, MagicMock]
    ) -> None:
        """Sentry should be initialized when DSN is configured."""
        _client, mock_sentry_init = client_with_sentry
        # The lifespan handler runs during TestClient initialization
        # Verify that sentry_sdk.init was called
        assert mock_sentry_init.called
        assert mock_sentry_init.call_count == 1

    def test_sentry_config_propagated_correctly(
        self, client_with_sentry: tuple[TestClient, MagicMock]
    ) -> None:
        """Verify Sentry is configured with correct parameters."""
        _client, mock_sentry_init = client_with_sentry

        # Get the call arguments
        call_kwargs = mock_sentry_init.call_args.kwargs

        # Verify DSN was passed
        assert call_kwargs["dsn"] == "https://test@sentry.io/123"

        # Verify integrations include both FastAPI and Starlette
        integrations = call_kwargs["integrations"]
        integration_types = [type(i).__name__ for i in integrations]
        assert (
            "FastApiIntegration" in integration_types
            and "StarletteIntegration" in integration_types
        )

        # Verify environment is set (debug=False by default -> production)
        assert call_kwargs["environment"] == "production"

        # Verify traces sample rate (debug=False -> 0.1)
        assert call_kwargs["traces_sample_rate"] == 0.1

        # Verify PII protection
        assert call_kwargs.get("send_default_pii") is False


@pytest.fixture
def test_config_no_sentry(tmp_path: Path) -> MagpieSettings:
    """Create test configuration without Sentry DSN."""
    config = MagpieSettings(
        storage_path=tmp_path,
        database_path=tmp_path / "magpie.db",
        sentry_dsn=None,
    )
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def test_storage_service_no_sentry(
    test_config_no_sentry: MagpieSettings,
) -> StorageService:
    """Create a StorageService instance for testing without Sentry."""
    return StorageService(test_config_no_sentry)


@pytest.fixture
def client_no_sentry(
    test_storage_service_no_sentry: StorageService,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, MagicMock]]:
    """Create test client without Sentry configuration.

    Yields:
        Tuple of (TestClient, mock_sentry_init) for testing.
    """

    def override_storage_service() -> StorageService:
        return test_storage_service_no_sentry

    app.dependency_overrides[get_storage_service] = override_storage_service

    # Clear cached settings and ensure no SENTRY_DSN is set
    get_settings.cache_clear()
    monkeypatch.delenv("MAGPIE_SENTRY_DSN", raising=False)

    # Patch sentry_sdk.init directly since setup_sentry imports sentry_sdk locally
    with patch("sentry_sdk.init") as mock_sentry_init:
        with TestClient(app) as client:
            yield client, mock_sentry_init

    app.dependency_overrides.clear()
    get_settings.cache_clear()


class TestSentryNotInitializedWithoutDSN:
    """Tests that Sentry is not initialized when DSN is not provided."""

    def test_sentry_not_initialized_without_dsn(
        self, client_no_sentry: tuple[TestClient, MagicMock]
    ) -> None:
        """Sentry should not be initialized when DSN is not configured."""
        _client, mock_sentry_init = client_no_sentry
        # Verify that sentry_sdk.init was NOT called
        assert not mock_sentry_init.called
