"""Integration tests for CLI query commands (ls, info, url)."""

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


class TestLsCommand:
    """Integration tests for ls command."""

    def test_ls_displays_versions_in_table_format(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls command displays versions in table format."""
        # Upload two versions
        upload_test_artifact(api_client, "test/ls", b"version 1 content")
        upload_test_artifact(api_client, "test/ls", b"version 2 content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "test/ls"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Check table headers
        assert "HASH" in result.output
        assert "TAGS" in result.output
        assert "UPLOADED_BY" in result.output
        assert "UPLOADED_AT" in result.output
        # Check we have hash refs in output
        assert "@" in result.output

    def test_ls_with_empty_artifact_shows_message(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls with non-existent artifact shows appropriate message."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "nonexistent/artifact"],
            )

        assert result.exit_code == 0
        assert "No versions found" in result.output or "No artifact found" in result.output

    def test_ls_shows_tags(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls command shows tags for versions."""
        # Upload an artifact (will have 'latest' tag)
        upload_test_artifact(api_client, "test/tags", b"content with tags")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "test/tags"],
            )

        assert result.exit_code == 0
        assert "latest" in result.output

    def test_ls_requires_server(self, cli_runner: CliRunner) -> None:
        """Ls without server configured fails with error."""
        result = cli_runner.invoke(cli, ["ls", "test/artifact"])

        assert result.exit_code != 0
        assert "No server configured" in result.output


class TestInfoCommand:
    """Integration tests for info command."""

    def test_info_displays_all_metadata_fields(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Info command displays all metadata fields."""
        upload_data = upload_test_artifact(
            api_client,
            "test/info",
            b"content for info test",
            source_uri="https://github.com/test/repo",
        )

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", "test/info:latest"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Check all metadata fields are present
        assert "Hash:" in result.output
        assert upload_data["hash"] in result.output
        assert "Hash Ref:" in result.output
        assert upload_data["hash_ref"] in result.output
        assert "Uploaded By:" in result.output
        assert "Uploaded At:" in result.output
        assert "Source URI:" in result.output
        assert "https://github.com/test/repo" in result.output
        assert "Tags:" in result.output
        assert "latest" in result.output

    def test_info_with_tag_resolves_correctly(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Info with tag name resolves correctly."""
        upload_data = upload_test_artifact(api_client, "test/tag-resolve", b"tag test")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", "test/tag-resolve:latest"],
            )

        assert result.exit_code == 0
        assert upload_data["hash_ref"] in result.output

    def test_info_with_hash_ref_resolves_correctly(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Info with hash ref resolves correctly."""
        upload_data = upload_test_artifact(api_client, "test/hash-resolve", b"hash test")
        hash_ref = upload_data["hash_ref"]

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", f"test/hash-resolve:{hash_ref}"],
            )

        assert result.exit_code == 0
        assert hash_ref in result.output

    def test_info_not_found(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Info for non-existent artifact returns error."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", "nonexistent/artifact"],
            )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_info_requires_server(self, cli_runner: CliRunner) -> None:
        """Info without server configured fails with error."""
        result = cli_runner.invoke(cli, ["info", "test/artifact"])

        assert result.exit_code != 0
        assert "No server configured" in result.output


class TestUrlCommand:
    """Integration tests for url command."""

    def test_url_outputs_bare_url(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Url command outputs bare URL for scripting."""
        upload_data = upload_test_artifact(api_client, "test/url", b"url test content")
        hash_ref = upload_data["hash_ref"]

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "url", "test/url:latest"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Check output is a bare URL
        output = result.output.strip()
        assert output.startswith("http://test/artifacts/")
        assert hash_ref in output
        assert "test/url" in output

    def test_url_suitable_for_curl(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Url output is suitable for curl/wget (single line, no extra text)."""
        upload_test_artifact(api_client, "test/curl", b"curl test")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "https://artifacts.example.com", "url", "test/curl"],
            )

        assert result.exit_code == 0
        output = result.output.strip()
        # Should be a single line
        assert "\n" not in output
        # Should start with the server URL
        assert output.startswith("https://artifacts.example.com/artifacts/")
        # Should not have any extra text
        assert output.count("http") == 1

    def test_url_resolves_tag_to_hash(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Url resolves tag to hash ref in output."""
        upload_data = upload_test_artifact(api_client, "test/resolve", b"resolve test")
        hash_ref = upload_data["hash_ref"]

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "url", "test/resolve:latest"],
            )

        assert result.exit_code == 0
        # URL should contain hash_ref, not "latest"
        assert hash_ref in result.output
        assert ":latest" not in result.output

    def test_url_not_found(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Url for non-existent artifact returns error."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "url", "nonexistent/artifact"],
            )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_url_requires_server(self, cli_runner: CliRunner) -> None:
        """Url without server configured fails with error."""
        result = cli_runner.invoke(cli, ["url", "test/artifact"])

        assert result.exit_code != 0
        assert "No server configured" in result.output
