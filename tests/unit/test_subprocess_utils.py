"""Unit tests for subprocess utilities."""

from __future__ import annotations

import asyncio

import pytest

from magpie.server.subprocess_utils import CtlCommandError, run_ctl_command


class TestCtlCommandError:
    """Tests for CtlCommandError exception."""

    def test_error_message(self) -> None:
        """CtlCommandError stores message correctly."""
        error = CtlCommandError("Test error")
        assert str(error) == "Test error"

    def test_error_with_stderr(self) -> None:
        """CtlCommandError stores stderr correctly."""
        error = CtlCommandError("Test error", stderr="stderr content")
        assert error.stderr == "stderr content"


class TestRunCtlCommand:
    """Tests for run_ctl_command function."""

    @pytest.mark.asyncio
    async def test_successful_command_returns_dict(self) -> None:
        """Successful command returns parsed JSON dict."""
        result = await run_ctl_command(["echo", '{"key": "value"}'])
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_command_not_found_raises_error(self) -> None:
        """Non-existent command raises CtlCommandError."""
        with pytest.raises(CtlCommandError):
            await run_ctl_command(["nonexistent-command-xyz123"])

    @pytest.mark.asyncio
    async def test_command_failure_raises_error(self) -> None:
        """Command that exits non-zero raises CtlCommandError."""
        with pytest.raises(CtlCommandError) as exc_info:
            await run_ctl_command(["sh", "-c", "exit 1"])
        assert "exit code 1" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_json_raises_error(self) -> None:
        """Command that outputs invalid JSON raises CtlCommandError."""
        with pytest.raises(CtlCommandError) as exc_info:
            await run_ctl_command(["echo", "not valid json"])
        assert "Invalid JSON" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_empty_output_raises_error(self) -> None:
        """Command with no output raises CtlCommandError."""
        with pytest.raises(CtlCommandError) as exc_info:
            await run_ctl_command(["sh", "-c", "true"])
        assert "no output" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_timeout_raises_error(self) -> None:
        """Command that exceeds timeout raises TimeoutError."""
        with pytest.raises(asyncio.TimeoutError):
            await run_ctl_command(["sleep", "10"], timeout=0.1)

    @pytest.mark.asyncio
    async def test_json_error_in_stdout_extracted(self) -> None:
        """JSON error message in stdout is extracted."""
        with pytest.raises(CtlCommandError) as exc_info:
            await run_ctl_command(["sh", "-c", 'echo \'{"error": "specific error"}\'; exit 1'])
        assert "specific error" in str(exc_info.value)
