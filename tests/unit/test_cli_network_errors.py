"""Unit tests for CLI network error handling."""

from __future__ import annotations

import errno
import socket
import ssl
from unittest.mock import MagicMock, patch

import click
import httpx
import pytest
from click.testing import CliRunner

from magpie.cli.errors import (
    format_network_error,
    handle_network_error,
    with_network_error_handling,
)
from magpie.cli.formatting import ExitCode


class TestFormatNetworkError:
    """Tests for format_network_error function."""

    def test_dns_resolution_failure(self) -> None:
        """DNS resolution failure produces helpful error message."""
        # Create ConnectError with DNS failure as cause
        dns_error = socket.gaierror("Name or service not known")
        connect_error = httpx.ConnectError("Connection failed", request=MagicMock())
        connect_error.__cause__ = dns_error

        result = format_network_error(
            connect_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "DNS resolution failed" in result
        assert "Hint:" in result
        assert "hostname is correct" in result

    def test_connection_refused(self) -> None:
        """Connection refused produces helpful error message."""
        # Create ConnectError with ConnectionRefusedError as cause
        refused_error = ConnectionRefusedError("Connection refused")
        connect_error = httpx.ConnectError("Connection refused", request=MagicMock())
        connect_error.__cause__ = refused_error

        result = format_network_error(
            connect_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "Connection refused" in result
        assert "Hint:" in result
        assert "server is running" in result

    def test_tls_handshake_failure(self) -> None:
        """TLS handshake failure produces helpful error message."""
        # Create ConnectError with ssl.SSLError as cause
        ssl_error = ssl.SSLError("certificate verify failed")
        connect_error = httpx.ConnectError("TLS handshake failed", request=MagicMock())
        connect_error.__cause__ = ssl_error

        result = format_network_error(
            connect_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "TLS handshake failed" in result
        assert "Hint:" in result
        assert "certificate" in result.lower()

    def test_network_unreachable(self) -> None:
        """Network unreachable produces helpful error message."""
        # Create ConnectError with OSError using errno.ENETUNREACH as cause
        net_error = OSError(errno.ENETUNREACH, "Network is unreachable")
        connect_error = httpx.ConnectError("Network unreachable", request=MagicMock())
        connect_error.__cause__ = net_error

        result = format_network_error(
            connect_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "Network unreachable" in result
        assert "Hint:" in result
        assert "routing" in result.lower()

    def test_connection_reset(self) -> None:
        """Connection reset by peer produces helpful error message."""
        # Create ConnectError with ConnectionResetError as cause
        reset_error = ConnectionResetError("Connection reset by peer")
        connect_error = httpx.ConnectError("Connection reset", request=MagicMock())
        connect_error.__cause__ = reset_error

        result = format_network_error(
            connect_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "Connection reset" in result
        assert "Hint:" in result
        assert "server" in result.lower()

    def test_generic_connect_error(self) -> None:
        """Generic ConnectError with unrecognized cause shows cause details."""
        # Create ConnectError with generic exception as cause
        generic_error = RuntimeError("Some unexpected error")
        connect_error = httpx.ConnectError("Connection failed", request=MagicMock())
        connect_error.__cause__ = generic_error

        result = format_network_error(
            connect_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "Connection failed" in result
        assert "Some unexpected error" in result
        assert "Hint:" in result

    def test_connect_error_no_cause(self) -> None:
        """ConnectError without a cause falls back to generic message."""
        connect_error = httpx.ConnectError("Connection failed", request=MagicMock())
        # Explicitly set no cause
        connect_error.__cause__ = None

        result = format_network_error(
            connect_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "Connection failed" in result
        assert "Hint:" in result

    def test_timeout_error(self) -> None:
        """Timeout error produces helpful error message."""
        timeout_error = httpx.TimeoutException("Request timed out", request=MagicMock())

        result = format_network_error(
            timeout_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "timed out" in result
        assert "Hint:" in result
        assert "timeout" in result

    def test_generic_request_error(self) -> None:
        """Generic request error produces error message."""
        request_error = httpx.RequestError("Network error", request=MagicMock())

        result = format_network_error(
            request_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "Network error" in result
        assert "Hint:" in result

    def test_hostname_extraction_from_url(self) -> None:
        """Hostname is extracted correctly from server URL."""
        connect_error = httpx.ConnectError("Connection failed", request=MagicMock())
        connect_error.__cause__ = socket.gaierror("Name or service not known")

        result = format_network_error(
            connect_error, "test_operation", server="https://artifacts.example.com:8443/api"
        )

        assert "artifacts.example.com:8443" in result
        assert "https://" not in result

    def test_no_server_url_provided(self) -> None:
        """Error message uses 'server' when no URL provided."""
        connect_error = httpx.ConnectError("Connection failed", request=MagicMock())

        result = format_network_error(connect_error, "test_operation", server=None)

        assert "test_operation failed:" in result

        assert "server" in result

    def test_standalone_socket_gaierror(self) -> None:
        """Standalone socket.gaierror produces helpful DNS error message."""
        dns_error = socket.gaierror("Name or service not known")

        result = format_network_error(
            dns_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "DNS resolution failed" in result
        assert "Hint:" in result
        assert "hostname is correct" in result

    def test_proxy_error(self) -> None:
        """ProxyError produces helpful error message."""
        proxy_error = httpx.ProxyError("Proxy connection failed")

        result = format_network_error(
            proxy_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "Proxy error" in result
        assert "Hint:" in result
        assert "proxy configuration" in result

    def test_unsupported_protocol_error(self) -> None:
        """UnsupportedProtocol produces helpful error message."""
        protocol_error = httpx.UnsupportedProtocol("Unsupported protocol 'ftp'")

        result = format_network_error(
            protocol_error, "test_operation", server="ftp://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "Unsupported protocol" in result
        assert "Hint:" in result
        assert "supported protocol" in result

    def test_protocol_error(self) -> None:
        """ProtocolError produces helpful error message."""
        protocol_error = httpx.ProtocolError("Invalid HTTP response")

        result = format_network_error(
            protocol_error, "test_operation", server="https://magpie.example.com"
        )

        assert "test_operation failed:" in result

        assert "magpie.example.com" in result
        assert "Protocol error" in result
        assert "Hint:" in result
        assert "invalid response" in result


class TestHandleNetworkError:
    """Tests for handle_network_error function."""

    def test_raises_click_exception(self) -> None:
        """handle_network_error raises ClickException with formatted message."""
        from click.exceptions import ClickException

        connect_error = httpx.ConnectError("Connection failed", request=MagicMock())
        connect_error.__cause__ = socket.gaierror("Name or service not known")

        with pytest.raises(ClickException) as exc_info:
            handle_network_error(
                connect_error, operation="push", server="https://magpie.example.com"
            )

        assert "DNS resolution failed" in str(exc_info.value)
        assert "magpie.example.com" in str(exc_info.value)

    @patch("magpie.cli.formatting.is_json_output")
    @patch("magpie.cli.formatting.output_error")
    def test_json_output_mode(self, mock_output_error: MagicMock, mock_is_json: MagicMock) -> None:
        """In JSON mode, calls output_error with network error code."""
        mock_is_json.return_value = True
        mock_output_error.side_effect = SystemExit(ExitCode.NETWORK_ERROR)

        connect_error = httpx.ConnectError("Connection failed", request=MagicMock())

        with pytest.raises(SystemExit) as exc_info:
            handle_network_error(
                connect_error, operation="push", server="https://magpie.example.com"
            )

        assert exc_info.value.code == ExitCode.NETWORK_ERROR
        mock_output_error.assert_called_once()
        call_args = mock_output_error.call_args
        assert call_args[0][0] == "NETWORK_ERROR"
        assert call_args[1]["exit_code"] == ExitCode.NETWORK_ERROR


class TestWithNetworkErrorHandlingDecorator:
    """Tests for with_network_error_handling decorator."""

    def test_catches_connect_error(self) -> None:
        """Decorator catches ConnectError and converts to user-friendly error."""
        from click.exceptions import ClickException

        @with_network_error_handling
        def failing_command() -> None:
            raise httpx.ConnectError("Connection failed", request=MagicMock())

        with pytest.raises(ClickException) as exc_info:
            failing_command()

        assert "Connection failed" in str(exc_info.value)

    def test_catches_timeout_error(self) -> None:
        """Decorator catches TimeoutException and converts to user-friendly error."""
        from click.exceptions import ClickException

        @with_network_error_handling
        def failing_command() -> None:
            raise httpx.TimeoutException("Timeout", request=MagicMock())

        with pytest.raises(ClickException) as exc_info:
            failing_command()

        assert "timed out" in str(exc_info.value)

    def test_catches_socket_gaierror(self) -> None:
        """Decorator catches socket.gaierror (DNS errors outside httpx)."""
        from click.exceptions import ClickException

        @with_network_error_handling
        def failing_command() -> None:
            raise socket.gaierror("Name or service not known")

        with pytest.raises(ClickException):
            failing_command()

    def test_passes_through_non_network_errors(self) -> None:
        """Decorator doesn't catch non-network exceptions."""

        @with_network_error_handling
        def failing_command() -> None:
            raise ValueError("Not a network error")

        with pytest.raises(ValueError) as exc_info:
            failing_command()

        assert "Not a network error" in str(exc_info.value)

    def test_successful_execution(self) -> None:
        """Decorator allows successful execution."""

        @with_network_error_handling
        def successful_command() -> str:
            return "success"

        result = successful_command()
        assert result == "success"

    def test_catches_proxy_error(self) -> None:
        """Decorator catches ProxyError and converts to user-friendly error."""
        from click.exceptions import ClickException

        @with_network_error_handling
        def failing_command() -> None:
            raise httpx.ProxyError("Proxy connection failed")

        with pytest.raises(ClickException) as exc_info:
            failing_command()

        assert "Proxy error" in str(exc_info.value)

    def test_catches_unsupported_protocol_error(self) -> None:
        """Decorator catches UnsupportedProtocol and converts to user-friendly error."""
        from click.exceptions import ClickException

        @with_network_error_handling
        def failing_command() -> None:
            raise httpx.UnsupportedProtocol("Unsupported protocol")

        with pytest.raises(ClickException) as exc_info:
            failing_command()

        assert "Unsupported protocol" in str(exc_info.value)

    def test_catches_protocol_error(self) -> None:
        """Decorator catches ProtocolError and converts to user-friendly error."""
        from click.exceptions import ClickException

        @with_network_error_handling
        def failing_command() -> None:
            raise httpx.ProtocolError("Invalid response")

        with pytest.raises(ClickException) as exc_info:
            failing_command()

        assert "Protocol error" in str(exc_info.value)

    def test_catches_json_decode_error(self) -> None:
        """Decorator catches JSONDecodeError and converts to user-friendly error."""
        import json

        from click.exceptions import ClickException

        @with_network_error_handling
        def failing_command() -> None:
            raise json.JSONDecodeError("Expecting value", "invalid json", 0)

        with pytest.raises(ClickException) as exc_info:
            failing_command()

        assert "Invalid JSON response from server" in str(exc_info.value)
        assert "Expecting value" in str(exc_info.value)
        assert "Hint:" in str(exc_info.value)

    def test_catches_http_status_error(self) -> None:
        """Decorator catches HTTPStatusError and converts to user-friendly error."""
        from click.exceptions import ClickException

        @with_network_error_handling
        def failing_command() -> None:
            mock_request = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.json.return_value = {"detail": "Not found"}
            raise httpx.HTTPStatusError(
                "404 Not Found", request=mock_request, response=mock_response
            )

        with pytest.raises(ClickException) as exc_info:
            failing_command()

        assert "404" in str(exc_info.value)


class TestExitCodes:
    """Tests for exit codes in error scenarios."""

    def test_http_status_to_exit_code_mapping(self) -> None:
        """HTTP status codes map to appropriate exit codes."""
        from magpie.cli.formatting import http_status_to_exit_code

        # Auth errors should map to AUTH_ERROR exit code
        assert http_status_to_exit_code(401) == ExitCode.AUTH_ERROR
        assert http_status_to_exit_code(403) == ExitCode.AUTH_ERROR

        # Not found should map to NOT_FOUND exit code
        assert http_status_to_exit_code(404) == ExitCode.NOT_FOUND

        # Other errors should map to GENERAL_ERROR
        assert http_status_to_exit_code(400) == ExitCode.GENERAL_ERROR
        assert http_status_to_exit_code(500) == ExitCode.GENERAL_ERROR

    def test_click_exception_exit_code_assignment(self) -> None:
        """ClickException objects get appropriate exit codes assigned."""
        from click.exceptions import ClickException

        from magpie.cli.errors import handle_http_error

        # Mock response with 404 status
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {"detail": "Not found"}

        with pytest.raises(ClickException) as exc_info:
            handle_http_error(mock_response, "Test operation")

        # Check that the exception has the correct exit code
        assert exc_info.value.exit_code == ExitCode.NOT_FOUND


class TestCliRunnerIntegration:
    """Integration tests using Click's CliRunner to verify exit codes end-to-end."""

    def test_network_error_exit_code_with_runner(self) -> None:
        """Network error in human mode produces exit code 2 (NETWORK_ERROR)."""

        @click.command()
        @with_network_error_handling
        def test_command() -> None:
            """Test command that raises a network error."""
            raise httpx.ConnectError("Connection failed", request=MagicMock())

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is NETWORK_ERROR (2), not GENERAL_ERROR (1)
        assert result.exit_code == ExitCode.NETWORK_ERROR
        assert "Connection failed" in result.output

    def test_timeout_error_exit_code_with_runner(self) -> None:
        """Timeout error in human mode produces exit code 2 (NETWORK_ERROR)."""

        @click.command()
        @with_network_error_handling
        def test_command() -> None:
            """Test command that raises a timeout error."""
            raise httpx.TimeoutException("Request timed out", request=MagicMock())

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is NETWORK_ERROR (2)
        assert result.exit_code == ExitCode.NETWORK_ERROR
        assert "timed out" in result.output

    def test_dns_error_exit_code_with_runner(self) -> None:
        """DNS error in human mode produces exit code 2 (NETWORK_ERROR)."""

        @click.command()
        @with_network_error_handling
        def test_command() -> None:
            """Test command that raises a DNS error."""
            dns_error = socket.gaierror("Name or service not known")
            connect_error = httpx.ConnectError("DNS failed", request=MagicMock())
            connect_error.__cause__ = dns_error
            raise connect_error

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is NETWORK_ERROR (2)
        assert result.exit_code == ExitCode.NETWORK_ERROR
        assert "DNS resolution failed" in result.output

    def test_non_network_error_exit_code_with_runner(self) -> None:
        """Non-network errors still exit with code 1 (GENERAL_ERROR)."""

        @click.command()
        @with_network_error_handling
        def test_command() -> None:
            """Test command that raises a non-network error."""
            raise ValueError("Not a network error")

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is GENERAL_ERROR (1) for unhandled exceptions
        assert result.exit_code == ExitCode.GENERAL_ERROR

    def test_http_404_error_exit_code_with_runner(self) -> None:
        """HTTP 404 error in human mode produces exit code 3 (NOT_FOUND)."""
        from magpie.cli.errors import handle_http_error

        @click.command()
        def test_command() -> None:
            """Test command that raises an HTTP 404 error."""
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.json.return_value = {"detail": "Not found"}
            handle_http_error(mock_response, "Test operation")

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is NOT_FOUND (3)
        assert result.exit_code == ExitCode.NOT_FOUND
        assert "Test operation failed (404)" in result.output

    def test_http_401_error_exit_code_with_runner(self) -> None:
        """HTTP 401 error in human mode produces exit code 4 (AUTH_ERROR)."""
        from magpie.cli.errors import handle_http_error

        @click.command()
        def test_command() -> None:
            """Test command that raises an HTTP 401 error."""
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.json.return_value = {"detail": "Unauthorized"}
            handle_http_error(mock_response, "Test operation")

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is AUTH_ERROR (4)
        assert result.exit_code == ExitCode.AUTH_ERROR
        assert "Test operation failed (401)" in result.output

    def test_http_500_error_exit_code_with_runner(self) -> None:
        """HTTP 500 error in human mode produces exit code 1 (GENERAL_ERROR)."""
        from magpie.cli.errors import handle_http_error

        @click.command()
        def test_command() -> None:
            """Test command that raises an HTTP 500 error."""
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.json.return_value = {"detail": "Internal server error"}
            handle_http_error(mock_response, "Test operation")

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is GENERAL_ERROR (1)
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "Test operation failed (500)" in result.output

    def test_json_decode_error_exit_code_with_runner(self) -> None:
        """JSONDecodeError in human mode produces exit code 2 (NETWORK_ERROR)."""
        import json

        @click.command()
        @with_network_error_handling
        def test_command() -> None:
            """Test command that raises a JSON decode error."""
            raise json.JSONDecodeError("Expecting value", "invalid json", 0)

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is NETWORK_ERROR (2)
        assert result.exit_code == ExitCode.NETWORK_ERROR
        assert "Invalid JSON response from server" in result.output
        assert "Hint:" in result.output

    def test_http_status_error_exit_code_with_runner(self) -> None:
        """HTTPStatusError in human mode produces appropriate exit code."""

        @click.command()
        @with_network_error_handling
        def test_command() -> None:
            """Test command that raises an HTTPStatusError."""
            mock_request = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.json.return_value = {"detail": "Not found"}
            raise httpx.HTTPStatusError(
                "404 Not Found", request=mock_request, response=mock_response
            )

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is NOT_FOUND (3) for 404 errors
        assert result.exit_code == ExitCode.NOT_FOUND
        assert "404" in result.output

    @patch("magpie.cli.formatting.is_json_output")
    @patch("magpie.cli.formatting.output_error")
    def test_json_decode_error_json_mode(
        self, mock_output_error: MagicMock, mock_is_json: MagicMock
    ) -> None:
        """JSONDecodeError in JSON mode calls output_error with network error code."""
        import json

        mock_is_json.return_value = True
        mock_output_error.side_effect = SystemExit(ExitCode.NETWORK_ERROR)

        @click.command()
        @with_network_error_handling
        def test_command() -> None:
            """Test command that raises a JSON decode error."""
            raise json.JSONDecodeError("Expecting value", "invalid json", 0)

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is NETWORK_ERROR (2)
        assert result.exit_code == ExitCode.NETWORK_ERROR
        # Verify output_error was called with correct parameters
        mock_output_error.assert_called_once()
        call_args = mock_output_error.call_args
        assert call_args[0][0] == "NETWORK_ERROR"
        assert "Invalid JSON response from server" in call_args[0][1]
        assert call_args[1]["exit_code"] == ExitCode.NETWORK_ERROR

    @patch("magpie.cli.formatting.is_json_output")
    @patch("magpie.cli.formatting.output_error")
    def test_http_status_error_json_mode(
        self, mock_output_error: MagicMock, mock_is_json: MagicMock
    ) -> None:
        """HTTPStatusError in JSON mode calls output_error with appropriate error code."""
        mock_is_json.return_value = True
        mock_output_error.side_effect = SystemExit(ExitCode.NOT_FOUND)

        @click.command()
        @with_network_error_handling
        def test_command() -> None:
            """Test command that raises an HTTPStatusError."""
            mock_request = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.json.return_value = {"detail": "Not found"}
            raise httpx.HTTPStatusError(
                "404 Not Found", request=mock_request, response=mock_response
            )

        runner = CliRunner()
        result = runner.invoke(test_command)

        # Verify exit code is NOT_FOUND (3) for 404 errors
        assert result.exit_code == ExitCode.NOT_FOUND
        # Verify output_error was called with correct parameters
        mock_output_error.assert_called_once()
        call_args = mock_output_error.call_args
        assert call_args[0][0] == "NOT_FOUND"
        assert "Not found" in call_args[0][1]
        assert call_args[1]["exit_code"] == ExitCode.NOT_FOUND
