"""Shared fixtures for integration tests.

This module consolidates common test fixtures used across integration tests,
reducing duplication and ensuring consistent test setup.

Note: This module was significantly updated to consolidate fixtures from
individual test files. Generated with assistance from Claude Code (Opus 4.5).
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenInfo, TokenService
from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import (
    get_storage_service,
    require_admin_scope,
    require_admin_scope_header,
    require_write_scope,
)
from magpie.storage.service import StorageService

# =============================================================================
# Core Configuration Fixtures
# =============================================================================


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths.

    This is the base configuration fixture used by most integration tests.
    Creates a MagpieSettings instance with storage_path set to a temporary
    directory, and ensures the temp_path subdirectory exists.
    """
    config = MagpieSettings(storage_path=tmp_path)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


# =============================================================================
# Service Fixtures
# =============================================================================


@pytest.fixture
def test_storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing.

    Uses the test_config fixture to ensure storage is isolated per test.
    """
    return StorageService(test_config)


@pytest.fixture
def storage_service(test_config: MagpieSettings) -> StorageService:
    """Alias for test_storage_service for tests using this naming convention."""
    return StorageService(test_config)


@pytest.fixture
def token_service(test_config: MagpieSettings) -> TokenService:
    """Create a TokenService instance for testing."""
    return TokenService(test_config)


# =============================================================================
# Token Fixtures
# =============================================================================


@pytest.fixture
def admin_token(token_service: TokenService) -> str:
    """Create an admin token for authentication."""
    return token_service.create_token("test-admin", TokenScope.ADMIN)


@pytest.fixture
def read_token(token_service: TokenService) -> str:
    """Create a read-only token for testing non-admin access."""
    return token_service.create_token("test-reader", TokenScope.READ)


@pytest.fixture
def write_token(token_service: TokenService) -> str:
    """Create a write token for testing non-admin access."""
    return token_service.create_token("test-writer", TokenScope.WRITE)


# =============================================================================
# TestClient Fixtures
# =============================================================================


@pytest.fixture
def client(test_storage_service: StorageService) -> TestClient:
    """Create test client with overridden storage service dependency.

    This is the standard client fixture for most endpoint tests. It overrides
    the storage service dependency to use a test-isolated storage instance.
    Auth dependencies are already overridden by the autouse fixture.
    """

    def override_storage_service() -> StorageService:
        return test_storage_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_raise(test_storage_service: StorageService) -> TestClient:
    """Create test client that doesn't raise server exceptions.

    Used for tests that need to check HTTP error responses without
    triggering pytest exception handling.
    """

    def override_storage_service() -> StorageService:
        return test_storage_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def api_client(test_storage_service: StorageService) -> TestClient:
    """Create test API client for CLI integration tests.

    Alias for client fixture, used by CLI tests that mock the HTTP client.
    """

    def override_storage_service() -> StorageService:
        return test_storage_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    yield TestClient(app)
    app.dependency_overrides.clear()


# =============================================================================
# CLI Fixtures
# =============================================================================


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def cli_runner_no_config() -> CliRunner:
    """Create CLI runner that simulates no server/token configuration.

    This fixture patches get_server and get_token to return empty strings
    when no CLI override is provided, simulating an environment where no
    config file exists and no environment variables are set. Use this for
    tests that verify "No server configured" or "No token configured" error
    handling.

    The patches respect CLI overrides (--server, --token) so tests can verify
    that a missing token fails even when server is provided via CLI.
    """
    runner = CliRunner()

    # Store original invoke method
    original_invoke = runner.invoke

    def mock_get_server(cli_override=None, config_path=None):
        """Return CLI override if provided, otherwise empty string."""
        return cli_override if cli_override else ""

    def mock_get_token(cli_override=None, config_path=None):
        """Return CLI override if provided, otherwise empty string."""
        return cli_override if cli_override else ""

    def patched_invoke(*args, **kwargs):
        # Patch where the functions are used (magpie.cli module), not where defined
        with patch("magpie.cli.get_server", side_effect=mock_get_server):
            with patch("magpie.cli.get_token", side_effect=mock_get_token):
                return original_invoke(*args, **kwargs)

    runner.invoke = patched_invoke
    return runner


# =============================================================================
# Auth Override Helpers (for autouse fixture)
# =============================================================================


def _noop_require_admin_scope() -> TokenInfo:
    """No-op override for require_admin_scope in tests."""
    return TokenInfo(
        name="test-token",
        scope=TokenScope.ADMIN,
    )


def _noop_require_write_scope() -> None:
    """No-op override for require_write_scope in tests.

    Returns None as this dependency only validates scope but does not
    return token info.
    """
    return None


def _noop_require_admin_scope_header() -> None:
    """No-op override for require_admin_scope_header in tests.

    Returns None as this dependency only validates scope but does not
    return token info.
    """
    return None


@pytest.fixture(autouse=True)
def override_auth_dependencies(request):
    """Override auth dependencies for integration tests.

    Integration tests run against the FastAPI app directly without Caddy,
    so there are no Authorization headers. This fixture disables auth
    checking for all integration tests.

    Test modules that define their own token fixtures (admin_token, read_token,
    write_token) are testing authentication behavior and are skipped.
    """
    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope_header
    app.dependency_overrides[require_write_scope] = _noop_require_write_scope
    yield
    app.dependency_overrides.pop(require_admin_scope, None)
    app.dependency_overrides.pop(require_admin_scope_header, None)
    app.dependency_overrides.pop(require_write_scope, None)


# =============================================================================
# Helper Functions (not fixtures, but commonly used across tests)
# =============================================================================


def upload_test_artifact(
    client: TestClient,
    path: str,
    content: bytes,
    source_uri: str | None = None,
    uploaded_by: str = "test-user",
) -> dict:
    """Helper to upload a test artifact and return response data.

    This is a utility function (not a fixture) that can be imported by test
    modules that need to upload artifacts as part of their test setup.

    Args:
        client: TestClient instance to use for the upload
        path: Artifact path (e.g., "test/artifact")
        content: Binary content to upload
        source_uri: Optional source URI for provenance
        uploaded_by: Uploader identifier (defaults to "test-user")

    Returns:
        dict: JSON response from the upload endpoint containing hash, hash_ref, etc.
    """
    files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
    params = {"uploaded_by": uploaded_by}
    if source_uri:
        params["source_uri"] = source_uri

    response = client.post(
        f"/api/v1/upload/{path}",
        files=files,
        params=params,
    )
    assert response.status_code == 200
    return response.json()
