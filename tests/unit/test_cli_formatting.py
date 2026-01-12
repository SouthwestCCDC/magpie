"""Unit tests for CLI formatting module."""

from __future__ import annotations

import json

import click
from click.testing import CliRunner

from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    OutputFormat,
    format_option,
    get_output_format,
    http_status_to_error_code,
    is_json_output,
    output_error,
    output_result,
)


class TestOutputFormat:
    """Tests for OutputFormat enum."""

    def test_output_format_values(self) -> None:
        """Test enum values are strings."""
        assert OutputFormat.HUMAN.value == "human"
        assert OutputFormat.JSON.value == "json"

    def test_output_format_str_subclass(self) -> None:
        """Test OutputFormat is a string enum."""
        assert isinstance(OutputFormat.HUMAN, str)
        assert OutputFormat.HUMAN == "human"


class TestCommandResult:
    """Tests for CommandResult dataclass."""

    def test_command_result_creation(self) -> None:
        """Test basic CommandResult creation."""
        result = CommandResult(
            data={"key": "value"},
            human_output="Human readable text",
        )
        assert result.data == {"key": "value"}
        assert result.human_output == "Human readable text"

    def test_command_result_with_list_data(self) -> None:
        """Test CommandResult with list data."""
        result = CommandResult(
            data={"items": [1, 2, 3]},
            human_output="Items: 1, 2, 3",
        )
        assert result.data["items"] == [1, 2, 3]

    def test_command_result_with_nested_data(self) -> None:
        """Test CommandResult with nested data structures."""
        result = CommandResult(
            data={
                "artifact": "test/path",
                "versions": [
                    {"version": "@abc123", "tags": ["latest"]},
                    {"version": "@def456", "tags": ["stable"]},
                ],
            },
            human_output="test output",
        )
        assert len(result.data["versions"]) == 2
        assert result.data["versions"][0]["tags"] == ["latest"]


class TestFormatOption:
    """Tests for format_option decorator."""

    def test_format_option_adds_option(self) -> None:
        """Test that format_option decorator adds --format option."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            pass

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--help"])
        assert "--format" in result.output
        assert "human" in result.output
        assert "json" in result.output

    def test_format_option_default_human(self) -> None:
        """Test that format defaults to human."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            ctx = click.get_current_context()
            fmt = ctx.obj.get("output_format")
            click.echo(f"Format: {fmt.value}")

        runner = CliRunner()
        result = runner.invoke(test_cmd)
        assert result.exit_code == 0
        assert "Format: human" in result.output

    def test_format_option_accepts_json(self) -> None:
        """Test that --format json sets JSON format."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            ctx = click.get_current_context()
            fmt = ctx.obj.get("output_format")
            click.echo(f"Format: {fmt.value}")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        assert result.exit_code == 0
        assert "Format: json" in result.output

    def test_format_option_case_insensitive(self) -> None:
        """Test that --format option is case insensitive."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            ctx = click.get_current_context()
            fmt = ctx.obj.get("output_format")
            click.echo(f"Format: {fmt.value}")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "JSON"])
        assert result.exit_code == 0
        assert "Format: json" in result.output

    def test_format_option_invalid_value(self) -> None:
        """Test that invalid format value is rejected."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            pass

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "invalid"])
        assert result.exit_code != 0
        assert "Invalid value" in result.output or "invalid" in result.output.lower()


class TestGetOutputFormat:
    """Tests for get_output_format function."""

    def test_get_output_format_no_context(self) -> None:
        """Test default when no context available."""
        assert get_output_format() == OutputFormat.HUMAN

    def test_get_output_format_from_dict(self) -> None:
        """Test getting format from dict context."""

        @click.command()
        @click.pass_context
        def test_cmd(ctx: click.Context) -> None:
            ctx.ensure_object(dict)
            ctx.obj["output_format"] = OutputFormat.JSON
            fmt = get_output_format()
            click.echo(f"Format: {fmt.value}")

        runner = CliRunner()
        result = runner.invoke(test_cmd)
        assert "Format: json" in result.output

    def test_get_output_format_from_object(self) -> None:
        """Test getting format from object with output_format attribute."""

        class MockContext:
            output_format = OutputFormat.JSON

        @click.command()
        @click.pass_context
        def test_cmd(ctx: click.Context) -> None:
            ctx.obj = MockContext()
            fmt = get_output_format()
            click.echo(f"Format: {fmt.value}")

        runner = CliRunner()
        result = runner.invoke(test_cmd)
        assert "Format: json" in result.output


class TestIsJsonOutput:
    """Tests for is_json_output function."""

    def test_is_json_output_false_by_default(self) -> None:
        """Test that is_json_output returns False by default."""
        assert is_json_output() is False

    def test_is_json_output_true_when_json(self) -> None:
        """Test that is_json_output returns True when JSON format."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            result = is_json_output()
            click.echo(f"JSON: {result}")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        assert "JSON: True" in result.output

    def test_is_json_output_false_when_human(self) -> None:
        """Test that is_json_output returns False when human format."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            result = is_json_output()
            click.echo(f"JSON: {result}")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "human"])
        assert "JSON: False" in result.output


class TestOutputResult:
    """Tests for output_result function."""

    def test_output_result_human_format(self) -> None:
        """Test output_result in human format."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            output_result(
                CommandResult(
                    data={"key": "value"},
                    human_output="Human readable output",
                )
            )

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "human"])
        assert result.exit_code == 0
        assert result.output.strip() == "Human readable output"

    def test_output_result_json_format(self) -> None:
        """Test output_result in JSON format."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            output_result(
                CommandResult(
                    data={"key": "value"},
                    human_output="Human readable output",
                )
            )

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        assert result.exit_code == 0

        # Parse the JSON output
        output = json.loads(result.output)
        assert output["status"] == "ok"
        assert output["data"] == {"key": "value"}

    def test_output_result_json_format_with_nested_data(self) -> None:
        """Test output_result JSON with nested data structures."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            output_result(
                CommandResult(
                    data={
                        "artifact": "test/path",
                        "versions": [
                            {"version": "@abc123", "tags": ["latest"]},
                        ],
                    },
                    human_output="",
                )
            )

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        output = json.loads(result.output)
        assert output["status"] == "ok"
        assert output["data"]["artifact"] == "test/path"
        assert len(output["data"]["versions"]) == 1


class TestOutputError:
    """Tests for output_error function."""

    def test_output_error_human_format(self) -> None:
        """Test output_error in human format - exit code is non-zero."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            output_error(ErrorCode.NOT_FOUND, "Resource not found")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "human"])
        assert result.exit_code == 1
        # In Click 8.x+, stderr and stdout are mixed by default
        # Just check the error message appears
        assert "Error: Resource not found" in result.output

    def test_output_error_json_format(self) -> None:
        """Test output_error in JSON format - outputs JSON envelope."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            output_error(ErrorCode.NOT_FOUND, "Resource not found")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        assert result.exit_code == 1

        # Parse the JSON error output (mixed into output in Click 8.x+)
        output = json.loads(result.output)
        assert output["status"] == "error"
        assert output["error"]["code"] == "NOT_FOUND"
        assert output["error"]["message"] == "Resource not found"

    def test_output_error_custom_exit_code(self) -> None:
        """Test output_error with custom exit code."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            output_error(ErrorCode.VALIDATION_ERROR, "Invalid input", exit_code=2)

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "human"])
        assert result.exit_code == 2

    def test_output_error_json_envelope_structure(self) -> None:
        """Test that JSON error has correct envelope structure."""

        @click.command()
        @format_option
        def test_cmd() -> None:
            output_error(ErrorCode.SERVER_ERROR, "Server failed")

        runner = CliRunner()
        result = runner.invoke(test_cmd, ["--format", "json"])
        output = json.loads(result.output)
        assert "status" in output
        assert output["status"] == "error"
        assert "error" in output
        assert "code" in output["error"]
        assert "message" in output["error"]


class TestErrorCode:
    """Tests for ErrorCode constants."""

    def test_error_code_values(self) -> None:
        """Test all error codes have expected values."""
        assert ErrorCode.NOT_FOUND == "NOT_FOUND"
        assert ErrorCode.UNAUTHORIZED == "UNAUTHORIZED"
        assert ErrorCode.FORBIDDEN == "FORBIDDEN"
        assert ErrorCode.CONFLICT == "CONFLICT"
        assert ErrorCode.VALIDATION_ERROR == "VALIDATION_ERROR"
        assert ErrorCode.SERVER_ERROR == "SERVER_ERROR"
        assert ErrorCode.NETWORK_ERROR == "NETWORK_ERROR"
        assert ErrorCode.IO_ERROR == "IO_ERROR"
        assert ErrorCode.CONFIG_ERROR == "CONFIG_ERROR"


class TestHttpStatusToErrorCode:
    """Tests for http_status_to_error_code function."""

    def test_maps_400_to_validation_error(self) -> None:
        """Test 400 maps to VALIDATION_ERROR."""
        assert http_status_to_error_code(400) == ErrorCode.VALIDATION_ERROR

    def test_maps_401_to_unauthorized(self) -> None:
        """Test 401 maps to UNAUTHORIZED."""
        assert http_status_to_error_code(401) == ErrorCode.UNAUTHORIZED

    def test_maps_403_to_forbidden(self) -> None:
        """Test 403 maps to FORBIDDEN."""
        assert http_status_to_error_code(403) == ErrorCode.FORBIDDEN

    def test_maps_404_to_not_found(self) -> None:
        """Test 404 maps to NOT_FOUND."""
        assert http_status_to_error_code(404) == ErrorCode.NOT_FOUND

    def test_maps_409_to_conflict(self) -> None:
        """Test 409 maps to CONFLICT."""
        assert http_status_to_error_code(409) == ErrorCode.CONFLICT

    def test_maps_5xx_to_server_error(self) -> None:
        """Test 5xx status codes map to SERVER_ERROR."""
        assert http_status_to_error_code(500) == ErrorCode.SERVER_ERROR
        assert http_status_to_error_code(502) == ErrorCode.SERVER_ERROR
        assert http_status_to_error_code(503) == ErrorCode.SERVER_ERROR

    def test_maps_unknown_to_validation_error(self) -> None:
        """Test unknown status codes map to VALIDATION_ERROR."""
        assert http_status_to_error_code(418) == ErrorCode.VALIDATION_ERROR
