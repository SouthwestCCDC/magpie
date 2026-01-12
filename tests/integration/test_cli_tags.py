"""Integration tests for CLI tag commands (tag, untag, flush-tag)."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie.cli import cli
from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_storage_service
from magpie.storage.service import StorageService

# Patch path for get_client - must match where it's imported/used in the CLI module
PATCH_GET_CLIENT = "magpie.cli.get_client"

# Patch path for subprocess in server flush-tag route
PATCH_FLUSH_TAG_CTL = "magpie.server.routes.tags.run_ctl_command"


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
def api_client(test_storage_service: StorageService) -> TestClient:
    """Create test API client with overridden storage service dependency."""

    def override_storage_service() -> StorageService:
        return test_storage_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create Click CLI test runner."""
    return CliRunner()


def upload_test_artifact(
    api_client: TestClient,
    path: str,
    content: bytes,
    source_uri: str | None = None,
) -> dict:
    """Helper to upload a test artifact and return response data."""
    files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
    params = {}
    if source_uri:
        params["source_uri"] = source_uri

    response = api_client.post(
        f"/api/v1/upload/{path}",
        files=files,
        params=params if params else None,
    )
    assert response.status_code == 200
    return response.json()


class TestTagCommand:
    """Integration tests for tag command."""

    def test_tag_creates_new_tag(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Tag command creates a new tag on an artifact."""
        upload_data = upload_test_artifact(api_client, "test/tag", b"tag test content")
        hash_ref = upload_data["hash_ref"]

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "tag", f"test/tag:{hash_ref}", "--as", "v1.0"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "v1.0" in result.output
        assert hash_ref in result.output

    def test_tag_on_latest(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Tag command works with :latest ref."""
        upload_test_artifact(api_client, "test/latest-tag", b"latest tag test")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "tag", "test/latest-tag:latest", "--as", "stable"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "stable" in result.output

    def test_tag_shows_all_tags(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Tag command displays all tags after creation."""
        upload_test_artifact(api_client, "test/all-tags", b"all tags test")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            # Create first additional tag
            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "tag", "test/all-tags:latest", "--as", "v1.0"],
            )

        assert result.exit_code == 0
        # Should show both latest and v1.0
        assert "latest" in result.output
        assert "v1.0" in result.output

    def test_tag_not_found(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Tag on non-existent artifact returns error."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "tag", "nonexistent/artifact:latest", "--as", "v1.0"],
            )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_tag_requires_server(self, cli_runner_no_config: CliRunner) -> None:
        """Tag without server configured fails with error."""
        result = cli_runner_no_config.invoke(cli, ["tag", "test/artifact:latest", "--as", "v1.0"])

        assert result.exit_code != 0
        assert "No server configured" in result.output


class TestUntagCommand:
    """Integration tests for untag command."""

    def test_untag_removes_tag(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Untag command removes a tag from an artifact."""
        # Upload and create a custom tag
        upload_test_artifact(api_client, "test/untag", b"untag test content")

        # Create a tag first via API
        api_client.post(
            "/api/v1/artifacts/test/untag/latest/tags",
            json={"tag_name": "removeme"},
        )

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "untag", "test/untag", "removeme"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Removed tag 'removeme'" in result.output

    def test_untag_not_found(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Untag on non-existent tag returns error."""
        upload_test_artifact(api_client, "test/untag-missing", b"untag missing test")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "untag", "test/untag-missing", "nonexistent"],
            )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_untag_requires_server(self, cli_runner_no_config: CliRunner) -> None:
        """Untag without server configured fails with error."""
        result = cli_runner_no_config.invoke(cli, ["untag", "test/artifact", "v1.0"])

        assert result.exit_code != 0
        assert "No server configured" in result.output


class TestFlushTagCommand:
    """Integration tests for flush-tag command."""

    def test_flush_tag_removes_tag_globally(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Flush-tag removes a tag from all artifacts."""
        # Mock the subprocess call to return flush-tag result
        mock_flush_result = {
            "tag": "common-tag",
            "dry_run": False,
            "artifacts_affected": 2,
            "artifacts": ["test/flush1", "test/flush2"],
        }

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            with patch(PATCH_FLUSH_TAG_CTL, new=AsyncMock(return_value=mock_flush_result)):
                result = cli_runner.invoke(
                    cli,
                    ["--server", "http://test", "flush-tag", "common-tag", "--yes"],
                )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Removed tag 'common-tag'" in result.output
        assert "2 artifact" in result.output

    def test_flush_tag_dry_run(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Flush-tag dry-run shows what would be affected."""
        # Mock the subprocess call to return dry-run result
        mock_flush_result = {
            "tag": "to-flush",
            "dry_run": True,
            "artifacts_affected": 1,
            "artifacts": ["test/flush-dry"],
        }

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            with patch(PATCH_FLUSH_TAG_CTL, new=AsyncMock(return_value=mock_flush_result)):
                result = cli_runner.invoke(
                    cli,
                    ["--server", "http://test", "flush-tag", "to-flush", "--dry-run"],
                )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Would remove tag 'to-flush'" in result.output

    def test_flush_tag_no_matches(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Flush-tag with no matching artifacts reports 0."""
        # Mock the subprocess call to return no matches
        mock_flush_result = {
            "tag": "nonexistent-tag",
            "dry_run": False,
            "artifacts_affected": 0,
            "artifacts": [],
        }

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            with patch(PATCH_FLUSH_TAG_CTL, new=AsyncMock(return_value=mock_flush_result)):
                result = cli_runner.invoke(
                    cli,
                    ["--server", "http://test", "flush-tag", "nonexistent-tag", "--yes"],
                )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "0 artifact" in result.output

    def test_flush_tag_shows_affected_artifacts(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Flush-tag shows list of affected artifacts."""
        # Mock the subprocess call to return affected artifacts
        mock_flush_result = {
            "tag": "show-me",
            "dry_run": False,
            "artifacts_affected": 1,
            "artifacts": ["test/show-affected"],
        }

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            with patch(PATCH_FLUSH_TAG_CTL, new=AsyncMock(return_value=mock_flush_result)):
                result = cli_runner.invoke(
                    cli,
                    ["--server", "http://test", "flush-tag", "show-me", "--yes"],
                )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Affected artifacts:" in result.output
        assert "test/show-affected" in result.output

    def test_flush_tag_requires_server(self, cli_runner_no_config: CliRunner) -> None:
        """Flush-tag without server configured fails with error."""
        result = cli_runner_no_config.invoke(cli, ["flush-tag", "some-tag", "--yes"])

        assert result.exit_code != 0
        assert "No server configured" in result.output

    def test_flush_tag_protected_tag_requires_force(self, cli_runner: CliRunner) -> None:
        """Flush-tag on protected tags requires --force flag."""
        # Test several protected tags (lowercase)
        protected_tags = ["latest", "stable", "production", "prod", "release"]

        for tag in protected_tags:
            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "flush-tag", tag, "--yes"],
            )

            assert result.exit_code != 0, f"Tag '{tag}' should require --force"
            assert "protected" in result.output.lower()
            assert "--force" in result.output

    def test_flush_tag_protected_tag_case_insensitive(self, cli_runner: CliRunner) -> None:
        """Flush-tag protection is case-insensitive."""
        # Test mixed-case variants of protected tags
        mixed_case_tags = ["LATEST", "Stable", "PRODUCTION", "Prod", "Release"]

        for tag in mixed_case_tags:
            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "flush-tag", tag, "--yes"],
            )

            assert result.exit_code != 0, f"Tag '{tag}' should require --force (case-insensitive)"
            assert "protected" in result.output.lower()
            assert "--force" in result.output

    def test_flush_tag_protected_tag_with_force_succeeds(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Flush-tag on protected tags succeeds with --force flag."""
        # Mock the subprocess call to return flush result
        mock_flush_result = {
            "tag": "latest",
            "dry_run": False,
            "artifacts_affected": 1,
            "artifacts": ["test/force-flush"],
        }

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            with patch(PATCH_FLUSH_TAG_CTL, new=AsyncMock(return_value=mock_flush_result)):
                result = cli_runner.invoke(
                    cli,
                    ["--server", "http://test", "flush-tag", "latest", "--force", "--yes"],
                )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Removed tag 'latest'" in result.output

    def test_flush_tag_non_protected_tag_no_force_needed(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Flush-tag on non-protected tags works without --force."""
        # Mock the subprocess call to return flush result
        mock_flush_result = {
            "tag": "custom-tag",
            "dry_run": False,
            "artifacts_affected": 1,
            "artifacts": ["test/normal-tag"],
        }

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            with patch(PATCH_FLUSH_TAG_CTL, new=AsyncMock(return_value=mock_flush_result)):
                result = cli_runner.invoke(
                    cli,
                    ["--server", "http://test", "flush-tag", "custom-tag", "--yes"],
                )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Removed tag 'custom-tag'" in result.output
