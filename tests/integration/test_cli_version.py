"""Integration tests for CLI version command with --server-version flag."""

from __future__ import annotations

from unittest.mock import Mock, patch

from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie import __version__
from magpie.cli import cli


class TestVersionCommand:
    """Tests for magpie version command."""

    def test_version_shows_client_version(self, cli_runner: CliRunner) -> None:
        """Version command without --server-version shows client version only."""
        result = cli_runner.invoke(cli, ["version"])

        assert result.exit_code == 0
        assert f"magpie {__version__}" in result.output
        # Should NOT include server version
        assert "server:" not in result.output

    def test_version_with_server_flag_shows_both_versions(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Version command with --server-version flag shows both client and server versions."""
        with patch("magpie.cli.get_client") as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "version", "--server-version"],
            )

        assert result.exit_code == 0
        assert f"magpie {__version__}" in result.output
        assert "server:" in result.output
        assert f"server: {__version__}" in result.output

    def test_version_with_server_flag_handles_connection_error(self, cli_runner: CliRunner) -> None:
        """Version command with --server-version flag handles connection errors gracefully."""
        mock_client = Mock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)

        with patch("magpie.cli.get_client") as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "version", "--server-version"],
            )

        # Should exit with error
        assert result.exit_code == 1
        # Should show client version and error message
        assert f"magpie {__version__}" in result.output
        assert "error" in result.output.lower()
        assert "Connection refused" in result.output

    def test_version_with_server_flag_handles_missing_version_field(
        self, cli_runner: CliRunner
    ) -> None:
        """Version command with --server-version flag handles missing version field."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {"status": "ok"}  # No version field
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)

        with patch("magpie.cli.get_client") as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "version", "--server-version"],
            )

        assert result.exit_code == 0
        assert f"magpie {__version__}" in result.output
        assert "server: unknown" in result.output
