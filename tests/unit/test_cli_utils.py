"""Tests for CLI error handling utilities."""

from __future__ import annotations

import json
from unittest.mock import Mock

import click
import pytest
from click.testing import CliRunner

from magpie.cli.errors import (
    TOKEN_MASK,
    format_auth_error,
    handle_http_error,
    handle_response_error,
    mask_token,
)
from magpie.cli.formatting import format_option


class TestBackwardCompatibility:
    """Tests for backward compatibility with utils module re-exports."""

    def test_utils_reexports_token_mask(self) -> None:
        """TOKEN_MASK is re-exported from utils for backward compatibility."""
        from magpie.cli.utils import TOKEN_MASK as utils_mask

        assert utils_mask == TOKEN_MASK

    def test_utils_reexports_mask_token(self) -> None:
        """mask_token is re-exported from utils for backward compatibility."""
        from magpie.cli.utils import mask_token as utils_mask_token

        assert utils_mask_token is mask_token

    def test_utils_reexports_format_auth_error(self) -> None:
        """format_auth_error is re-exported from utils for backward compatibility."""
        from magpie.cli.utils import format_auth_error as utils_format

        assert utils_format is format_auth_error

    def test_utils_reexports_handle_http_error(self) -> None:
        """handle_http_error is re-exported from utils for backward compatibility."""
        from magpie.cli.utils import handle_http_error as utils_handle

        assert utils_handle is handle_http_error


class TestMaskToken:
    """Tests for mask_token function."""

    def test_mask_long_token(self) -> None:
        """Tokens longer than 4 chars show last 4 chars."""
        result = mask_token("mgp_1234567890abcdef")
        assert result == "********cdef"

    def test_mask_exactly_5_chars(self) -> None:
        """Token of exactly 5 chars shows last 4."""
        result = mask_token("12345")
        assert result == "********2345"

    def test_mask_short_token_4_chars(self) -> None:
        """Token of exactly 4 chars returns just the mask."""
        result = mask_token("abcd")
        assert result == TOKEN_MASK

    def test_mask_short_token_3_chars(self) -> None:
        """Token shorter than 4 chars returns just the mask."""
        result = mask_token("abc")
        assert result == TOKEN_MASK

    def test_mask_empty_token(self) -> None:
        """Empty token returns just the mask."""
        result = mask_token("")
        assert result == TOKEN_MASK

    def test_mask_single_char(self) -> None:
        """Single character token returns just the mask."""
        result = mask_token("x")
        assert result == TOKEN_MASK


class TestFormatAuthError:
    """Tests for format_auth_error function."""

    def test_format_with_json_detail(self) -> None:
        """Error message includes detail from JSON response."""
        response = Mock()
        response.status_code = 500
        response.json.return_value = {"detail": "Internal server error"}
        response.text = "raw text"

        result = format_auth_error(response, "Upload")
        assert result == "Upload failed (500): Internal server error"

    def test_format_with_json_no_detail(self) -> None:
        """Falls back to response.text when JSON has no detail key."""
        response = Mock()
        response.status_code = 400
        response.json.return_value = {"error": "something"}
        response.text = "Bad request"

        result = format_auth_error(response, "Download")
        assert result == "Download failed (400): Bad request"

    def test_format_with_json_parse_error(self) -> None:
        """Falls back to response.text when JSON parsing fails."""
        response = Mock()
        response.status_code = 502
        response.json.side_effect = ValueError("Invalid JSON")
        response.text = "Bad gateway"

        result = format_auth_error(response, "Info")
        assert result == "Info failed (502): Bad gateway"

    def test_format_401_with_token(self) -> None:
        """401 errors include masked token in message."""
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"detail": "Unauthorized"}

        result = format_auth_error(response, "Upload", "mgp_secret123456")
        assert result == "Upload failed (401): Unauthorized (token: ********3456)"

    def test_format_403_with_token(self) -> None:
        """403 errors include masked token in message."""
        response = Mock()
        response.status_code = 403
        response.json.return_value = {"detail": "Forbidden"}

        result = format_auth_error(response, "Download", "mgp_secret123456")
        assert result == "Download failed (403): Forbidden (token: ********3456)"

    def test_format_401_without_token(self) -> None:
        """401 errors without token don't include token suffix."""
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"detail": "Unauthorized"}

        result = format_auth_error(response, "Upload", None)
        assert result == "Upload failed (401): Unauthorized"
        assert "token:" not in result

    def test_format_non_auth_error_with_token(self) -> None:
        """Non-auth errors don't include token even if provided."""
        response = Mock()
        response.status_code = 500
        response.json.return_value = {"detail": "Server error"}

        result = format_auth_error(response, "Upload", "mgp_secret123456")
        assert result == "Upload failed (500): Server error"
        assert "token:" not in result


class TestHandleHttpError:
    """Tests for handle_http_error function."""

    def test_raises_click_exception(self) -> None:
        """Always raises ClickException."""
        response = Mock()
        response.status_code = 500
        response.json.return_value = {"detail": "Error"}

        with pytest.raises(click.ClickException) as exc_info:
            handle_http_error(response, "Test")

        assert "Test failed (500): Error" in str(exc_info.value)

    def test_exception_includes_masked_token_for_401(self) -> None:
        """ClickException message includes masked token for 401."""
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"detail": "Unauthorized"}

        with pytest.raises(click.ClickException) as exc_info:
            handle_http_error(response, "Upload", "mgp_testtoken1234")

        assert "token: ********1234" in str(exc_info.value)

    def test_exception_includes_masked_token_for_403(self) -> None:
        """ClickException message includes masked token for 403."""
        response = Mock()
        response.status_code = 403
        response.json.return_value = {"detail": "Forbidden"}

        with pytest.raises(click.ClickException) as exc_info:
            handle_http_error(response, "Download", "mgp_testtoken5678")

        assert "token: ********5678" in str(exc_info.value)


class TestHandleResponseError:
    """Tests for handle_response_error function.

    This function handles HTTP error responses for both JSON and human-readable
    output modes. It extracts error details from the response and either outputs
    JSON error envelope or raises ClickException for human mode.
    """

    def test_human_mode_raises_click_exception(self) -> None:
        """In human mode, raises ClickException with formatted error."""
        response = Mock()
        response.status_code = 500
        response.json.return_value = {"detail": "Internal server error"}

        @click.command()
        @format_option
        def test_cmd() -> None:
            handle_response_error(response, "Upload")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "human"])
        assert result.exit_code == 1
        assert "Upload failed (500): Internal server error" in result.output

    def test_human_mode_includes_token_for_401(self) -> None:
        """In human mode, 401 errors include masked token."""
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"detail": "Unauthorized"}

        @click.command()
        @format_option
        def test_cmd() -> None:
            handle_response_error(response, "Download", "mgp_secret123456")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "human"])
        # 401 should produce exit code 4 (AUTH_ERROR)
        assert result.exit_code == 4
        assert "token: ********3456" in result.output

    def test_json_mode_outputs_error_envelope(self) -> None:
        """In JSON mode, outputs JSON error envelope to stderr."""
        response = Mock()
        response.status_code = 404
        response.json.return_value = {"detail": "Resource not found"}

        @click.command()
        @format_option
        def test_cmd() -> None:
            handle_response_error(response, "Info")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        # 404 should produce exit code 3 (NOT_FOUND)
        assert result.exit_code == 3

        output = json.loads(result.output)
        assert output["status"] == "error"
        assert output["error"]["code"] == "NOT_FOUND"
        assert output["error"]["message"] == "Resource not found"

    def test_json_mode_401_maps_to_unauthorized(self) -> None:
        """In JSON mode, 401 maps to UNAUTHORIZED error code."""
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"detail": "Token expired"}

        @click.command()
        @format_option
        def test_cmd() -> None:
            handle_response_error(response, "Upload")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        output = json.loads(result.output)
        assert output["error"]["code"] == "UNAUTHORIZED"
        assert output["error"]["message"] == "Token expired"

    def test_json_mode_403_maps_to_forbidden(self) -> None:
        """In JSON mode, 403 maps to FORBIDDEN error code."""
        response = Mock()
        response.status_code = 403
        response.json.return_value = {"detail": "Insufficient permissions"}

        @click.command()
        @format_option
        def test_cmd() -> None:
            handle_response_error(response, "Delete")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        output = json.loads(result.output)
        assert output["error"]["code"] == "FORBIDDEN"
        assert output["error"]["message"] == "Insufficient permissions"

    def test_json_mode_500_maps_to_server_error(self) -> None:
        """In JSON mode, 500 maps to SERVER_ERROR error code."""
        response = Mock()
        response.status_code = 500
        response.json.return_value = {"detail": "Database connection failed"}

        @click.command()
        @format_option
        def test_cmd() -> None:
            handle_response_error(response, "Query")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        output = json.loads(result.output)
        assert output["error"]["code"] == "SERVER_ERROR"
        assert output["error"]["message"] == "Database connection failed"

    def test_json_mode_malformed_json_falls_back_to_text(self) -> None:
        """In JSON mode, malformed JSON response falls back to text."""
        response = Mock()
        response.status_code = 502
        response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
        response.text = "Bad Gateway: upstream server unavailable"

        @click.command()
        @format_option
        def test_cmd() -> None:
            handle_response_error(response, "Proxy")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        assert result.exit_code == 1

        output = json.loads(result.output)
        assert output["status"] == "error"
        assert output["error"]["code"] == "SERVER_ERROR"
        assert output["error"]["message"] == "Bad Gateway: upstream server unavailable"

    def test_json_mode_no_detail_key_falls_back_to_text(self) -> None:
        """In JSON mode, missing detail key falls back to response text."""
        response = Mock()
        response.status_code = 409
        response.json.return_value = {"error": "ConflictError"}
        response.text = "Resource already exists"

        @click.command()
        @format_option
        def test_cmd() -> None:
            handle_response_error(response, "Create")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        output = json.loads(result.output)
        assert output["error"]["code"] == "CONFLICT"
        assert output["error"]["message"] == "Resource already exists"

    def test_human_mode_malformed_json_falls_back_to_text(self) -> None:
        """In human mode, malformed JSON response falls back to text."""
        response = Mock()
        response.status_code = 503
        response.json.side_effect = ValueError("No JSON")
        response.text = "Service Unavailable"

        @click.command()
        @format_option
        def test_cmd() -> None:
            handle_response_error(response, "Health")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "human"])
        assert result.exit_code == 1
        assert "Health failed (503): Service Unavailable" in result.output
