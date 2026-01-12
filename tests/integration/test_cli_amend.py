"""Integration tests for CLI amend command."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

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


class TestAmendCommand:
    """Integration tests for amend command."""

    def test_amend_updates_source_uri(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Amend command updates source_uri."""
        # Upload artifact without source_uri
        upload_test_artifact(api_client, "test/amend", b"amend test content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "amend",
                    "test/amend:latest",
                    "--source-uri",
                    "https://github.com/example/repo",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Updated:" in result.output
        assert "https://github.com/example/repo" in result.output

    def test_amend_displays_updated_metadata(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Amend command displays updated metadata including tags."""
        upload_data = upload_test_artifact(api_client, "test/amend-meta", b"metadata test")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "amend",
                    "test/amend-meta:latest",
                    "--source-uri",
                    "https://example.com/source",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert upload_data["hash_ref"] in result.output
        assert "Source URI:" in result.output
        assert "Tags:" in result.output
        assert "latest" in result.output

    def test_amend_nonexistent_artifact_shows_error(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Amend with nonexistent artifact shows clear error."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "amend",
                    "nonexistent/artifact:latest",
                    "--source-uri",
                    "https://example.com",
                ],
            )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_amend_by_tag_name_works(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Amend by tag name resolves correctly."""
        upload_test_artifact(api_client, "test/bytag", b"tag test content")

        # Create additional tag
        api_client.post(
            "/api/v1/artifacts/test/bytag/latest/tags",
            json={"tag_name": "stable"},
        )

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "amend",
                    "test/bytag:stable",
                    "--source-uri",
                    "https://stable.example.com",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Updated:" in result.output
        assert "https://stable.example.com" in result.output

    def test_amend_by_hash_ref_works(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Amend by hash ref resolves correctly."""
        upload_data = upload_test_artifact(api_client, "test/byhash", b"hash ref test")
        hash_ref = upload_data["hash_ref"]

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "amend",
                    f"test/byhash:{hash_ref}",
                    "--source-uri",
                    "https://hash.example.com",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert hash_ref in result.output
        assert "https://hash.example.com" in result.output

    def test_amend_updates_existing_source_uri(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Amend can update an existing source_uri to a new value."""
        # Upload with source_uri
        upload_test_artifact(
            api_client,
            "test/update-uri",
            b"update test",
            source_uri="https://initial.example.com",
        )

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "amend",
                    "test/update-uri:latest",
                    "--source-uri",
                    "https://updated.example.com",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "https://updated.example.com" in result.output
        assert "https://initial.example.com" not in result.output

    def test_amend_requires_server(self, cli_runner_no_config: CliRunner) -> None:
        """Amend without server configured fails with error."""
        result = cli_runner_no_config.invoke(
            cli, ["amend", "test/artifact:latest", "--source-uri", "https://example.com"]
        )

        assert result.exit_code != 0
        assert "No server configured" in result.output

    def test_amend_requires_update_option(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Amend without any update options shows error."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "amend", "test/artifact:latest"],
            )

        assert result.exit_code != 0
        assert "No metadata updates specified" in result.output
