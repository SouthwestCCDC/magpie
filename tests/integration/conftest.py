"""Shared fixtures for integration tests.

This module consolidates common test fixtures used across integration tests,
reducing duplication and ensuring consistent test setup.

Note: This module was significantly updated to consolidate fixtures from
individual test files. Generated with assistance from Claude Code (Opus 4.5).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    import httpx

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenInfo, TokenService
from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import (
    clear_token_service_cache,
    get_storage_service,
    get_token_service,
    require_admin_scope,
    require_admin_scope_header,
    require_read_scope,
    require_write_scope,
)
from magpie.storage.service import StorageService

# =============================================================================
# Mock Classes for CLI Download Tests
# =============================================================================


class MockStreamResponse:
    """Mock streaming response for download tests.

    Provides a context manager interface for simulating httpx streaming responses
    in CLI integration tests.
    """

    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self._content = content
        self.headers = {"content-length": str(len(content))}

    def iter_bytes(self):
        """Yield content in chunks."""
        yield self._content

    def read(self) -> bytes:
        """Read full response body."""
        return self._content

    def __enter__(self) -> "MockStreamResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass


class MockClientWithDownload:
    """Wrapper around TestClient that handles download endpoints.

    The /artifacts/{path}/{hash_ref} download path isn't a real API endpoint -
    it's typically served by a static file server. This wrapper intercepts
    those requests and returns the appropriate content.

    Used by CLI integration tests that need to test the full push/get workflow.
    """

    def __init__(self, api_client: TestClient, storage_service: StorageService) -> None:
        self.api_client = api_client
        self.storage_service = storage_service

    def get(self, url: str) -> "MagicMock | httpx.Response":
        """Handle GET requests, routing downloads to storage."""
        if url.startswith("/artifacts/"):
            return self._handle_download(url)
        return self.api_client.get(url)

    def stream(self, method: str, url: str) -> MockStreamResponse:
        """Handle streaming requests for downloads."""
        if method == "GET" and url.startswith("/artifacts/"):
            return self._handle_stream_download(url)
        response = self.api_client.get(url)
        return MockStreamResponse(response.status_code, response.content)

    def post(self, url: str, **kwargs: object) -> "httpx.Response":
        """Delegate POST to api_client."""
        return self.api_client.post(url, **kwargs)

    def delete(self, url: str, **kwargs: object) -> "httpx.Response":
        """Delegate DELETE to api_client."""
        return self.api_client.delete(url, **kwargs)

    def patch(self, url: str, **kwargs: object) -> "httpx.Response":
        """Delegate PATCH to api_client."""
        return self.api_client.patch(url, **kwargs)

    def _get_download_content(self, url: str) -> tuple[int, bytes]:
        """Get download content and status code for a URL."""
        from magpie.storage.blob import read_blob
        from magpie.storage.paths import artifact_dir_path

        parts = url.split("/")

        if "blobs" in parts:
            # Pattern: /artifacts/{path}/blobs/{hash}
            blobs_idx = parts.index("blobs")
            path = "/".join(parts[2:blobs_idx])
            hash_ref = "@" + parts[blobs_idx + 1]
        else:
            # Pattern: /artifacts/{path}/{hash_ref}
            hash_ref = parts[-1]
            path = "/".join(parts[2:-1])

        try:
            info = self.storage_service.get_artifact_info(path, hash_ref)
            artifact_dir = artifact_dir_path(self.storage_service.config.storage_path, path)
            blob_file = read_blob(artifact_dir, info.hash)
            content = blob_file.read_bytes()
            return 200, content
        except Exception:
            return 404, b""

    def _handle_download(self, url: str) -> MagicMock:
        """Handle download from /artifacts/ URL."""
        status_code, content = self._get_download_content(url)
        response = MagicMock()
        response.status_code = status_code
        response.content = content
        return response

    def _handle_stream_download(self, url: str) -> MockStreamResponse:
        """Handle streaming download from /artifacts/ URLs."""
        status_code, content = self._get_download_content(url)
        return MockStreamResponse(status_code, content)

    def __enter__(self) -> "MockClientWithDownload":
        return self

    def __exit__(self, *args: object) -> None:
        pass


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
    config = MagpieSettings(
        storage_path=tmp_path,
        database_path=tmp_path / "magpie.db",
    )
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


@pytest.fixture
def test_token_service(token_service: TokenService) -> TokenService:
    """Alias for token_service for tests using this naming convention."""
    return token_service


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
def client(test_storage_service: StorageService, test_config: MagpieSettings) -> TestClient:
    """Create test client with overridden storage service dependency.

    This is the standard client fixture for most endpoint tests. It overrides
    the storage service dependency to use a test-isolated storage instance.
    Auth dependencies are already overridden by the autouse fixture.
    """

    def override_storage_service() -> StorageService:
        return test_storage_service

    def override_settings() -> MagpieSettings:
        return test_config

    from magpie.server.deps import get_magpie_settings

    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[get_magpie_settings] = override_settings
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_raise(
    test_storage_service: StorageService, test_config: MagpieSettings
) -> TestClient:
    """Create test client that doesn't raise server exceptions.

    Used for tests that need to check HTTP error responses without
    triggering pytest exception handling.
    """

    def override_storage_service() -> StorageService:
        return test_storage_service

    def override_settings() -> MagpieSettings:
        return test_config

    from magpie.server.deps import get_magpie_settings

    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[get_magpie_settings] = override_settings
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def api_client(test_storage_service: StorageService, test_config: MagpieSettings) -> TestClient:
    """Create test API client for CLI integration tests.

    Alias for client fixture, used by CLI tests that mock the HTTP client.
    """

    def override_storage_service() -> StorageService:
        return test_storage_service

    def override_settings() -> MagpieSettings:
        return test_config

    from magpie.server.deps import get_magpie_settings

    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[get_magpie_settings] = override_settings
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def api_client_no_auth_override(
    test_storage_service: StorageService, test_token_service: TokenService
) -> TestClient:
    """Create test API client without auth overrides for authentication tests.

    This fixture sets up storage and token services without the autouse auth
    bypass. Use this for tests that need to verify actual authentication and
    authorization behavior.

    The autouse override_auth_dependencies fixture will detect this fixture
    in request.fixturenames and skip applying auth overrides.
    """

    def override_storage_service() -> StorageService:
        return test_storage_service

    def override_token_service() -> TokenService:
        return test_token_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[get_token_service] = override_token_service
    yield TestClient(app, raise_server_exceptions=False)
    # Clean up only the overrides we added
    app.dependency_overrides.pop(get_storage_service, None)
    app.dependency_overrides.pop(get_token_service, None)


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


def _noop_require_read_scope() -> None:
    """No-op override for require_read_scope in tests.

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

    If a test uses api_client_no_auth_override, this fixture skips applying
    overrides to avoid fixture ordering conflicts.
    """
    # Skip auth overrides if test is using no-auth-override fixture
    if "api_client_no_auth_override" in request.fixturenames:
        yield
        return

    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope_header
    app.dependency_overrides[require_write_scope] = _noop_require_write_scope
    app.dependency_overrides[require_read_scope] = _noop_require_read_scope
    yield
    app.dependency_overrides.pop(require_admin_scope, None)
    app.dependency_overrides.pop(require_admin_scope_header, None)
    app.dependency_overrides.pop(require_write_scope, None)
    app.dependency_overrides.pop(require_read_scope, None)


@pytest.fixture(autouse=True)
def _clear_token_service_cache_fixture():
    """Clear the lru_cache on get_token_service between tests.

    The get_token_service dependency uses @functools.lru_cache(maxsize=1)
    to create a singleton. Without clearing this cache between tests, a
    TokenService instance from one test could leak into another test,
    breaking test isolation.
    """
    clear_token_service_cache()
    yield
    clear_token_service_cache()


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
