"""Integration tests for CLI gc command."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

if TYPE_CHECKING:
    import httpx
from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenService
from magpie.cli import cli
from magpie.config import MagpieSettings, get_settings
from magpie.server.app import app
from magpie.server.deps import get_storage_service, get_token_service
from magpie.storage.service import StorageService

# Patch path for get_client - must match where it's imported/used in the CLI module
PATCH_GET_CLIENT = "magpie.cli.get_client"

# Patch path for subprocess in server GC route
PATCH_RUN_CTL = "magpie.server.routes.gc.run_ctl_command"


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path, retention_days=90)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def token_service(test_config: MagpieSettings) -> TokenService:
    """Create a TokenService instance for testing."""
    return TokenService(test_config)


@pytest.fixture
def storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing."""
    return StorageService(test_config)


@pytest.fixture
def admin_token(token_service: TokenService) -> str:
    """Create an admin token for authentication."""
    return token_service.create_token("test-admin", TokenScope.ADMIN)


@pytest.fixture
def api_client(
    token_service: TokenService, storage_service: StorageService, test_config: MagpieSettings
) -> TestClient:
    """Create test API client with overridden dependencies.

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


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create Click CLI test runner."""
    return CliRunner()


class TestGCCommand:
    """Integration tests for gc command."""

    def test_gc_requires_server(self, cli_runner_no_config: CliRunner) -> None:
        """GC without server configured fails with error."""
        result = cli_runner_no_config.invoke(cli, ["gc"])

        assert result.exit_code != 0
        assert "No server configured" in result.output

    def test_gc_requires_token(self, cli_runner_no_config: CliRunner) -> None:
        """GC without token configured fails with error."""
        result = cli_runner_no_config.invoke(cli, ["--server", "http://test", "gc"])

        assert result.exit_code != 0
        assert "No token configured" in result.output

    def test_gc_calls_endpoint(
        self, cli_runner: CliRunner, api_client: TestClient, admin_token: str
    ) -> None:
        """GC command calls the GC endpoint."""

        # Create a mock client that wraps api_client and adds auth header
        class MockClientWithAuth:
            def __init__(self, client: TestClient, token: str) -> None:
                self.client = client
                self.token = token

            def post(self, url: str, **kwargs: object) -> "httpx.Response":
                # Add auth header
                headers = kwargs.pop("headers", {})
                headers["Authorization"] = f"Bearer {self.token}"
                return self.client.post(url, headers=headers, **kwargs)

            def __enter__(self) -> "MockClientWithAuth":
                return self

            def __exit__(self, *args: object) -> None:
                pass

        mock_client = MockClientWithAuth(api_client, admin_token)

        # Mock the subprocess call in the server to return GC stats
        mock_gc_result = {
            "dry_run": False,
            "artifacts_scanned": 10,
            "blobs_found": 5,
            "blobs_deleted": 0,
            "space_reclaimed_bytes": 0,
            "symlinks_checked": 3,
            "symlinks_fixed": 1,
            "items_removed": 0,
            "errors": [],
        }

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            with patch(PATCH_RUN_CTL, new=AsyncMock(return_value=mock_gc_result)):
                result = cli_runner.invoke(
                    cli,
                    ["--server", "http://test", "--token", admin_token, "gc"],
                )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert "GC Complete:" in result.output
        assert "Artifacts scanned:" in result.output
        assert "Blobs found:" in result.output
        assert "Symlinks checked:" in result.output
        assert "Symlinks fixed:" in result.output

    def test_gc_dry_run(
        self, cli_runner: CliRunner, api_client: TestClient, admin_token: str
    ) -> None:
        """GC --dry-run shows preview."""

        class MockClientWithAuth:
            def __init__(self, client: TestClient, token: str) -> None:
                self.client = client
                self.token = token

            def post(self, url: str, **kwargs: object) -> "httpx.Response":
                headers = kwargs.pop("headers", {})
                headers["Authorization"] = f"Bearer {self.token}"
                return self.client.post(url, headers=headers, **kwargs)

            def __enter__(self) -> "MockClientWithAuth":
                return self

            def __exit__(self, *args: object) -> None:
                pass

        mock_client = MockClientWithAuth(api_client, admin_token)

        # Mock the subprocess call in the server to return dry-run results
        mock_gc_result = {
            "dry_run": True,
            "artifacts_scanned": 5,
            "blobs_found": 3,
            "blobs_deleted": 2,
            "space_reclaimed_bytes": 1024,
            "symlinks_checked": 2,
            "symlinks_fixed": 0,
            "items_removed": 0,
            "errors": [],
        }

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            with patch(PATCH_RUN_CTL, new=AsyncMock(return_value=mock_gc_result)):
                result = cli_runner.invoke(
                    cli,
                    ["--server", "http://test", "--token", admin_token, "gc", "--dry-run"],
                )

        assert result.exit_code == 0
        assert "GC Preview (dry run):" in result.output
        assert "Would delete:" in result.output

    def test_gc_displays_stats(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        admin_token: str,
        storage_service: StorageService,
    ) -> None:
        """GC displays statistics from server response."""
        # Upload some artifacts first
        files = {"file": ("test.bin", io.BytesIO(b"test content"), "application/octet-stream")}
        api_client.post("/api/v1/upload/gc-cli-test/artifact", files=files)

        class MockClientWithAuth:
            def __init__(self, client: TestClient, token: str) -> None:
                self.client = client
                self.token = token

            def post(self, url: str, **kwargs: object) -> "httpx.Response":
                headers = kwargs.pop("headers", {})
                headers["Authorization"] = f"Bearer {self.token}"
                return self.client.post(url, headers=headers, **kwargs)

            def __enter__(self) -> "MockClientWithAuth":
                return self

            def __exit__(self, *args: object) -> None:
                pass

        mock_client = MockClientWithAuth(api_client, admin_token)

        # Mock the subprocess call in the server to return stats
        mock_gc_result = {
            "dry_run": False,
            "artifacts_scanned": 5,
            "blobs_found": 3,
            "blobs_deleted": 1,
            "space_reclaimed_bytes": 512,
            "symlinks_checked": 2,
            "symlinks_fixed": 0,
            "items_removed": 0,
            "errors": [],
        }

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            with patch(PATCH_RUN_CTL, new=AsyncMock(return_value=mock_gc_result)):
                result = cli_runner.invoke(
                    cli,
                    ["--server", "http://test", "--token", admin_token, "gc"],
                )

        assert result.exit_code == 0
        assert "Artifacts scanned:" in result.output
        assert "Blobs found:" in result.output
        assert "Symlinks checked:" in result.output
        assert "Symlinks fixed:" in result.output
        assert "Deleted:" in result.output

    def test_gc_unauthorized_shows_error(
        self, cli_runner: CliRunner, api_client: TestClient, token_service: TokenService
    ) -> None:
        """GC with non-admin token shows appropriate error."""
        read_token = token_service.create_token("reader", TokenScope.READ)

        class MockClientWithAuth:
            def __init__(self, client: TestClient, token: str) -> None:
                self.client = client
                self.token = token

            def post(self, url: str, **kwargs: object) -> "httpx.Response":
                headers = kwargs.pop("headers", {})
                headers["Authorization"] = f"Bearer {self.token}"
                return self.client.post(url, headers=headers, **kwargs)

            def __enter__(self) -> "MockClientWithAuth":
                return self

            def __exit__(self, *args: object) -> None:
                pass

        mock_client = MockClientWithAuth(api_client, read_token)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "--token", read_token, "gc"],
            )

        assert result.exit_code != 0
        assert "Admin token required" in result.output
