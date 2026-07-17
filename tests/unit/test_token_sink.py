"""Unit tests for admin token delivery sinks."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from magpie.auth.token_sink import TokenSinkError, deliver_admin_token
from magpie.config import MagpieSettings

TOKEN = "mgp_ADMIN_test_token_value_should_never_leak"  # nosec B105 - test fixture, not a real secret


@pytest.fixture
def base_settings(tmp_path: Path) -> MagpieSettings:
    """Create test MagpieSettings with temporary paths and no sink configured."""
    return MagpieSettings(
        storage_path=tmp_path / "storage",
        database_path=tmp_path / "magpie.db",
    )


class TestNoSinkConfigured:
    """Fail-closed: an unset sink is a hard failure, never a silent default."""

    def test_none_sink_raises(self, base_settings: MagpieSettings) -> None:
        with pytest.raises(TokenSinkError, match="MAGPIE_ADMIN_TOKEN_SINK is not set"):
            deliver_admin_token(TOKEN, base_settings, action="init")


class TestFileSink:
    """Tests for sink=file."""

    def test_writes_token_to_file(self, tmp_path: Path, base_settings: MagpieSettings) -> None:
        target = tmp_path / "admin-token"
        settings = base_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": target}
        )

        deliver_admin_token(TOKEN, settings, action="init")

        assert target.read_text().strip() == TOKEN

    def test_file_is_0600(self, tmp_path: Path, base_settings: MagpieSettings) -> None:
        target = tmp_path / "admin-token"
        settings = base_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": target}
        )

        deliver_admin_token(TOKEN, settings, action="init")

        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600

    def test_file_0600_even_with_permissive_umask(
        self, tmp_path: Path, base_settings: MagpieSettings
    ) -> None:
        target = tmp_path / "admin-token"
        settings = base_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": target}
        )

        import os

        old_umask = os.umask(0o000)
        try:
            deliver_admin_token(TOKEN, settings, action="init")
        finally:
            os.umask(old_umask)

        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600

    def test_creates_parent_directories(
        self, tmp_path: Path, base_settings: MagpieSettings
    ) -> None:
        target = tmp_path / "nested" / "dir" / "admin-token"
        settings = base_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": target}
        )

        deliver_admin_token(TOKEN, settings, action="init")

        assert target.read_text().strip() == TOKEN

    def test_overwrites_existing_file(self, tmp_path: Path, base_settings: MagpieSettings) -> None:
        target = tmp_path / "admin-token"
        target.write_text("stale-value\n")
        settings = base_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": target}
        )

        deliver_admin_token(TOKEN, settings, action="rotate")

        assert target.read_text().strip() == TOKEN

    def test_unwritable_path_raises_token_sink_error(
        self, tmp_path: Path, base_settings: MagpieSettings
    ) -> None:
        # A file component in the path makes it impossible to create the
        # parent directory, guaranteeing a write failure.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        target = blocker / "admin-token"
        settings = base_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": target}
        )

        with pytest.raises(TokenSinkError, match="admin token file sink failed to write"):
            deliver_admin_token(TOKEN, settings, action="init")

    def test_symlink_at_target_path_is_rejected(
        self, tmp_path: Path, base_settings: MagpieSettings
    ) -> None:
        """A pre-placed symlink at the configured path must not be followed.

        Otherwise an attacker who can create a symlink at the target path
        (but not write the target's actual destination directly) could
        redirect the plaintext token to a location of their choosing.
        """
        real_target = tmp_path / "elsewhere" / "not-the-real-path"
        real_target.parent.mkdir(parents=True)
        symlink_path = tmp_path / "admin-token"
        symlink_path.symlink_to(real_target)
        settings = base_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": symlink_path}
        )

        with pytest.raises(TokenSinkError, match="admin token file sink failed to write"):
            deliver_admin_token(TOKEN, settings, action="init")

        assert not real_target.exists()

    def test_default_file_path_derived_from_database_path(self, tmp_path: Path) -> None:
        settings = MagpieSettings(
            storage_path=tmp_path / "storage",
            database_path=tmp_path / "sub" / "magpie.db",
            admin_token_sink="file",
        )

        assert settings.admin_token_sink_file_path == tmp_path / "sub" / "admin-token"


class TestExecSink:
    """Tests for sink=exec."""

    def test_token_delivered_via_stdin(self, tmp_path: Path, base_settings: MagpieSettings) -> None:
        out_file = tmp_path / "captured-stdin"
        # Use the current interpreter so this works regardless of PATH setup.
        command = (
            f"{sys.executable} -c \"import sys; open('{out_file}', 'w').write(sys.stdin.read())\""
        )
        settings = base_settings.model_copy(
            update={"admin_token_sink": "exec", "admin_token_sink_exec_command": command}
        )

        deliver_admin_token(TOKEN, settings, action="init")

        assert out_file.read_text() == TOKEN

    def test_token_not_in_argv_or_env(self, tmp_path: Path, base_settings: MagpieSettings) -> None:
        """The token must never appear in the child's argv or environment."""
        out_file = tmp_path / "captured-argv-env"
        script = (
            "import sys, os, json;"
            f"open({str(out_file)!r}, 'w').write(json.dumps({{'argv': sys.argv, 'env': dict(os.environ)}}))"
        )
        command = f"{sys.executable} -c {script!r}"
        settings = base_settings.model_copy(
            update={"admin_token_sink": "exec", "admin_token_sink_exec_command": command}
        )

        deliver_admin_token(TOKEN, settings, action="init")

        captured = out_file.read_text()
        assert TOKEN not in captured

    def test_nonzero_exit_raises_token_sink_error(
        self, tmp_path: Path, base_settings: MagpieSettings
    ) -> None:
        command = f'{sys.executable} -c "import sys; sys.exit(3)"'
        settings = base_settings.model_copy(
            update={"admin_token_sink": "exec", "admin_token_sink_exec_command": command}
        )

        with pytest.raises(TokenSinkError, match="exited with status 3"):
            deliver_admin_token(TOKEN, settings, action="init")

    def test_nonzero_exit_error_does_not_include_token(
        self, tmp_path: Path, base_settings: MagpieSettings
    ) -> None:
        command = f"{sys.executable} -c \"import sys; sys.stderr.write('boom'); sys.exit(1)\""
        settings = base_settings.model_copy(
            update={"admin_token_sink": "exec", "admin_token_sink_exec_command": command}
        )

        with pytest.raises(TokenSinkError) as exc_info:
            deliver_admin_token(TOKEN, settings, action="init")

        assert TOKEN not in str(exc_info.value)

    def test_nonzero_exit_does_not_leak_echoed_stdin(
        self, tmp_path: Path, base_settings: MagpieSettings
    ) -> None:
        """A command that echoes its stdin (the token) to stderr must not leak it.

        This is the realistic leak scenario: an operator-configured command
        that reflects its input back on failure (common for debugging) must
        never cause the token to end up in TokenSinkError's message, which
        callers print/log.
        """
        command = (
            f'{sys.executable} -c "import sys; sys.stderr.write(sys.stdin.read()); sys.exit(1)"'
        )
        settings = base_settings.model_copy(
            update={"admin_token_sink": "exec", "admin_token_sink_exec_command": command}
        )

        with pytest.raises(TokenSinkError) as exc_info:
            deliver_admin_token(TOKEN, settings, action="init")

        assert TOKEN not in str(exc_info.value)

    def test_command_not_found_raises_token_sink_error(self, base_settings: MagpieSettings) -> None:
        settings = base_settings.model_copy(
            update={
                "admin_token_sink": "exec",
                "admin_token_sink_exec_command": "definitely-not-a-real-command-xyz",
            }
        )

        with pytest.raises(TokenSinkError, match="failed to run"):
            deliver_admin_token(TOKEN, settings, action="init")

    def test_timeout_raises_token_sink_error(self, base_settings: MagpieSettings) -> None:
        command = f'{sys.executable} -c "import time; time.sleep(5)"'
        settings = base_settings.model_copy(
            update={
                "admin_token_sink": "exec",
                "admin_token_sink_exec_command": command,
                "admin_token_sink_exec_timeout": 0.1,
            }
        )

        with pytest.raises(TokenSinkError, match="failed to run"):
            deliver_admin_token(TOKEN, settings, action="init")

    def test_unset_command_raises(self, base_settings: MagpieSettings) -> None:
        settings = base_settings.model_copy(update={"admin_token_sink": "exec"})

        with pytest.raises(TokenSinkError, match="MAGPIE_ADMIN_TOKEN_SINK_EXEC_COMMAND"):
            deliver_admin_token(TOKEN, settings, action="init")

    def test_unparsable_command_raises(self, base_settings: MagpieSettings) -> None:
        # An unterminated quote is invalid shlex syntax.
        settings = base_settings.model_copy(
            update={"admin_token_sink": "exec", "admin_token_sink_exec_command": 'unterminated "'}
        )

        with pytest.raises(TokenSinkError, match="could not be parsed"):
            deliver_admin_token(TOKEN, settings, action="init")

    def test_whitespace_only_command_raises(self, base_settings: MagpieSettings) -> None:
        settings = base_settings.model_copy(
            update={"admin_token_sink": "exec", "admin_token_sink_exec_command": "   "}
        )

        with pytest.raises(TokenSinkError, match="empty after parsing"):
            deliver_admin_token(TOKEN, settings, action="init")


class TestDiscardSink:
    """Tests for sink=discard."""

    def test_discard_does_not_raise(self, base_settings: MagpieSettings) -> None:
        settings = base_settings.model_copy(update={"admin_token_sink": "discard"})

        deliver_admin_token(TOKEN, settings, action="init")  # should not raise

    def test_discard_does_not_write_any_file(
        self, tmp_path: Path, base_settings: MagpieSettings
    ) -> None:
        settings = base_settings.model_copy(update={"admin_token_sink": "discard"})

        deliver_admin_token(TOKEN, settings, action="init")

        # Nothing under tmp_path should have been created by delivery beyond
        # what the fixture itself set up (storage_path/database_path dirs
        # aren't even created by delivery).
        assert not (tmp_path / "admin-token").exists()


class TestStdoutSink:
    """Tests for sink=stdout (opt-in only)."""

    def test_prints_token_and_warning(
        self, tmp_path: Path, base_settings: MagpieSettings, capsys: pytest.CaptureFixture[str]
    ) -> None:
        settings = base_settings.model_copy(update={"admin_token_sink": "stdout"})

        deliver_admin_token(TOKEN, settings, action="init")

        captured = capsys.readouterr()
        assert TOKEN in captured.out
        assert "insecure" in captured.out.lower()
