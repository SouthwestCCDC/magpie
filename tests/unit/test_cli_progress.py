"""Unit tests for CLI progress bar behavior."""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock, patch

from magpie.cli.commands.push import ProgressFileWrapper
from magpie.cli.progress import count_progress, is_tty, processing_spinner, transfer_progress


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


class TestCountProgress:
    """Tests for count_progress context manager."""

    def test_progress_suppressed_with_quiet_flag(self) -> None:
        """Test that progress is suppressed when quiet=True."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            with count_progress("Scanning", 100, quiet=True) as (progress, task_id):
                assert progress is None
                assert task_id is None

    def test_progress_suppressed_when_not_tty(self) -> None:
        """Test that progress is suppressed when stdout is not a TTY."""
        with patch("magpie.cli.progress.is_tty", return_value=False):
            with count_progress("Scanning", 100, quiet=False) as (progress, task_id):
                assert progress is None
                assert task_id is None

    def test_progress_displayed_in_tty_mode(self) -> None:
        """Test that progress is displayed in normal TTY conditions."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            with count_progress("Scanning", 100, quiet=False) as (progress, task_id):
                # Progress should be a rich Progress instance (not None)
                assert progress is not None
                assert task_id is not None
                # Verify we can update the progress (basic functionality check)
                progress.update(task_id, advance=10)

    def test_progress_description_passed_correctly(self) -> None:
        """Test that description is passed to progress bar correctly."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            with count_progress("Scanning artifacts", 50, quiet=False) as (progress, task_id):
                assert progress is not None
                task = progress.tasks[task_id]
                assert task.description == "Scanning artifacts"
                assert task.total == 50

    def test_progress_with_none_total(self) -> None:
        """Test that count_progress works with indeterminate total."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            with count_progress("Processing", total=None, quiet=False) as (progress, task_id):
                assert progress is not None
                assert task_id is not None
                task = progress.tasks[task_id]
                assert task.total is None


class TestProcessingSpinner:
    """Tests for processing_spinner context manager."""

    def test_spinner_suppressed_with_quiet_flag(self) -> None:
        """Test that spinner is suppressed when quiet=True."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            # Should not raise and should do nothing
            with processing_spinner("Processing...", quiet=True):
                pass

    def test_spinner_suppressed_when_not_tty(self) -> None:
        """Test that spinner is suppressed when stdout is not a TTY."""
        with patch("magpie.cli.progress.is_tty", return_value=False):
            # Should not raise and should do nothing
            with processing_spinner("Processing...", quiet=False):
                pass

    def test_spinner_displayed_in_tty_mode(self) -> None:
        """Test that spinner is displayed in normal TTY conditions."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            # Should not raise - the spinner context manager runs
            with processing_spinner("Processing...", quiet=False):
                pass

    def test_spinner_with_custom_message(self) -> None:
        """Test that spinner accepts custom message."""
        with patch("magpie.cli.progress.is_tty", return_value=True):
            # Should not raise with custom message
            with processing_spinner("Finalizing upload...", quiet=False):
                pass


class TestProgressFileWrapper:
    """Tests for ProgressFileWrapper class."""

    def test_read_updates_progress(self) -> None:
        """Test that reading updates the progress bar."""
        data = b"Hello, World!"
        file = io.BytesIO(data)
        progress = MagicMock()
        task_id = MagicMock()

        wrapper = ProgressFileWrapper(file, len(data), progress, task_id)
        result = wrapper.read(5)

        assert result == b"Hello"
        progress.update.assert_called_once_with(task_id, advance=5)

    def test_read_without_progress(self) -> None:
        """Test that reading works when progress is None."""
        data = b"Hello, World!"
        file = io.BytesIO(data)

        wrapper = ProgressFileWrapper(file, len(data), None, None)
        result = wrapper.read(5)

        assert result == b"Hello"

    def test_callback_fired_when_upload_complete(self) -> None:
        """Test that on_upload_complete callback is fired when all bytes read."""
        data = b"Hello"
        file = io.BytesIO(data)
        callback = MagicMock()

        wrapper = ProgressFileWrapper(file, len(data), None, None, on_upload_complete=callback)

        # Read all bytes
        wrapper.read(-1)

        callback.assert_called_once()

    def test_callback_fired_only_once(self) -> None:
        """Test that callback is only fired once even if read multiple times."""
        data = b"Hello, World!"
        file = io.BytesIO(data)
        callback = MagicMock()

        wrapper = ProgressFileWrapper(file, len(data), None, None, on_upload_complete=callback)

        # Read all bytes in multiple calls
        wrapper.read(5)  # "Hello"
        wrapper.read(8)  # ", World!"
        wrapper.read()  # Empty read after EOF

        # Callback should only be fired once
        callback.assert_called_once()

    def test_callback_not_fired_before_complete(self) -> None:
        """Test that callback is not fired until all bytes are read."""
        data = b"Hello, World!"
        file = io.BytesIO(data)
        callback = MagicMock()

        wrapper = ProgressFileWrapper(file, len(data), None, None, on_upload_complete=callback)

        # Read partial data
        wrapper.read(5)

        callback.assert_not_called()

    def test_seek_resets_bytes_read(self) -> None:
        """Test that seek() resets _bytes_read to match file position."""
        data = b"Hello, World!"
        file = io.BytesIO(data)
        callback = MagicMock()

        wrapper = ProgressFileWrapper(file, len(data), None, None, on_upload_complete=callback)

        # Read all bytes
        wrapper.read(-1)
        callback.assert_called_once()

        # Seek back to beginning
        wrapper.seek(0)

        # _bytes_read should now be 0, matching file position
        assert wrapper._bytes_read == 0

    def test_seek_allows_reread_without_duplicate_callback(self) -> None:
        """Test that seeking back and re-reading doesn't fire callback again."""
        data = b"Hello"
        file = io.BytesIO(data)
        callback = MagicMock()

        wrapper = ProgressFileWrapper(file, len(data), None, None, on_upload_complete=callback)

        # Read all bytes - callback fires
        wrapper.read(-1)
        assert callback.call_count == 1

        # Seek back to beginning
        wrapper.seek(0)

        # Read all bytes again - callback should NOT fire again
        wrapper.read(-1)
        assert callback.call_count == 1  # Still only 1 call

    def test_tell_returns_current_position(self) -> None:
        """Test that tell() returns current file position."""
        data = b"Hello, World!"
        file = io.BytesIO(data)

        wrapper = ProgressFileWrapper(file, len(data), None, None)

        assert wrapper.tell() == 0
        wrapper.read(5)
        assert wrapper.tell() == 5
