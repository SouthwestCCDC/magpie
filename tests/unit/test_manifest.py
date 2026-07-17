"""Unit tests for manifest management operations."""

from __future__ import annotations

import fcntl
import json
import os
import threading
from pathlib import Path

import pytest

from magpie.storage.exceptions import ManifestCorruptError
from magpie.storage.manifest import (
    Manifest,
    artifact_lock,
    read_manifest,
    remove_tag,
    update_tag,
    write_manifest,
)
from magpie.storage.paths import manifest_path

# Bounds for the concurrency tests below: a crashed/deadlocked worker thread
# should surface as a test failure within a few seconds, not hang the suite.
_BARRIER_TIMEOUT = 5.0
_JOIN_TIMEOUT = 10.0


def _join_and_assert_exited(threads: list[threading.Thread]) -> None:
    """Join worker threads with a timeout and assert they actually exited.

    A bare, timeout-less join() would hang the whole suite if a worker
    deadlocks (e.g. inside artifact_lock). Joining with a timeout and then
    asserting liveness turns that failure mode into a fast, clear assertion
    instead.
    """
    for t in threads:
        t.join(timeout=_JOIN_TIMEOUT)
    for t in threads:
        assert not t.is_alive(), f"{t.name} did not exit within {_JOIN_TIMEOUT}s (deadlock?)"


class TestManifestModel:
    """Tests for Manifest Pydantic model."""

    def test_default_manifest(self) -> None:
        """Default manifest should have version=1 and empty tags."""
        manifest = Manifest()
        assert manifest.version == 1
        assert manifest.tags == {}

    def test_manifest_with_tags(self) -> None:
        """Manifest should accept tags dictionary."""
        manifest = Manifest(tags={"latest": "@abc12345", "v1.0": "@def67890"})
        assert manifest.tags["latest"] == "@abc12345"
        assert manifest.tags["v1.0"] == "@def67890"


class TestReadWriteManifest:
    """Tests for read_manifest and write_manifest functions."""

    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        """Writing and reading manifest should preserve data."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(version=1, tags={"latest": "@abc12345"})

        write_manifest(artifact_dir, manifest)
        loaded = read_manifest(artifact_dir)

        assert loaded.version == manifest.version
        assert loaded.tags == manifest.tags

    def test_read_missing_manifest_returns_default(self, tmp_path: Path) -> None:
        """Reading non-existent manifest should return default Manifest."""
        artifact_dir = tmp_path / "nonexistent"
        artifact_dir.mkdir()

        manifest = read_manifest(artifact_dir)

        assert manifest.version == 1
        assert manifest.tags == {}

    def test_write_creates_artifact_dir(self, tmp_path: Path) -> None:
        """write_manifest should create artifact_dir if it doesn't exist."""
        artifact_dir = tmp_path / "new" / "nested" / "artifact"
        manifest = Manifest(tags={"test": "@123"})

        write_manifest(artifact_dir, manifest)

        assert artifact_dir.exists()
        assert manifest_path(artifact_dir).exists()

    def test_write_manifest_json_format(self, tmp_path: Path) -> None:
        """Written manifest should be valid JSON."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"latest": "@abc12345"})

        write_manifest(artifact_dir, manifest)

        path = manifest_path(artifact_dir)
        content = path.read_text()
        data = json.loads(content)

        assert data["version"] == 1
        assert data["tags"]["latest"] == "@abc12345"


class TestAtomicWrite:
    """Tests for atomic write behavior."""

    def test_no_temp_files_after_successful_write(self, tmp_path: Path) -> None:
        """Successful write should not leave temp files behind."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"test": "@abc"})

        write_manifest(artifact_dir, manifest)

        # Check for leftover temp files
        temp_files = list(artifact_dir.glob(".magpie_*.tmp"))
        assert len(temp_files) == 0

    def test_manifest_file_created(self, tmp_path: Path) -> None:
        """Write should create .magpie file."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"test": "@abc"})

        write_manifest(artifact_dir, manifest)

        path = manifest_path(artifact_dir)
        assert path.exists()
        assert path.name == ".magpie"


class TestUpdateTag:
    """Tests for update_tag function."""

    def test_update_tag_creates_new_tag(self, tmp_path: Path) -> None:
        """update_tag should create a new tag in empty manifest."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()

        result = update_tag(artifact_dir, "latest", "@abc12345")

        assert result.tags["latest"] == "@abc12345"
        # Verify persisted
        loaded = read_manifest(artifact_dir)
        assert loaded.tags["latest"] == "@abc12345"

    def test_update_tag_updates_existing_tag(self, tmp_path: Path) -> None:
        """update_tag should update an existing tag."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"latest": "@old12345"})
        write_manifest(artifact_dir, manifest)

        result = update_tag(artifact_dir, "latest", "@new67890")

        assert result.tags["latest"] == "@new67890"
        # Verify persisted
        loaded = read_manifest(artifact_dir)
        assert loaded.tags["latest"] == "@new67890"

    def test_update_tag_preserves_other_tags(self, tmp_path: Path) -> None:
        """update_tag should not affect other tags."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"v1.0": "@version1", "v2.0": "@version2"})
        write_manifest(artifact_dir, manifest)

        update_tag(artifact_dir, "latest", "@newlatest")

        loaded = read_manifest(artifact_dir)
        assert loaded.tags["v1.0"] == "@version1"
        assert loaded.tags["v2.0"] == "@version2"
        assert loaded.tags["latest"] == "@newlatest"


class TestRemoveTag:
    """Tests for remove_tag function."""

    def test_remove_tag_removes_existing_tag(self, tmp_path: Path) -> None:
        """remove_tag should remove an existing tag."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"latest": "@abc12345", "v1.0": "@def67890"})
        write_manifest(artifact_dir, manifest)

        result = remove_tag(artifact_dir, "latest")

        assert "latest" not in result.tags
        assert result.tags["v1.0"] == "@def67890"
        # Verify persisted
        loaded = read_manifest(artifact_dir)
        assert "latest" not in loaded.tags

    def test_remove_tag_nonexistent_no_error(self, tmp_path: Path) -> None:
        """remove_tag on non-existent tag should not raise error."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"v1.0": "@abc12345"})
        write_manifest(artifact_dir, manifest)

        # Should not raise
        result = remove_tag(artifact_dir, "nonexistent")

        assert result.tags == {"v1.0": "@abc12345"}

    def test_remove_tag_from_empty_manifest(self, tmp_path: Path) -> None:
        """remove_tag from empty manifest should not raise error."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()

        # Should not raise
        result = remove_tag(artifact_dir, "nonexistent")

        assert result.tags == {}


def _is_locked_exclusively(artifact_dir: Path) -> bool:
    """Probe whether artifact_dir is currently flock()-held exclusively.

    Opens a *second* fd to artifact_dir and attempts a non-blocking
    exclusive flock. If that fails with EWOULDBLOCK/EAGAIN, some other fd
    (i.e. artifact_lock()'s own held lock) has it locked already.
    """
    probe_fd = os.open(artifact_dir, os.O_RDONLY)
    try:
        fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe_fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(probe_fd)


class TestOnLockedCallback:
    """Regression tests for issue #547.

    update_tag()/remove_tag() must invoke their on_locked callback while
    still holding the artifact lock (not after releasing it), so that
    callers doing further locked work derived from the new manifest --
    e.g. StorageService's symlink reconciliation -- see a state that can't
    be raced by a concurrent writer between the manifest write and the
    follow-up work.
    """

    def test_update_tag_on_locked_runs_while_lock_held(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        observed: dict[str, bool] = {}

        def on_locked(manifest: Manifest) -> None:
            observed["lock_held"] = _is_locked_exclusively(artifact_dir)
            observed["tag_visible"] = manifest.tags.get("latest") == "@abc12345"

        update_tag(artifact_dir, "latest", "@abc12345", on_locked=on_locked)

        assert observed["lock_held"] is True
        assert observed["tag_visible"] is True

    def test_remove_tag_on_locked_runs_while_lock_held(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / "artifact"
        write_manifest(artifact_dir, Manifest(tags={"latest": "@abc12345"}))
        observed: dict[str, bool] = {}

        def on_locked(manifest: Manifest, had_tag: bool) -> None:
            observed["lock_held"] = _is_locked_exclusively(artifact_dir)
            observed["had_tag"] = had_tag
            observed["tag_gone"] = "latest" not in manifest.tags

        remove_tag(artifact_dir, "latest", on_locked=on_locked)

        assert observed["lock_held"] is True
        assert observed["had_tag"] is True
        assert observed["tag_gone"] is True

    def test_remove_tag_on_locked_reports_had_tag_false_when_absent(self, tmp_path: Path) -> None:
        """had_tag reflects the locked read, not merely "call completed"."""
        artifact_dir = tmp_path / "artifact"
        write_manifest(artifact_dir, Manifest(tags={"other": "@xyz98765"}))
        observed: dict[str, bool] = {}

        def on_locked(manifest: Manifest, had_tag: bool) -> None:
            observed["had_tag"] = had_tag

        remove_tag(artifact_dir, "nonexistent", on_locked=on_locked)

        assert observed["had_tag"] is False

    def test_on_locked_not_required(self, tmp_path: Path) -> None:
        """Callers that don't pass on_locked are unaffected (back-compat)."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()

        result = update_tag(artifact_dir, "latest", "@abc12345")
        assert result.tags["latest"] == "@abc12345"

        result = remove_tag(artifact_dir, "latest")
        assert "latest" not in result.tags


class TestArtifactLockRetries:
    """Regression tests for artifact_lock()'s retry-on-race behavior.

    artifact_lock() acquires its lock via mkdir() followed by os.open(),
    two separate, non-atomic syscalls. Under adversarial concurrent
    create/remove pressure on the exact same artifact directory (e.g. two
    overlapping GC cleanup passes), either step can lose a race:

    - os.open() can hit FileNotFoundError if a concurrent caller removed
      the directory in the gap after our mkdir().
    - mkdir(exist_ok=True) itself can raise FileExistsError: its own
      internal implementation catches EEXIST from os.mkdir() and then
      rechecks is_dir(), but if a third caller removes the directory again
      in that narrow window, it re-raises instead of tolerating it.

    Both are retried (bounded) rather than allowed to crash the caller.
    These tests drive each race deterministically via monkeypatching,
    rather than relying on real thread scheduling to hit a narrow window.
    """

    def test_retries_on_open_race(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retries mkdir+open when os.open() hits FileNotFoundError once.

        Only os.open() calls for our own artifact_dir are intercepted (and
        only the first one), so unrelated os.open() calls elsewhere in the
        process during the test (logging, etc.) are unaffected.
        """
        import magpie.storage.manifest as manifest_module

        artifact_dir = tmp_path / "artifact"
        real_open = manifest_module.os.open
        intercepted = {"done": False}

        def flaky_open(path, flags, mode=0o777, *, dir_fd=None):
            if not intercepted["done"] and Path(path) == artifact_dir:
                intercepted["done"] = True
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(manifest_module.os, "open", flaky_open)

        with artifact_lock(artifact_dir):
            pass

        assert intercepted["done"], "expected the simulated race to actually trigger"
        assert artifact_dir.exists()

    def test_retries_on_mkdir_race(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retries mkdir+open when mkdir(exist_ok=True) hits FileExistsError once.

        Only Path.mkdir() calls for our own artifact_dir are intercepted
        (and only the first one), so unrelated Path.mkdir() calls elsewhere
        in the process during the test are unaffected.
        """
        artifact_dir = tmp_path / "artifact"
        real_mkdir = Path.mkdir
        intercepted = {"done": False}

        def flaky_mkdir(
            path_self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
        ):
            if not intercepted["done"] and path_self == artifact_dir:
                intercepted["done"] = True
                raise FileExistsError(17, "File exists", str(path_self))
            return real_mkdir(path_self, mode, parents=parents, exist_ok=exist_ok)

        monkeypatch.setattr(Path, "mkdir", flaky_mkdir)

        with artifact_lock(artifact_dir):
            pass

        assert intercepted["done"], "expected the simulated race to actually trigger"
        assert artifact_dir.exists()

    def test_raises_clear_error_after_exhausting_retries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Gives up with a clear OSError if the directory never stops flapping.

        This is not expected to happen from the ordinary two/three-caller
        races the retry loop is meant to absorb, but the loop must still
        terminate (not hang or loop forever) and fail loudly rather than
        with a bare, confusing FileNotFoundError. Only os.open() calls for
        our own artifact_dir are affected.
        """
        import magpie.storage.manifest as manifest_module

        artifact_dir = tmp_path / "artifact"
        real_open = manifest_module.os.open

        def always_missing_open(path, flags, mode=0o777, *, dir_fd=None):
            if Path(path) == artifact_dir:
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(manifest_module.os, "open", always_missing_open)

        with pytest.raises(OSError, match="kept flapping"):
            with artifact_lock(artifact_dir):
                pass

    def test_non_directory_at_path_raises_immediately_not_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stray plain file at the artifact path fails fast and clearly.

        ``FileExistsError`` from ``mkdir(exist_ok=True)`` can mean either a
        transient create/remove race (retried above) or a non-directory
        genuinely occupying the path (not retried -- no amount of retrying
        turns a file into a directory). This asserts the latter case is
        distinguished from the former: it raises immediately, with a clear
        message identifying the actual problem, rather than exhausting the
        retry loop into a confusing "kept flapping" error.
        """
        artifact_dir = tmp_path / "artifact"
        artifact_dir.write_text("not a directory", encoding="utf-8")

        mkdir_calls = {"count": 0}
        real_mkdir = Path.mkdir

        def counting_mkdir(
            path_self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
        ):
            if path_self == artifact_dir:
                mkdir_calls["count"] += 1
            return real_mkdir(path_self, mode, parents=parents, exist_ok=exist_ok)

        monkeypatch.setattr(Path, "mkdir", counting_mkdir)

        with pytest.raises(OSError, match="non-directory") as exc_info:
            with artifact_lock(artifact_dir):
                pass

        assert "kept flapping" not in str(exc_info.value)
        assert mkdir_calls["count"] == 1, "should fail on the first attempt, not retry"


class TestConcurrentManifestUpdates:
    """Regression tests for issue #527.

    Before per-artifact locking was added, update_tag()'s read-modify-write
    cycle (read_manifest -> mutate dict -> write_manifest) was not
    serialized. Two concurrent writers setting *different* tags on the same
    artifact could both read the manifest before either had written, so
    whichever writer finished last would silently overwrite (and lose) the
    other's tag. These tests force that interleaving with a barrier and
    assert no tag is ever lost.
    """

    def test_concurrent_different_tags_no_lost_update(self, tmp_path: Path) -> None:
        """Two threads racing to set different tags must both survive.

        Runs many rounds, starting both writers at the same instant via a
        barrier each round to maximize the chance of the interleaving that
        caused #527 (both threads reading the manifest before either
        writes). Every tag set across every round must be present in the
        final manifest.
        """
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        rounds = 50
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def writer(worker_id: int) -> None:
            for round_num in range(rounds):
                try:
                    barrier.wait(timeout=_BARRIER_TIMEOUT)
                    update_tag(
                        artifact_dir,
                        f"worker{worker_id}-round{round_num}",
                        f"@{worker_id}{round_num:07d}",
                    )
                except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

        threads = [threading.Thread(target=writer, args=(worker_id,)) for worker_id in (0, 1)]
        for t in threads:
            t.start()
        _join_and_assert_exited(threads)

        assert not errors, f"Unexpected errors during concurrent tag updates: {errors}"

        loaded = read_manifest(artifact_dir)
        expected_tags = {
            f"worker{worker_id}-round{round_num}"
            for worker_id in (0, 1)
            for round_num in range(rounds)
        }
        missing = expected_tags - set(loaded.tags.keys())
        assert not missing, f"Lost tag updates under concurrency: {sorted(missing)}"
        assert len(loaded.tags) == len(expected_tags)

    def test_concurrent_update_and_remove_different_tags(self, tmp_path: Path) -> None:
        """Concurrent update_tag and remove_tag on different tags don't race.

        One worker repeatedly adds a new tag while another repeatedly
        removes and re-adds a *different*, pre-existing tag. The tag being
        added by the first worker must never be lost due to the second
        worker's unrelated read-modify-write cycle.
        """
        artifact_dir = tmp_path / "artifact"
        write_manifest(artifact_dir, Manifest(tags={"stable": "@stable01"}))

        rounds = 50
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def adder() -> None:
            for round_num in range(rounds):
                try:
                    barrier.wait(timeout=_BARRIER_TIMEOUT)
                    update_tag(artifact_dir, f"new-tag-{round_num}", f"@new{round_num:06d}")
                except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

        def remover() -> None:
            for round_num in range(rounds):
                try:
                    barrier.wait(timeout=_BARRIER_TIMEOUT)
                    remove_tag(artifact_dir, "stable")
                    update_tag(artifact_dir, "stable", "@stable01")
                except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

        threads = [threading.Thread(target=adder), threading.Thread(target=remover)]
        for t in threads:
            t.start()
        _join_and_assert_exited(threads)

        assert not errors, f"Unexpected errors during concurrent updates: {errors}"

        loaded = read_manifest(artifact_dir)
        assert loaded.tags.get("stable") == "@stable01"
        missing = {f"new-tag-{i}" for i in range(rounds)} - set(loaded.tags.keys())
        assert not missing, f"Lost tag updates under concurrency: {sorted(missing)}"


class TestCorruptManifest:
    """Tests for corrupt manifest handling."""

    def test_corrupt_json_raises_manifest_corrupt_error(self, tmp_path: Path) -> None:
        """Invalid JSON should raise ManifestCorruptError."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        path = manifest_path(artifact_dir)
        path.write_text("{ invalid json }", encoding="utf-8")

        with pytest.raises(ManifestCorruptError, match="Invalid JSON"):
            read_manifest(artifact_dir)

    def test_truncated_json_raises_manifest_corrupt_error(self, tmp_path: Path) -> None:
        """Truncated JSON should raise ManifestCorruptError."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        path = manifest_path(artifact_dir)
        path.write_text('{"version": 1, "tags":', encoding="utf-8")

        with pytest.raises(ManifestCorruptError):
            read_manifest(artifact_dir)

    def test_empty_file_raises_manifest_corrupt_error(self, tmp_path: Path) -> None:
        """Empty manifest file should raise ManifestCorruptError."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        path = manifest_path(artifact_dir)
        path.write_text("", encoding="utf-8")

        with pytest.raises(ManifestCorruptError):
            read_manifest(artifact_dir)
