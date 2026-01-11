"""Progress bar utilities for CLI file transfers."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from rich.progress import Progress, TaskID


def is_tty() -> bool:
    """Check if stdout is a TTY (interactive terminal).

    Returns:
        True if stdout is a TTY, False otherwise.
    """
    return sys.stdout.isatty()


@contextmanager
def transfer_progress(
    description: str,
    total_bytes: int,
    quiet: bool = False,
) -> Generator[tuple["Progress | None", "TaskID | None"], None, None]:
    """Context manager for file transfer progress display.

    Only shows progress bar in TTY environments and when not quiet.

    Args:
        description: Description to show (e.g., "Uploading", "Downloading").
        total_bytes: Total number of bytes to transfer.
        quiet: If True, suppress progress output entirely.

    Yields:
        Tuple of (Progress instance or None, TaskID or None).
        Both are None when progress display is suppressed.
    """
    if quiet or not is_tty():
        yield None, None
        return

    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    )

    with progress:
        task_id = progress.add_task(description, total=total_bytes)
        yield progress, task_id
