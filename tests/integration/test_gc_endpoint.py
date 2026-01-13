"""Integration tests for the GC endpoint."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from magpie.auth.service import TokenService
from magpie.config import MagpieSettings, get_settings
from magpie.server.app import app
from magpie.server.deps import get_storage_service, get_token_service
from magpie.storage.service import StorageService


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with retention_days for GC tests."""
    config = MagpieSettings(storage_path=tmp_path, retention_days=90)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def client(
    token_service: TokenService, storage_service: StorageService, test_config: MagpieSettings
) -> TestClient:
    """Create test client with overridden dependencies.

    Note: This fixture removes auth overrides from the autouse conftest fixture
    so that actual token authentication is tested.
    """
    # Clear any auth overrides from the autouse conftest fixture
    # so that real authentication is tested
    app.dependency_overrides.clear()

    def override_token_service() -> TokenService:
        return token_service

    def override_storage_service() -> StorageService:
        return storage_service

    def override_settings() -> MagpieSettings:
        return test_config

    app.dependency_overrides[get_token_service] = override_token_service
    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[get_settings] = override_settings
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _upload_artifact(
    client: TestClient,
    path: str,
    content: bytes,
    uploaded_by: str = "test-user",
    scope: str = "write",
) -> dict:
    """Helper to upload an artifact and return the response data.

    Uses X-Magpie-Scope header to simulate Caddy forward_auth for scope checking.
    """
    files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
    headers = {"X-Magpie-Scope": scope}

    response = client.post(
        f"/api/v1/upload/{path}",
        files=files,
        params={"uploaded_by": uploaded_by},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


class TestGCEndpointAuth:
    """Tests for GC endpoint authentication and authorization."""

    def test_gc_requires_admin_scope(
        self, client: TestClient, admin_token: str, test_config: MagpieSettings
    ) -> None:
        """GC endpoint requires admin scope."""
        # Create storage so subprocess would be called
        test_config.storage_path.mkdir(parents=True, exist_ok=True)

        mock_result = {
            "dry_run": False,
            "blobs_removed": 0,
            "bytes_reclaimed": 0,
            "errors": [],
        }

        with patch(
            "magpie.server.routes.gc.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client.post(
                "/api/v1/gc",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200

    def test_gc_with_read_scope_returns_403(self, client: TestClient, read_token: str) -> None:
        """GC with read scope returns 403."""
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {read_token}"},
        )

        assert response.status_code == 403
        assert "Admin scope required" in response.json()["detail"]

    def test_gc_with_write_scope_returns_403(self, client: TestClient, write_token: str) -> None:
        """GC with write scope returns 403."""
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {write_token}"},
        )

        assert response.status_code == 403

    def test_gc_without_auth_returns_401(self, client: TestClient) -> None:
        """GC without authorization returns 401."""
        response = client.post("/api/v1/gc")

        assert response.status_code == 401


class TestGCEndpointFunctionality:
    """Tests for GC endpoint functionality.

    Note: The GC endpoint now shells out to magpie-ctl, so we mock the subprocess
    call to test endpoint behavior without requiring the CLI to be installed.
    """

    def test_gc_dry_run_returns_preview(self, client: TestClient, admin_token: str) -> None:
        """GC with dry_run returns preview without modification."""
        mock_result = {
            "dry_run": True,
            "artifacts_scanned": 5,
            "blobs_found": 3,
            "blobs_deleted": 0,
            "space_reclaimed_bytes": 0,
            "symlinks_checked": 2,
            "symlinks_fixed": 0,
            "items_removed": 0,
            "errors": [],
        }

        with patch(
            "magpie.server.routes.gc.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client.post(
                "/api/v1/gc",
                headers={"Authorization": f"Bearer {admin_token}"},
                params={"dry_run": "true"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        assert "blobs_deleted" in data
        assert "space_reclaimed_bytes" in data

    def test_gc_returns_zero_for_tagged_blobs(self, client: TestClient, admin_token: str) -> None:
        """GC returns zero removed blobs when all blobs are tagged."""
        mock_result = {
            "dry_run": False,
            "artifacts_scanned": 5,
            "blobs_found": 3,
            "blobs_deleted": 0,
            "space_reclaimed_bytes": 0,
            "symlinks_checked": 2,
            "symlinks_fixed": 0,
            "items_removed": 0,
            "errors": [],
        }

        with patch(
            "magpie.server.routes.gc.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client.post(
                "/api/v1/gc",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["blobs_deleted"] == 0

    def test_gc_response_format(self, client: TestClient, admin_token: str) -> None:
        """GC response includes all expected fields."""
        mock_result = {
            "dry_run": False,
            "artifacts_scanned": 5,
            "blobs_found": 3,
            "blobs_deleted": 5,
            "space_reclaimed_bytes": 1024,
            "symlinks_checked": 2,
            "symlinks_fixed": 0,
            "items_removed": 0,
            "errors": [],
        }

        with patch(
            "magpie.server.routes.gc.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client.post(
                "/api/v1/gc",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()

        required_fields = {
            "dry_run",
            "artifacts_scanned",
            "blobs_found",
            "blobs_deleted",
            "space_reclaimed_bytes",
            "symlinks_checked",
            "symlinks_fixed",
            "items_removed",
            "errors",
        }
        assert required_fields == set(data.keys())

    def test_gc_empty_storage(
        self, client: TestClient, admin_token: str, test_config: MagpieSettings
    ) -> None:
        """GC on empty storage returns zero counts."""
        # Ensure storage path exists but is empty
        test_config.storage_path.mkdir(parents=True, exist_ok=True)

        mock_result = {
            "dry_run": False,
            "artifacts_scanned": 0,
            "blobs_found": 0,
            "blobs_deleted": 0,
            "space_reclaimed_bytes": 0,
            "symlinks_checked": 0,
            "symlinks_fixed": 0,
            "items_removed": 0,
            "errors": [],
        }

        with patch(
            "magpie.server.routes.gc.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client.post(
                "/api/v1/gc",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["blobs_deleted"] == 0
        assert data["space_reclaimed_bytes"] == 0

    def test_gc_reports_blobs_removed(self, client: TestClient, admin_token: str) -> None:
        """GC reports number of blobs removed."""
        mock_result = {
            "dry_run": False,
            "artifacts_scanned": 5,
            "blobs_found": 15,
            "blobs_deleted": 10,
            "space_reclaimed_bytes": 5120,
            "symlinks_checked": 2,
            "symlinks_fixed": 0,
            "items_removed": 0,
            "errors": [],
        }

        with patch(
            "magpie.server.routes.gc.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client.post(
                "/api/v1/gc",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["blobs_deleted"] == 10
        assert data["space_reclaimed_bytes"] == 5120


class TestGCSubprocessIntegration:
    """Tests for GC endpoint subprocess error handling."""

    def test_gc_subprocess_error_returns_500(
        self, client: TestClient, admin_token: str, test_config: MagpieSettings
    ) -> None:
        """GC subprocess failure returns 500 error."""
        # Create storage path so subprocess is called
        test_config.storage_path.mkdir(parents=True, exist_ok=True)

        from magpie.server.subprocess_utils import CtlCommandError

        with patch(
            "magpie.server.routes.gc.run_ctl_command",
            new=AsyncMock(side_effect=CtlCommandError("Command failed")),
        ):
            response = client.post(
                "/api/v1/gc",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 500
        # Error message should be sanitized (not expose internal details)
        assert response.json()["detail"] == "Garbage collection failed"

    def test_gc_passes_retention_days_to_subprocess(
        self, client: TestClient, admin_token: str, test_config: MagpieSettings
    ) -> None:
        """GC passes retention_days parameter to subprocess command."""
        test_config.storage_path.mkdir(parents=True, exist_ok=True)

        mock_result = {
            "dry_run": False,
            "blobs_removed": 0,
            "bytes_reclaimed": 0,
            "errors": [],
        }

        mock_run = AsyncMock(return_value=mock_result)

        with patch("magpie.server.routes.gc.run_ctl_command", new=mock_run):
            response = client.post(
                "/api/v1/gc",
                headers={"Authorization": f"Bearer {admin_token}"},
                params={"retention_days": 7},
            )

        assert response.status_code == 200
        # Verify command includes retention-days flag
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--retention-days" in cmd
        assert "7" in cmd
