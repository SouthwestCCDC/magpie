"""Subprocess utilities for running magpie-ctl commands asynchronously.

This module provides async helpers for running magpie-ctl commands as subprocesses,
allowing the server to offload blocking filesystem operations without blocking the
FastAPI event loop.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


class CtlCommandError(Exception):
    """Raised when a magpie-ctl command fails."""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


async def run_ctl_command(cmd: list[str], timeout: float = 300.0) -> dict[str, Any]:
    """Run a magpie-ctl command and return parsed JSON output.

    This function runs magpie-ctl commands asynchronously, allowing the FastAPI
    server to handle other requests while the filesystem operation runs.

    Args:
        cmd: Command and arguments to run (e.g., ["magpie-ctl", "gc", "--json-output"]).
        timeout: Maximum time to wait for command completion (seconds). Default 5 minutes.

    Returns:
        Parsed JSON output from the command.

    Raises:
        CtlCommandError: If the command fails or returns invalid JSON.
        asyncio.TimeoutError: If command exceeds timeout.

    Example:
        >>> result = await run_ctl_command(["magpie-ctl", "gc", "--json-output"])
        >>> print(result["blobs_removed"])
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise CtlCommandError(f"Command not found: {cmd[0]}") from e
    except OSError as e:
        raise CtlCommandError(f"Failed to execute command: {e}") from e

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    stdout_text = stdout.decode() if stdout else ""
    stderr_text = stderr.decode() if stderr else ""

    if proc.returncode != 0:
        # Try to extract error message from JSON output
        error_message = stderr_text
        if stdout_text:
            try:
                error_data = json.loads(stdout_text)
                if "error" in error_data:
                    error_message = error_data["error"]
            except json.JSONDecodeError:
                pass

        raise CtlCommandError(
            f"magpie-ctl command failed with exit code {proc.returncode}: {error_message}",
            stderr=stderr_text,
        )

    if not stdout_text:
        raise CtlCommandError("magpie-ctl command returned no output")

    try:
        return json.loads(stdout_text)
    except json.JSONDecodeError as e:
        raise CtlCommandError(f"Invalid JSON from magpie-ctl: {e}") from e
