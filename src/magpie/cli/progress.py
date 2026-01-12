"""Progress bar utilities for CLI operations."""

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


@contextmanager
def count_progress(
    description: str,
    total: int | None = None,
    quiet: bool = False,
) -> Generator[tuple["Progress | None", "TaskID | None"], None, None]:
    """Context manager for count-based progress display.

    Shows a progress bar for iterating over items (not byte transfers).
    Only shows progress bar in TTY environments and when not quiet.

    Args:
        description: Description to show (e.g., "Scanning artifacts").
        total: Total number of items, or None for indeterminate progress.
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
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )

    with progress:
        task_id = progress.add_task(description, total=total)
        yield progress, task_id


@contextmanager
def processing_spinner(
    message: str = "Processing...",
    quiet: bool = False,
) -> Generator[None, None, None]:
    """Context manager for displaying a processing spinner.

    Shows a spinner with a message while waiting for an operation to complete.
    Only shows in TTY environments and when not quiet.

    Args:
        message: Message to display (e.g., "Processing...", "Finalizing...").
        quiet: If True, suppress spinner output entirely.

    Yields:
        Nothing. The spinner displays until the context exits.
    """
    if quiet or not is_tty():
        yield
        return

    from rich.console import Console
    from rich.live import Live
    from rich.spinner import Spinner

    console = Console()
    spinner = Spinner("dots", text=f"[bold blue]{message}")

    with Live(spinner, console=console, refresh_per_second=10):
        yield
