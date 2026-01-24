"""Security tests for authentication timing (issue #302).

This module tests that authentication is checked BEFORE path existence,
preventing information disclosure through timing or status code differences.

The vulnerability: If path existence is checked before authentication, an
attacker can enumerate valid artifact paths by observing different response
codes (404 for non-existent vs 401 for existing but unauthorized).

The fix: Authentication must be checked first, returning 401 for all
unauthenticated requests regardless of whether the path exists.

AI-assisted: Generated with Claude Code (Sonnet 4.5).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import (
    get_storage_service,
    require_admin_scope,
    require_admin_scope_header,
    require_write_scope,
)
from magpie.storage.service import StorageService


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def test_storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing."""
    return StorageService(test_config)


@pytest.fixture
def client_with_write_auth(test_storage_service: StorageService) -> TestClient:
    """Create test client that overrides ONLY write auth (not read auth).

    This fixture allows us to test read endpoints without authentication
    while still bypassing write auth for setup operations.
    """
    saved_overrides = {
        get_storage_service: app.dependency_overrides.get(get_storage_service),
        require_admin_scope: app.dependency_overrides.get(require_admin_scope),
        require_admin_scope_header: app.dependency_overrides.get(require_admin_scope_header),
        require_write_scope: app.dependency_overrides.get(require_write_scope),
    }

    def override_storage_service() -> StorageService:
        return test_storage_service

    def _noop_require_write_scope() -> None:
        """No-op override for require_write_scope in tests."""

    def _noop_require_admin_scope_header() -> None:
        """No-op override for require_admin_scope_header in tests."""

    # Override storage and write auth, but NOT read auth
    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[require_write_scope] = _noop_require_write_scope
    app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope_header

    yield TestClient(app)

    # Restore previous state
    for dep, saved_value in saved_overrides.items():
        if saved_value is None:
            app.dependency_overrides.pop(dep, None)
        else:
            app.dependency_overrides[dep] = saved_value


class TestAuthenticationBeforePathResolution:
    """Test that authentication is checked before path existence.

    These tests verify the fix for issue #302: all unauthenticated requests
    to protected endpoints must return 401, regardless of whether the
    requested path exists or not.
    """

    def test_get_artifact_info_nonexistent_path_returns_401_not_404(
        self, client_with_write_auth: TestClient
    ) -> None:
        """Unauthenticated request to non-existent path returns 401, not 404.

        Before the fix, this would return 404 because path existence was
        checked before authentication, leaking information about what paths
        exist in storage.
        """
        # Request info for a path that definitely doesn't exist
        # WITHOUT X-Magpie-Scope header (unauthenticated)
        response = client_with_write_auth.get("/api/v1/artifacts/nonexistent/path/latest/info")

        # Should return 401 (unauthenticated), NOT 404 (not found)
        assert response.status_code == 401
        assert "authentication required" in response.text.lower()

    def test_get_artifact_info_existing_path_returns_401(
        self, client_with_write_auth: TestClient
    ) -> None:
        """Unauthenticated request to existing path also returns 401.

        This verifies that the response is consistent regardless of path
        existence, preventing enumeration attacks.
        """
        # First, create an artifact (write auth is bypassed for this setup)
        content = b"test content for auth timing test"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
        upload_response = client_with_write_auth.post(
            "/api/v1/upload/security/authtest",
            files=files,
        )
        assert upload_response.status_code == 200

        # Now request info for the path WITHOUT X-Magpie-Scope header
        response = client_with_write_auth.get("/api/v1/artifacts/security/authtest/latest/info")

        # Should return 401 (unauthenticated), same as non-existent path
        assert response.status_code == 401
        assert "authentication required" in response.text.lower()

    def test_get_artifact_info_with_auth_returns_200_for_existing(
        self, client_with_write_auth: TestClient
    ) -> None:
        """Authenticated request to existing path returns 200.

        This verifies that authentication checking doesn't break normal
        operation for authenticated requests.
        """
        # Create an artifact
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
        upload_response = client_with_write_auth.post(
            "/api/v1/upload/security/authtest2",
            files=files,
        )
        assert upload_response.status_code == 200

        # Request with X-Magpie-Scope header (simulating authenticated request)
        response = client_with_write_auth.get(
            "/api/v1/artifacts/security/authtest2/latest/info",
            headers={"X-Magpie-Scope": "read"},
        )

        # Should return 200 (success)
        assert response.status_code == 200

    def test_get_artifact_info_with_auth_returns_404_for_nonexistent(
        self, client_with_write_auth: TestClient
    ) -> None:
        """Authenticated request to non-existent path returns 404.

        This verifies that 404 is returned ONLY when authenticated, not
        for unauthenticated requests (which should get 401 first).
        """
        # Request non-existent path WITH X-Magpie-Scope header
        response = client_with_write_auth.get(
            "/api/v1/artifacts/nonexistent/path2/latest/info",
            headers={"X-Magpie-Scope": "read"},
        )

        # Should return 404 (not found) for authenticated users
        assert response.status_code == 404

    def test_list_artifacts_nonexistent_path_returns_401_not_empty_list(
        self, client_with_write_auth: TestClient
    ) -> None:
        """Unauthenticated list request returns 401, not empty list.

        Before the fix, listing a non-existent path would return 200 with
        an empty list, while listing an existing path would return different
        content, leaking information about path existence.
        """
        # List a non-existent path WITHOUT authentication
        response = client_with_write_auth.get("/api/v1/artifacts/nonexistent/listtest")

        # Should return 401, not 200 with empty list
        assert response.status_code == 401
        assert "authentication required" in response.text.lower()

    def test_list_artifacts_existing_path_returns_401(
        self, client_with_write_auth: TestClient
    ) -> None:
        """Unauthenticated list request to existing path also returns 401."""
        # Create an artifact
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
        upload_response = client_with_write_auth.post(
            "/api/v1/upload/security/listtest",
            files=files,
        )
        assert upload_response.status_code == 200

        # List WITHOUT authentication
        response = client_with_write_auth.get("/api/v1/artifacts/security/listtest")

        # Should return 401, same as non-existent path
        assert response.status_code == 401
        assert "authentication required" in response.text.lower()

    def test_list_artifact_paths_returns_401_without_auth(
        self, client_with_write_auth: TestClient
    ) -> None:
        """Unauthenticated request to list all paths returns 401.

        The list endpoint could be used to enumerate all artifact paths
        if it allowed unauthenticated access.
        """
        # Request to list all paths WITHOUT authentication
        response = client_with_write_auth.get("/api/v1/artifacts")

        # Should return 401
        assert response.status_code == 401
        assert "authentication required" in response.text.lower()

    def test_list_artifact_paths_with_prefix_returns_401_without_auth(
        self, client_with_write_auth: TestClient
    ) -> None:
        """Unauthenticated request to list paths with prefix returns 401."""
        # Request with prefix WITHOUT authentication
        response = client_with_write_auth.get(
            "/api/v1/artifacts",
            params={"prefix": "security"},
        )

        # Should return 401
        assert response.status_code == 401
        assert "authentication required" in response.text.lower()


class TestAuthenticationConsistency:
    """Test that authentication behavior is consistent across endpoints.

    These tests verify that all read endpoints enforce authentication
    consistently, preventing any endpoint from being an information
    disclosure vector.
    """

    def test_all_read_endpoints_require_auth(self, client_with_write_auth: TestClient) -> None:
        """All GET endpoints under /api/v1/artifacts require authentication.

        This test verifies that every read endpoint returns 401 for
        unauthenticated requests, preventing any path from being used
        to enumerate artifacts.
        """
        read_endpoints = [
            "/api/v1/artifacts",
            "/api/v1/artifacts/test/path",
            "/api/v1/artifacts/test/path/latest/info",
        ]

        for endpoint in read_endpoints:
            response = client_with_write_auth.get(endpoint)
            assert response.status_code == 401, (
                f"Endpoint {endpoint} should return 401 for unauthenticated requests, "
                f"got {response.status_code}"
            )
            assert "authentication required" in response.text.lower()

    def test_authenticated_requests_work_normally(self, client_with_write_auth: TestClient) -> None:
        """Authenticated requests to all endpoints work normally.

        This verifies that adding authentication checks doesn't break
        normal operation for authenticated users.
        """
        # Create a test artifact
        content = b"consistency test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
        upload_response = client_with_write_auth.post(
            "/api/v1/upload/security/consistency",
            files=files,
        )
        assert upload_response.status_code == 200

        # Test all read endpoints with authentication
        auth_headers = {"X-Magpie-Scope": "read"}

        # List all paths
        response = client_with_write_auth.get(
            "/api/v1/artifacts",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "security/consistency" in data["paths"]

        # List versions
        response = client_with_write_auth.get(
            "/api/v1/artifacts/security/consistency",
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Get info
        response = client_with_write_auth.get(
            "/api/v1/artifacts/security/consistency/latest/info",
            headers=auth_headers,
        )
        assert response.status_code == 200
