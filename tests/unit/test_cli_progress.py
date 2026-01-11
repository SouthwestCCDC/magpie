"""Unit tests for CLI progress bar behavior."""

from __future__ import annotations

import sys
from unittest.mock import patch

from magpie.cli.progress import is_tty, transfer_progress


class TestIsTty:
    """Tests for is_tty function."""

    def test_is_tty_returns_true_when_stdout_is_tty(self) -> None:
        """Test that is_tty returns True when stdout.isatty() is True."""
        with patch.object(sys.stdout, "isatty", return_value=True):
            assert is_tty() is True

    def test_is_tty_returns_false_when_stdout_is_not_tty(self) -> None:
        """Test that is_tty returns False when stdout.isatty() is False."""
        with patch.object(sys.stdout, "isatty", return_value=False):
            assert is_tty() is False


class TestTransferProgress:
    """Tests for transfer_progress context manager."""

    def test_progress_suppressed_with_quiet_flag(self) -> None:
        """Test that progress is suppressed when quiet=True."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            with transfer_progress("Test", 1000, quiet=True) as (progress, task_id):
                assert progress is None
                assert task_id is None

    def test_progress_suppressed_when_not_tty(self) -> None:
        """Test that progress is suppressed when stdout is not a TTY."""
        with patch("magpie.cli.progress.is_tty", return_value=False):
            with transfer_progress("Test", 1000, quiet=False) as (progress, task_id):
                assert progress is None
                assert task_id is None

    def test_progress_displayed_in_tty_mode(self) -> None:
        """Test that progress is displayed in normal TTY conditions."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            with transfer_progress("Test", 1000, quiet=False) as (progress, task_id):
                # Progress should be a rich Progress instance (not None)
                assert progress is not None
                assert task_id is not None
                # Verify we can update the progress (basic functionality check)
                progress.update(task_id, advance=100)

    def test_progress_suppressed_when_both_quiet_and_not_tty(self) -> None:
        """Test that progress is suppressed when both quiet=True and not TTY."""
        with patch("magpie.cli.progress.is_tty", return_value=False):
            with transfer_progress("Test", 1000, quiet=True) as (progress, task_id):
                assert progress is None
                assert task_id is None

    def test_progress_description_passed_correctly(self) -> None:
        """Test that description is passed to progress bar correctly."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            with transfer_progress("Uploading", 5000, quiet=False) as (progress, task_id):
                assert progress is not None
                # Get the task to verify description
                task = progress.tasks[task_id]
                assert task.description == "Uploading"
                assert task.total == 5000

    def test_progress_total_bytes_passed_correctly(self) -> None:
        """Test that total_bytes is passed to progress bar correctly."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            with transfer_progress("Downloading", 123456, quiet=False) as (progress, task_id):
                assert progress is not None
                task = progress.tasks[task_id]
                assert task.total == 123456
