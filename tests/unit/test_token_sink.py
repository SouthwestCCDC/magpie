"""Unit tests for admin token delivery sinks."""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from magpie.auth.database import get_connection, get_token_by_name, init_database
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenExistsError, TokenService
from magpie.auth.token_sink import TokenSinkError, admin_token_lock, deliver_admin_token
from magpie.config import MagpieSettings

TOKEN = "mgp_ADMIN_test_token_value_should_never_leak"  # nosec B105 - test fixture, not a real secret

# Bounds for the concurrency tests below: a crashed/deadlocked worker thread
# should surface as a test failure within a few seconds, not hang the suite.
_BARRIER_TIMEOUT = 5.0
_JOIN_TIMEOUT = 10.0


def _join_and_assert_exited(threads: list[threading.Thread]) -> None:
    """Join worker threads with a timeout and assert they actually exited.

    A bare, timeout-less join() would hang the whole suite if a worker
    deadlocks (e.g. inside admin_token_lock). Joining with a timeout and
    then asserting liveness turns that failure mode into a fast, clear
    assertion instead.
    """
    for t in threads:
        t.join(timeout=_JOIN_TIMEOUT)
    for t in threads:
        assert not t.is_alive(), f"{t.name} did not exit within {_JOIN_TIMEOUT}s (deadlock?)"


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

    def test_preexisting_wide_permissions_are_tightened_to_0600(
        self, tmp_path: Path, base_settings: MagpieSettings
    ) -> None:
        """A pre-existing, world-readable target file must end up 0600.

        os.open's mode=0o600 only applies when a file is newly created --
        O_TRUNC on a pre-existing file leaves its mode untouched. Simulates
        an attacker pre-placing a 0666 file at the target path to widen the
        exposure window before the token is written.
        """
        target = tmp_path / "admin-token"
        target.write_text("stale-value\n")
        os.chmod(target, 0o666)

        settings = base_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": target}
        )

        deliver_admin_token(TOKEN, settings, action="init")

        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600
        assert target.read_text().strip() == TOKEN

    def test_permissions_tightened_before_token_is_written(
        self, tmp_path: Path, base_settings: MagpieSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fd's mode must already be 0600 by the time content is written.

        Directly verifies the ordering (not just the end state): intercepts
        os.fdopen (called immediately after os.open + os.fchmod, right
        before the token is written) and asserts the fd's mode is already
        0600 at that point, even though the target pre-existed with a wider
        mode.
        """
        target = tmp_path / "admin-token"
        target.write_text("stale-value\n")
        os.chmod(target, 0o666)

        settings = base_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": target}
        )

        real_fdopen = os.fdopen
        observed_modes: list[int] = []

        def spying_fdopen(fd: int, *args: object, **kwargs: object) -> object:
            observed_modes.append(stat.S_IMODE(os.fstat(fd).st_mode))
            return real_fdopen(fd, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "fdopen", spying_fdopen)

        deliver_admin_token(TOKEN, settings, action="init")

        assert observed_modes == [0o600]

    def test_fchmod_failure_does_not_leak_file_descriptor(
        self, tmp_path: Path, base_settings: MagpieSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If os.fchmod() fails after os.open(), the fd must still be closed.

        Before os.fdopen() takes ownership of the fd, nothing else closes
        it -- a failure in the fchmod-before-write window would otherwise
        leak a raw file descriptor.
        """
        target = tmp_path / "admin-token"
        settings = base_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": target}
        )

        real_open = os.open
        opened_fds: list[int] = []

        def spying_open(path: object, flags: int, mode: int = 0o777) -> int:
            fd = real_open(path, flags, mode)  # type: ignore[arg-type]
            opened_fds.append(fd)
            return fd

        def failing_fchmod(fd: int, mode: int) -> None:
            raise OSError("simulated fchmod failure")

        monkeypatch.setattr(os, "open", spying_open)
        monkeypatch.setattr(os, "fchmod", failing_fchmod)

        with pytest.raises(TokenSinkError):
            deliver_admin_token(TOKEN, settings, action="init")

        assert len(opened_fds) == 1
        with pytest.raises(OSError):
            os.fstat(opened_fds[0])

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


class TestAdminTokenLock:
    """Tests for admin_token_lock(), the mutual-exclusion primitive behind #552."""

    def test_lock_file_created_next_to_database(
        self, tmp_path: Path, base_settings: MagpieSettings
    ) -> None:
        assert not (tmp_path / ".magpie-admin-token.lock").exists()

        with admin_token_lock(base_settings):
            pass

        assert (tmp_path / ".magpie-admin-token.lock").exists()

    def test_lock_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        settings = MagpieSettings(
            storage_path=tmp_path / "storage",
            database_path=tmp_path / "nested" / "dir" / "magpie.db",
        )

        with admin_token_lock(settings):
            pass

        assert (tmp_path / "nested" / "dir" / ".magpie-admin-token.lock").exists()

    def test_lock_is_reentrant_across_sequential_acquisitions(
        self, base_settings: MagpieSettings
    ) -> None:
        """The lock must be released on exit so a later caller can acquire it again."""
        with admin_token_lock(base_settings):
            pass
        with admin_token_lock(base_settings):
            pass  # would hang if the first acquisition leaked the lock

    def test_lock_serializes_concurrent_critical_sections(
        self, base_settings: MagpieSettings
    ) -> None:
        """Two threads racing for the lock must never be inside it at the same time.

        Tracks occupancy with a plain threading.Lock-guarded counter --
        incremented on entry, decremented on exit, both under the counter's
        own lock so the read-check-write is atomic (unlike a bare
        threading.Event, where "is it set" and "set it" are two separate,
        independently racy operations that could both observe False and
        both proceed even with a broken admin_token_lock). Records the
        highest occupancy ever observed; with a real mutual-exclusion lock
        this must stay at 1, and with a no-op/broken lock it will exceed 1.
        Runs many rounds, starting both workers at the same instant via a
        barrier each round to maximize the chance of catching a broken lock.
        """
        rounds = 50
        barrier = threading.Barrier(2)
        errors: list[Exception] = []
        occupancy_guard = threading.Lock()
        occupancy = 0
        max_occupancy = 0

        def worker(worker_id: int) -> None:
            nonlocal occupancy, max_occupancy
            for round_num in range(rounds):
                try:
                    barrier.wait(timeout=_BARRIER_TIMEOUT)
                    with admin_token_lock(base_settings):
                        with occupancy_guard:
                            occupancy += 1
                            max_occupancy = max(max_occupancy, occupancy)
                        time.sleep(0.001)
                        with occupancy_guard:
                            occupancy -= 1
                except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        _join_and_assert_exited(threads)

        assert not errors, f"Unexpected errors: {errors}"
        assert max_occupancy == 1, (
            f"Lock allowed overlapping critical sections: max_occupancy={max_occupancy}"
        )


class TestAdminTokenSinkRaceRegression:
    """Regression tests for #552: a concurrent unlocked init/rotate could
    deliver a token to the sink that never became the active, persisted one.
    """

    def test_concurrent_first_boot_checks_never_both_deliver(self, tmp_path: Path) -> None:
        """Two threads racing through check-exists -> deliver -> persist
        (mirroring `magpie-ctl init` on first boot) must not both deliver a
        candidate -- the loser must observe the token as already existing
        (under the lock) and deliver nothing at all.
        """
        sink_file = tmp_path / "admin-token"
        settings = MagpieSettings(
            storage_path=tmp_path / "storage",
            database_path=tmp_path / "magpie.db",
            admin_token_sink="file",
            admin_token_sink_file_path=sink_file,
        )
        init_database(settings.database_path)
        token_service = TokenService(settings)

        barrier = threading.Barrier(2)
        errors: list[Exception] = []
        delivered_by: list[int] = []

        def first_boot_init(worker_id: int) -> None:
            try:
                barrier.wait(timeout=_BARRIER_TIMEOUT)
                with admin_token_lock(settings):
                    conn = get_connection(settings.database_path)
                    try:
                        exists = get_token_by_name(conn, "admin") is not None
                    finally:
                        conn.close()
                    if exists:
                        return
                    candidate = token_service.generate_plaintext_token(TokenScope.ADMIN)
                    deliver_admin_token(candidate, settings, action="init")
                    delivered_by.append(worker_id)
                    try:
                        token_service.create_token("admin", TokenScope.ADMIN, candidate)
                    except TokenExistsError:  # pragma: no cover - defensive, see init.py
                        pass
            except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                errors.append(exc)

        threads = [threading.Thread(target=first_boot_init, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        _join_and_assert_exited(threads)

        assert not errors, f"Unexpected errors: {errors}"

        # Exactly one thread must have delivered a candidate at all -- the
        # locked existence check means the loser sees the token as already
        # persisted and never delivers a second, doomed-to-lose value.
        assert len(delivered_by) == 1, f"Expected exactly one delivery, got {delivered_by}"

        # The sink must hold exactly the token that authenticates -- never a
        # delivered-but-discarded candidate.
        delivered = sink_file.read_text().strip()
        info = token_service.validate_token(delivered)
        assert info is not None, "sink-delivered token must be the active, persisted token"
        assert info.scope == TokenScope.ADMIN

    def test_concurrent_reset_never_leaves_sink_diverged_from_persisted(
        self, tmp_path: Path
    ) -> None:
        """Two threads racing through deliver -> persist (mirroring
        `magpie-ctl init --reset-admin-token`) must never leave the sink
        holding a token that isn't the one persisted as active.

        Runs many rounds to make the invariant check meaningful regardless
        of thread scheduling: whichever racer's critical section runs last
        (in the lock's strict total order) determines the final state, and
        that final state must always be internally consistent.
        """
        sink_file = tmp_path / "admin-token"
        settings = MagpieSettings(
            storage_path=tmp_path / "storage",
            database_path=tmp_path / "magpie.db",
            admin_token_sink="file",
            admin_token_sink_file_path=sink_file,
        )
        init_database(settings.database_path)
        token_service = TokenService(settings)
        token_service.create_token("admin", TokenScope.ADMIN)

        rounds = 25
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def reset_once() -> None:
            candidate = token_service.generate_plaintext_token(TokenScope.ADMIN)
            with admin_token_lock(settings):
                deliver_admin_token(candidate, settings, action="reset")
                token_service.replace_token("admin", TokenScope.ADMIN, candidate)

        def worker() -> None:
            for _ in range(rounds):
                try:
                    barrier.wait(timeout=_BARRIER_TIMEOUT)
                    reset_once()
                except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        _join_and_assert_exited(threads)

        assert not errors, f"Unexpected errors: {errors}"

        delivered = sink_file.read_text().strip()
        info = token_service.validate_token(delivered)
        assert info is not None, "sink-delivered token must be the active, persisted token"
        assert info.scope == TokenScope.ADMIN

    def test_concurrent_revoke_never_orphans_a_delivered_rotate_candidate(
        self, tmp_path: Path
    ) -> None:
        """A concurrent `token revoke admin` racing `token rotate admin`
        must not delete the row between rotate's delivery and its persist.

        This is the "rarer variant" noted in #552: rotate looks up the
        existing admin token, delivers a new candidate to the sink, then
        persists via an atomic revoke-old+create-new that requires the row
        it just looked up to still be there. If a concurrent revoke isn't
        serialized by the same lock, it can delete that row in the gap
        between rotate's delivery and its persist -- rotate_token() then
        finds nothing to rotate and returns None, so the already-delivered
        candidate never becomes active anywhere, yet the sink keeps holding
        it. With both operations under admin_token_lock, revoke can't run
        until rotate's whole critical section (including persist) is done,
        so this can never happen.

        Runs many rounds, re-seeding an admin token at the start of each
        round so rotate has something to find regardless of which round's
        race revoke won. Each round uses an Event handshake (rather than
        relying on scheduler timing) to deterministically force revoke to
        attempt its delete in the exact delivered-but-not-yet-persisted
        window: revoke waits for rotate's "delivered" signal before racing
        for the row, and rotate yields briefly after signaling so revoke
        gets a real chance to run before rotate's persist call. With the
        real lock, that yield just means rotate holds the lock a little
        longer -- revoke still can't acquire it until rotate is done.
        """
        sink_file = tmp_path / "admin-token"
        settings = MagpieSettings(
            storage_path=tmp_path / "storage",
            database_path=tmp_path / "magpie.db",
            admin_token_sink="file",
            admin_token_sink_file_path=sink_file,
        )
        init_database(settings.database_path)
        token_service = TokenService(settings)

        rounds = 25
        errors: list[Exception] = []
        orphaned_candidates: list[str] = []

        def rotate_once(delivered: threading.Event) -> None:
            with admin_token_lock(settings):
                conn = get_connection(settings.database_path)
                try:
                    existing = get_token_by_name(conn, "admin")
                finally:
                    conn.close()
                if existing is None or existing.scope != TokenScope.ADMIN:
                    delivered.set()  # unblock revoke_once even when there's nothing to rotate
                    return
                candidate = token_service.generate_plaintext_token(TokenScope.ADMIN)
                deliver_admin_token(candidate, settings, action="rotate")
                delivered.set()
                # Yield so a concurrent, unlocked revoke gets a real chance
                # to run in this window before persistence completes --
                # without this, the race is real but too narrow to hit
                # reliably even with the Event handshake above (confirmed
                # empirically: 1ms wasn't enough for the other thread to be
                # scheduled and complete its DB write before this resumes;
                # 20ms reproduces the race reliably).
                time.sleep(0.02)
                rotate_result = token_service.rotate_token("admin", plaintext_token=candidate)
                if rotate_result is None:
                    # The exact race this test guards against: delivered,
                    # but the row vanished before persistence could happen.
                    orphaned_candidates.append(candidate)

        def revoke_once(delivered: threading.Event) -> None:
            delivered.wait(timeout=_BARRIER_TIMEOUT)
            with admin_token_lock(settings):
                token_service.revoke_token("admin")

        for _ in range(rounds):
            conn = get_connection(settings.database_path)
            try:
                exists = get_token_by_name(conn, "admin") is not None
            finally:
                conn.close()
            if not exists:
                token_service.create_token("admin", TokenScope.ADMIN)

            delivered = threading.Event()

            def run(fn: object) -> None:
                try:
                    fn(delivered)  # type: ignore[operator]
                except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

            threads = [
                threading.Thread(target=run, args=(rotate_once,)),
                threading.Thread(target=run, args=(revoke_once,)),
            ]
            for t in threads:
                t.start()
            _join_and_assert_exited(threads)

        assert not errors, f"Unexpected errors: {errors}"
        assert not orphaned_candidates, (
            f"rotate delivered {len(orphaned_candidates)} candidate(s) that were never "
            "persisted -- a concurrent revoke was not serialized by admin_token_lock"
        )

    def test_concurrent_token_create_admin_never_diverges_first_boot_sink(
        self, tmp_path: Path
    ) -> None:
        """A concurrent `token create --name admin --scope admin` racing
        first-boot `magpie-ctl init` must not leave the sink holding a
        candidate that isn't the token that ended up persisted.

        Mirrors test_concurrent_first_boot_checks_never_both_deliver, but
        the second racer is a direct `token create` of the same reserved
        name instead of a second `init` -- both mutate the same "admin"-
        named row and so must be serialized by the same lock.
        """
        sink_file = tmp_path / "admin-token"
        settings = MagpieSettings(
            storage_path=tmp_path / "storage",
            database_path=tmp_path / "magpie.db",
            admin_token_sink="file",
            admin_token_sink_file_path=sink_file,
        )
        init_database(settings.database_path)
        token_service = TokenService(settings)

        barrier = threading.Barrier(2)
        errors: list[Exception] = []
        succeeded: list[str] = []

        def first_boot_init() -> None:
            try:
                barrier.wait(timeout=_BARRIER_TIMEOUT)
                with admin_token_lock(settings):
                    conn = get_connection(settings.database_path)
                    try:
                        exists = get_token_by_name(conn, "admin") is not None
                    finally:
                        conn.close()
                    if exists:
                        return
                    candidate = token_service.generate_plaintext_token(TokenScope.ADMIN)
                    deliver_admin_token(candidate, settings, action="init")
                    try:
                        token_service.create_token("admin", TokenScope.ADMIN, candidate)
                        succeeded.append("init")
                    except TokenExistsError:  # pragma: no cover - defensive, see init.py
                        pass
            except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                errors.append(exc)

        def token_create_admin() -> None:
            try:
                barrier.wait(timeout=_BARRIER_TIMEOUT)
                with admin_token_lock(settings):
                    try:
                        token_service.create_token("admin", TokenScope.ADMIN)
                        succeeded.append("token_create")
                    except TokenExistsError:
                        pass
            except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                errors.append(exc)

        threads = [
            threading.Thread(target=first_boot_init),
            threading.Thread(target=token_create_admin),
        ]
        for t in threads:
            t.start()
        _join_and_assert_exited(threads)

        assert not errors, f"Unexpected errors: {errors}"

        # Exactly one of the two must have actually persisted a row -- the
        # locked existence check means the loser sees the token as already
        # taken and never wastes (or orphans) a delivered candidate.
        assert len(succeeded) == 1, f"Expected exactly one winner, got {succeeded}"

        # If init delivered anything to the sink, it must be the token that
        # actually ended up persisted -- never a discarded candidate.
        if sink_file.exists():
            delivered = sink_file.read_text().strip()
            info = token_service.validate_token(delivered)
            assert info is not None, "sink holds a token that isn't the active persisted one"
            assert info.scope == TokenScope.ADMIN
