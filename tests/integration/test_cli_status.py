"""Integration tests for CLI status command."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    import httpx
from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie import __version__
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenService
from magpie.cli import cli

# Patch path for get_client - must match where it's imported/used in the CLI module
PATCH_GET_CLIENT = "magpie.cli.get_client"


def upload_test_artifact(
    api_client: TestClient,
    path: str,
    content: bytes,
) -> dict:
    """Helper to upload a test artifact and return response data."""
    files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
    response = api_client.post(f"/api/v1/upload/{path}", files=files)
    assert response.status_code == 200
    return response.json()


class TestStatusCommand:
    """Integration tests for status command."""

    def test_status_shows_server_url(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Status command displays configured server URL."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test.example.com", "--token", "dummy-token", "status"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Server:" in result.output
        assert "http://test.example.com" in result.output

    def test_status_shows_ok_status(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Status command displays OK status when server is healthy."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "--token", "dummy-token", "status"],
            )

        assert result.exit_code == 0
        assert "Status:" in result.output
        assert "OK" in result.output

    def test_status_shows_version(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Status command displays server version."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "--token", "dummy-token", "status"],
            )

        assert result.exit_code == 0
        assert "Version:" in result.output
        assert __version__ in result.output

    def test_status_shows_storage_stats(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Status command displays storage statistics."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "--token", "dummy-token", "status"],
            )

        assert result.exit_code == 0
        assert "Storage:" in result.output
        assert "Artifacts:" in result.output
        assert "Blobs:" in result.output

    def test_status_shows_artifact_count(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Status command shows correct artifact count."""
        # Upload some artifacts
        upload_test_artifact(api_client, "test/artifact1", b"content1")
        upload_test_artifact(api_client, "test/artifact2", b"content2")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "--token", "dummy-token", "status"],
            )

        assert result.exit_code == 0
        assert "Artifacts: 2 total" in result.output

    def test_status_requires_server(self, cli_runner_no_config: CliRunner) -> None:
        """Status without server configured fails with error."""
        result = cli_runner_no_config.invoke(cli, ["status"])

        assert result.exit_code != 0
        assert "No server configured" in result.output

    def test_status_requires_token(self, cli_runner_no_config: CliRunner) -> None:
        """Status without token configured fails with error."""
        result = cli_runner_no_config.invoke(cli, ["--server", "http://test", "status"])

        assert result.exit_code != 0
        assert "No token configured" in result.output

    def test_status_unauthorized_shows_error(
        self,
        cli_runner: CliRunner,
        api_client_no_auth_override: TestClient,
        token_service: TokenService,
    ) -> None:
        """Status with non-admin token shows appropriate error."""
        read_token = token_service.create_token("reader", TokenScope.READ)

        class MockClientWithAuth:
            def __init__(self, client: TestClient, token: str) -> None:
                self.client = client
                self.token = token

            def get(self, url: str, **kwargs: object) -> "httpx.Response":
                headers = kwargs.pop("headers", {})
                headers["Authorization"] = f"Bearer {self.token}"
                return self.client.get(url, headers=headers, **kwargs)

            def __enter__(self) -> "MockClientWithAuth":
                return self

            def __exit__(self, *args: object) -> None:
                pass

        mock_client = MockClientWithAuth(api_client_no_auth_override, read_token)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "--token", read_token, "status"],
            )

        assert result.exit_code != 0
        assert "Admin scope required" in result.output

    def test_status_json_output(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Status command outputs valid JSON when --format json is used."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "--token", "dummy-token", "--format", "json", "status"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert "data" in data
        assert data["data"]["server"] == "http://test"
        assert data["data"]["status"] == "ok"
        assert data["data"]["version"] == __version__
        assert "storage" in data["data"]

    def test_status_json_includes_storage_stats(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Status JSON output includes storage statistics."""
        upload_test_artifact(api_client, "test/artifact", b"test content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "--token", "dummy-token", "--format", "json", "status"],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        storage = data["data"]["storage"]
        assert storage["artifact_count"] == 1
        assert storage["blob_count"] == 1
        assert storage["total_size_bytes"] > 0


class TestStatusFormatSize:
    """Tests for size formatting in status command."""

    def test_status_formats_bytes(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Status command formats small sizes in bytes."""
        upload_test_artifact(api_client, "test/small", b"tiny")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "--token", "dummy-token", "status"],
            )

        assert result.exit_code == 0
        # 4 bytes should show as "4 B"
        assert "Storage:   4 B used" in result.output
