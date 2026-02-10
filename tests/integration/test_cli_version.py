"""Integration tests for CLI version command with --server-version flag."""

from __future__ import annotations

from unittest.mock import Mock, patch

import httpx
from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie import __version__
from magpie.cli import cli
from magpie.cli.formatting import ExitCode


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

    def test_version_with_server_flag_handles_network_error(self, cli_runner: CliRunner) -> None:
        """Version command with --server-version flag handles network errors with exit code 2."""
        mock_client = Mock()
        # Raise httpx.ConnectError to test network error handling
        mock_request = Mock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused", request=mock_request)
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)

        with patch("magpie.cli.get_client") as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "version", "--server-version"],
            )

        # Should exit with NETWORK_ERROR code (2), not generic error (1)
        assert result.exit_code == ExitCode.NETWORK_ERROR
        # Should show user-friendly network error message
        assert (
            "Could not connect to server" in result.output or "Connection refused" in result.output
        )

    def test_version_with_server_flag_handles_timeout_error(self, cli_runner: CliRunner) -> None:
        """Version command with --server-version flag handles timeout errors with exit code 2."""
        mock_client = Mock()
        mock_request = Mock()
        mock_client.get.side_effect = httpx.TimeoutException(
            "Request timed out", request=mock_request
        )
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)

        with patch("magpie.cli.get_client") as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "version", "--server-version"],
            )

        # Should exit with NETWORK_ERROR code (2)
        assert result.exit_code == ExitCode.NETWORK_ERROR
        # Should show timeout-specific message
        assert "timed out" in result.output

    def test_version_with_server_flag_handles_proxy_error(self, cli_runner: CliRunner) -> None:
        """Version command with --server-version flag handles proxy errors with exit code 2."""
        mock_client = Mock()
        mock_client.get.side_effect = httpx.ProxyError("Proxy connection failed")
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)

        with patch("magpie.cli.get_client") as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "version", "--server-version"],
            )

        # Should exit with NETWORK_ERROR code (2)
        assert result.exit_code == ExitCode.NETWORK_ERROR
        # Should show proxy-specific message
        assert "Proxy error" in result.output

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

    def test_version_with_server_flag_no_server_configured(
        self, cli_runner_no_config: CliRunner
    ) -> None:
        """Version command with --server-version flag fails when no server is configured."""
        result = cli_runner_no_config.invoke(cli, ["version", "--server-version"])

        # Should exit with error
        assert result.exit_code == 1
        # Should show error message about missing server configuration
        assert "No server configured" in result.output
        assert "MAGPIE_SERVER" in result.output
