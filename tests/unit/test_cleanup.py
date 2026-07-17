"""Unit tests for cleanup utilities."""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from magpie.storage.cleanup import (
    CleanupStats,
    cleanup_artifact_directories,
    is_empty_directory,
)
from magpie.storage.manifest import Manifest, read_manifest, update_tag, write_manifest

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


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """Create a temporary storage root directory."""
    root = tmp_path / "storage"
    root.mkdir()
    return root


def create_artifact_structure(
    storage_root: Path,
    artifact_path: str,
    blobs: list[str] | None = None,
    metadata: list[str] | None = None,
    tags: dict[str, str] | None = None,
) -> Path:
    """Create an artifact directory structure for testing.

    Args:
        storage_root: Base storage directory.
        artifact_path: Relative path to artifact.
        blobs: List of blob hashes to create (creates actual files).
        metadata: List of metadata file names to create.
        tags: Dict of tag_name -> hash for manifest.

    Returns:
        Path to the artifact directory.
    """
    artifact_dir = storage_root / artifact_path
    artifact_dir.mkdir(parents=True, exist_ok=True)

    if blobs:
        blobs_dir = artifact_dir / "blobs"
        blobs_dir.mkdir(exist_ok=True)
        for blob_hash in blobs:
            (blobs_dir / blob_hash[:8]).write_bytes(b"blob content")

    if metadata:
        metadata_dir = artifact_dir / "metadata"
        metadata_dir.mkdir(exist_ok=True)
        for meta_hash in metadata:
            meta_file = metadata_dir / f"{meta_hash[:8]}.json"
            meta_content = {
                "hash": meta_hash,
                "uploaded_by": "test",
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "source_uri": None,
            }
            meta_file.write_text(json.dumps(meta_content), encoding="utf-8")

    if tags is not None:
        manifest = {"version": 1, "tags": tags}
        (artifact_dir / ".magpie").write_text(json.dumps(manifest), encoding="utf-8")

    return artifact_dir


class TestIsEmptyDirectory:
    """Tests for is_empty_directory function."""

    def test_empty_directory_returns_true(self, tmp_path: Path) -> None:
        """Empty directory returns True."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert is_empty_directory(empty_dir) is True

    def test_directory_with_file_returns_false(self, tmp_path: Path) -> None:
        """Directory with a file returns False."""
        dir_with_file = tmp_path / "has_file"
        dir_with_file.mkdir()
        (dir_with_file / "file.txt").write_text("content")
        assert is_empty_directory(dir_with_file) is False

    def test_directory_with_subdir_returns_false(self, tmp_path: Path) -> None:
        """Directory with a subdirectory returns False."""
        dir_with_subdir = tmp_path / "has_subdir"
        dir_with_subdir.mkdir()
        (dir_with_subdir / "subdir").mkdir()
        assert is_empty_directory(dir_with_subdir) is False

    def test_file_returns_false(self, tmp_path: Path) -> None:
        """Regular file returns False."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("content")
        assert is_empty_directory(file_path) is False

    def test_nonexistent_path_returns_false(self, tmp_path: Path) -> None:
        """Non-existent path returns False."""
        nonexistent = tmp_path / "does_not_exist"
        assert is_empty_directory(nonexistent) is False


class TestCleanupStats:
    """Tests for CleanupStats dataclass."""

    def test_default_values(self) -> None:
        """Default values are all zero."""
        stats = CleanupStats()
        assert stats.empty_blobs_dirs == 0
        assert stats.empty_metadata_dirs == 0
        assert stats.empty_manifests == 0
        assert stats.empty_artifact_dirs == 0
        assert stats.empty_parent_dirs == 0
        assert stats.removed_paths == []

    def test_total_removed_calculation(self) -> None:
        """total_removed sums all counts correctly."""
        stats = CleanupStats(
            empty_blobs_dirs=1,
            empty_metadata_dirs=2,
            empty_manifests=3,
            empty_artifact_dirs=4,
            empty_parent_dirs=5,
        )
        assert stats.total_removed == 15


class TestCleanupArtifactDirectories:
    """Tests for cleanup_artifact_directories function."""

    def test_removes_empty_blobs_dir(self, storage_root: Path) -> None:
        """Removes empty blobs/ directory."""
        artifact_dir = create_artifact_structure(
            storage_root, "test/artifact", blobs=["abc12345"], tags={"latest": "abc12345"}
        )

        # Manually empty the blobs directory
        blobs_dir = artifact_dir / "blobs"
        for f in blobs_dir.iterdir():
            f.unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_blobs_dirs == 1
        assert not blobs_dir.exists()

    def test_removes_empty_metadata_dir(self, storage_root: Path) -> None:
        """Removes empty metadata/ directory."""
        artifact_dir = create_artifact_structure(
            storage_root, "test/artifact", metadata=["abc12345"], tags={"latest": "abc12345"}
        )

        # Manually empty the metadata directory
        metadata_dir = artifact_dir / "metadata"
        for f in metadata_dir.iterdir():
            f.unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_metadata_dirs == 1
        assert not metadata_dir.exists()

    def test_removes_manifest_with_no_tags(self, storage_root: Path) -> None:
        """Removes .magpie file when no tags remain."""
        artifact_dir = create_artifact_structure(
            storage_root,
            "test/artifact",
            tags={},  # Empty tags
        )

        manifest_file = artifact_dir / ".magpie"
        assert manifest_file.exists()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_manifests == 1
        assert not manifest_file.exists()

    def test_preserves_manifest_with_tags(self, storage_root: Path) -> None:
        """Does not remove .magpie file when tags exist."""
        artifact_dir = create_artifact_structure(
            storage_root, "test/artifact", tags={"latest": "abc12345"}
        )

        manifest_file = artifact_dir / ".magpie"
        assert manifest_file.exists()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_manifests == 0
        assert manifest_file.exists()

    def test_removes_empty_artifact_dir(self, storage_root: Path) -> None:
        """Removes artifact directory when completely empty."""
        artifact_dir = create_artifact_structure(storage_root, "test/artifact", tags={})

        # Remove manifest to make it truly empty
        (artifact_dir / ".magpie").unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_artifact_dirs == 1
        assert not artifact_dir.exists()

    def test_removes_empty_parent_dirs(self, storage_root: Path) -> None:
        """Removes empty parent directories up to storage root."""
        artifact_dir = create_artifact_structure(storage_root, "deep/nested/path/artifact", tags={})

        # Remove manifest to make artifact dir empty
        (artifact_dir / ".magpie").unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_artifact_dirs == 1
        assert stats.empty_parent_dirs == 3  # path, nested, deep
        assert not (storage_root / "deep").exists()

    def test_stops_at_storage_root(self, storage_root: Path) -> None:
        """Does not remove storage root directory."""
        artifact_dir = create_artifact_structure(storage_root, "single", tags={})

        # Remove manifest
        (artifact_dir / ".magpie").unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_artifact_dirs == 1
        assert storage_root.exists()

    def test_preserves_non_empty_parent(self, storage_root: Path) -> None:
        """Stops parent cleanup when reaching non-empty directory."""
        # Create two artifacts under same parent
        artifact_dir1 = create_artifact_structure(storage_root, "parent/artifact1", tags={})
        artifact_dir2 = create_artifact_structure(
            storage_root, "parent/artifact2", tags={"latest": "abc12345"}
        )

        # Remove manifest from first artifact
        (artifact_dir1 / ".magpie").unlink()

        stats = cleanup_artifact_directories(artifact_dir1, storage_root, dry_run=False)

        assert stats.empty_artifact_dirs == 1
        assert stats.empty_parent_dirs == 0  # Parent not empty (has artifact2)
        assert not artifact_dir1.exists()
        assert artifact_dir2.exists()
        assert (storage_root / "parent").exists()

    def test_dry_run_does_not_remove(self, storage_root: Path) -> None:
        """Dry run reports but does not remove directories."""
        artifact_dir = create_artifact_structure(
            storage_root, "test/artifact", blobs=["abc12345"], tags={}
        )

        # Empty the blobs dir
        blobs_dir = artifact_dir / "blobs"
        for f in blobs_dir.iterdir():
            f.unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=True)

        # Should report what would be removed
        # Note: In dry run, blobs_dir and manifest are reported as removable,
        # but artifact_dir is not empty (still has .magpie until we "remove" it)
        # because we don't actually remove anything in dry run mode
        assert stats.empty_blobs_dirs == 1
        assert stats.empty_manifests == 1
        # artifact_dir still contains .magpie (not actually removed), so not empty
        assert stats.empty_artifact_dirs == 0
        assert len(stats.removed_paths) >= 2

        # But nothing should actually be removed
        assert blobs_dir.exists()
        assert (artifact_dir / ".magpie").exists()
        assert artifact_dir.exists()

    def test_removed_paths_contains_relative_paths(self, storage_root: Path) -> None:
        """removed_paths contains paths relative to storage root."""
        artifact_dir = create_artifact_structure(storage_root, "test/artifact", tags={})

        # Remove manifest - now artifact_dir is truly empty
        (artifact_dir / ".magpie").unlink()

        # In dry run mode, we report artifact_dir as empty but don't remove it
        # Since we don't remove it, the parent "test" still has content
        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=True)

        assert "test/artifact" in stats.removed_paths
        # Parent "test" is not empty in dry-run because artifact_dir still exists
        assert stats.empty_parent_dirs == 0

    def test_handles_missing_directories(self, storage_root: Path) -> None:
        """Handles case where subdirectories don't exist."""
        artifact_dir = create_artifact_structure(storage_root, "test/artifact", tags={})

        # No blobs/ or metadata/ directories created
        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        # Should still process the empty manifest
        assert stats.empty_blobs_dirs == 0  # Didn't exist
        assert stats.empty_metadata_dirs == 0  # Didn't exist
        assert stats.empty_manifests == 1
        assert stats.empty_artifact_dirs == 1

    def test_full_cleanup_scenario(self, storage_root: Path) -> None:
        """Tests a complete cleanup scenario after all blobs deleted."""
        # Create artifact with blobs and metadata
        artifact_dir = create_artifact_structure(
            storage_root,
            "project/component/artifact",
            blobs=["hash1234", "hash5678"],
            metadata=["hash1234", "hash5678"],
            tags={},  # No tags (simulates after untag)
        )

        # Simulate GC having deleted all blobs and metadata
        blobs_dir = artifact_dir / "blobs"
        for f in blobs_dir.iterdir():
            f.unlink()

        metadata_dir = artifact_dir / "metadata"
        for f in metadata_dir.iterdir():
            f.unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_blobs_dirs == 1
        assert stats.empty_metadata_dirs == 1
        assert stats.empty_manifests == 1
        assert stats.empty_artifact_dirs == 1
        assert stats.empty_parent_dirs == 2  # component, project
        assert stats.total_removed == 6

        # Everything should be cleaned up
        assert not (storage_root / "project").exists()

    def test_cleans_parents_when_artifact_dir_already_removed(self, storage_root: Path) -> None:
        """Cleans up empty parent directories even if artifact_dir is gone.

        Regression test: cleanup_artifact_directories()'s early-exit guard
        for an already-nonexistent artifact_dir (added to stop
        artifact_lock() from resurrecting a removed directory via its own
        mkdir()) must not also skip step 5 (empty parent directory
        cleanup). The artifact directory being gone -- whether because we
        removed it ourselves or a concurrent GC pass got there first -- is
        exactly the situation in which its parents may have become empty
        and still need cleaning, per this function's documented contract.
        """
        artifact_dir = create_artifact_structure(storage_root, "deep/nested/artifact", tags={})
        # Simulate the artifact directory (and everything in it) having
        # already been removed entirely by some other caller before this
        # cleanup pass runs, leaving only the now-empty parent chain.
        shutil.rmtree(artifact_dir)
        assert not artifact_dir.exists()
        assert (storage_root / "deep" / "nested").exists()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        # We didn't remove the artifact directory ourselves -- it was
        # already gone -- but its empty parents must still be cleaned up.
        assert stats.empty_artifact_dirs == 0
        assert stats.empty_parent_dirs == 2  # nested, deep
        assert not (storage_root / "deep").exists()

    def test_tolerates_concurrent_removal_of_parent_dir(
        self, storage_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Parent-directory cleanup tolerates losing a race to another pass.

        Parent directories are shared by every artifact underneath them,
        so the parent-climbing step isn't covered by artifact_lock()
        (which is per-artifact). Two concurrent GC passes cleaning sibling
        artifacts under the same parent can both decide it's empty and
        race to remove it. This simulates that race deterministically:
        Path.rmdir() is patched so that, for the specific parent directory
        under test, a concurrent process is simulated actually removing it
        first (via the real rmdir), and then FileNotFoundError is raised
        for our own call -- exactly what a real race would produce. This
        must be tolerated rather than crashing the whole GC run.
        """
        artifact_dir = create_artifact_structure(storage_root, "shared/artifact", tags={})
        shutil.rmtree(artifact_dir)
        parent = storage_root / "shared"
        assert parent.exists()

        real_rmdir = Path.rmdir

        def racy_rmdir(path_self: Path) -> None:
            if path_self == parent:
                # Simulate a concurrent GC pass winning the race: the
                # directory really is removed, but *our* call observes it
                # as already gone, same as a real race would.
                real_rmdir(path_self)
                raise FileNotFoundError(2, "No such file or directory", str(path_self))
            return real_rmdir(path_self)

        monkeypatch.setattr(Path, "rmdir", racy_rmdir)

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert not parent.exists()
        assert stats.empty_parent_dirs == 1


class TestConcurrentCleanupVsUpdateTag:
    """Regression tests for the GC-cleanup-vs-tag-update race.

    Before cleanup_artifact_directories() took the per-artifact lock, GC
    could read the manifest as empty (no tags), decide the artifact
    directory was deletable, and then unlink the manifest and rmdir the
    directory -- even though a concurrent update_tag() had, in the
    meantime, won the lock and written a brand new tag into that very
    manifest. The result was total loss of both the new tag and the
    artifact directory. cleanup_artifact_directories() and update_tag()
    now share the same artifact_lock(), so whichever side wins the race
    completes its full read-modify-write (or check-then-delete) before the
    other proceeds, and no interleaving can observe a stale state.
    """

    def test_concurrent_cleanup_and_update_tag_no_data_loss(self, storage_root: Path) -> None:
        """Racing cleanup against a new tag write must never lose either.

        Runs many rounds, each starting a cleanup thread and an
        update_tag thread at the same instant via a barrier. Every round
        starts from an artifact directory that looks fully deletable to
        GC (an existing manifest with no tags, no blobs/metadata dirs).
        Regardless of which thread wins the race, the artifact directory
        must exist afterward and must contain the tag written by
        update_tag -- it must never end up deleted out from under a
        concurrent write.
        """
        rounds = 30
        errors: list[Exception] = []

        for round_num in range(rounds):
            artifact_dir = storage_root / f"race-{round_num}" / "artifact"
            write_manifest(artifact_dir, Manifest(tags={}))
            assert artifact_dir.exists()

            barrier = threading.Barrier(2)

            def run_cleanup() -> None:
                try:
                    barrier.wait(timeout=_BARRIER_TIMEOUT)
                    cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)
                except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

            def run_update_tag() -> None:
                try:
                    barrier.wait(timeout=_BARRIER_TIMEOUT)
                    update_tag(artifact_dir, "release", "@newhash1")
                except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

            threads = [
                threading.Thread(target=run_cleanup),
                threading.Thread(target=run_update_tag),
            ]
            for t in threads:
                t.start()
            _join_and_assert_exited(threads)

            assert not errors, f"Round {round_num}: unexpected errors: {errors}"

            # The artifact directory and the concurrently-written tag must
            # both survive, regardless of which side won the race.
            assert artifact_dir.exists(), (
                f"Round {round_num}: artifact directory was lost to a concurrent GC cleanup"
            )
            manifest = read_manifest(artifact_dir)
            assert manifest.tags.get("release") == "@newhash1", (
                f"Round {round_num}: 'release' tag was lost to a concurrent GC cleanup, "
                f"tags={manifest.tags}"
            )


class TestConcurrentCleanupVsCleanup:
    """Regression test for a crash found during re-review of the GC-cleanup
    lock fix.

    artifact_lock() used to do mkdir() then os.open() as two separate,
    non-atomic syscalls. If two cleanup_artifact_directories() calls raced
    on the same already-empty artifact (e.g. two overlapping GC runs), the
    first to finish could delete the manifest and rmdir the artifact
    directory in the gap between the second call's mkdir() and os.open(),
    so the second call's open() would raise FileNotFoundError on a
    directory that no longer existed -- uncaught, aborting the entire GC
    run. artifact_lock() now retries the mkdir+open pair (bounded) on that
    specific error, so a concurrent deleter removing the directory in that
    window just triggers a re-mkdir and re-open instead of a crash.
    """

    def test_concurrent_cleanup_vs_cleanup_soak(self, storage_root: Path) -> None:
        """Many barrier-synchronized cleanup-vs-cleanup rounds don't crash.

        Each round starts from a fresh, fully-deletable artifact directory
        (manifest with no tags, no blobs/metadata) and fires two
        cleanup_artifact_directories() calls at the same instant via a
        barrier. This exercises ordinary OS/GIL scheduling rather than
        forcing a specific interleaving -- it's a general soak test for
        concurrent cleanup correctness, not a targeted reproduction of the
        exact mkdir/open race (see
        test_concurrent_cleanup_vs_cleanup_deterministic for that).
        """
        rounds = 100
        errors: list[Exception] = []

        for round_num in range(rounds):
            artifact_dir = storage_root / f"cleanup-race-{round_num}" / "artifact"
            write_manifest(artifact_dir, Manifest(tags={}))
            assert artifact_dir.exists()

            barrier = threading.Barrier(2)

            def run_cleanup() -> None:
                try:
                    barrier.wait(timeout=_BARRIER_TIMEOUT)
                    cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)
                except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

            threads = [threading.Thread(target=run_cleanup) for _ in range(2)]
            for t in threads:
                t.start()
            _join_and_assert_exited(threads)

            assert not errors, f"Round {round_num}: unexpected errors: {errors}"
            # Both callers agree the artifact is fully deletable, so
            # regardless of interleaving, it should end up gone.
            assert not artifact_dir.exists(), (
                f"Round {round_num}: artifact directory unexpectedly survived"
            )

    def test_concurrent_cleanup_vs_cleanup_deterministic(
        self, storage_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Directly reproduces the mkdir()/open() TOCTOU race.

        Natural OS/GIL scheduling rarely lands a thread switch in the
        narrow window between artifact_lock()'s mkdir() and os.open() calls
        (confirmed: hundreds of natural-scheduling rounds in
        test_concurrent_cleanup_vs_cleanup_soak did not reproduce the bug
        pre-fix). This test controls the interleaving directly instead:
        thread A is parked right after its mkdir() (patched os.open blocks
        before delegating to the real os.open), thread B is then allowed to
        run cleanup_artifact_directories() to full completion -- deleting
        the manifest and rmdir'ing the artifact directory -- and only then
        is thread A released to call the real os.open() against a
        directory that no longer exists at that instant.

        Before the fix, thread A's os.open() raised FileNotFoundError,
        uncaught. After the fix, artifact_lock() retries mkdir()+open() on
        that error and thread A completes without raising.
        """
        import magpie.storage.manifest as manifest_module

        artifact_dir = storage_root / "deterministic" / "artifact"
        write_manifest(artifact_dir, Manifest(tags={}))

        mkdir_done = threading.Event()
        release_open = threading.Event()
        real_open = manifest_module.os.open
        intercepted = {"done": False}

        def patched_open(path, flags, mode=0o777, *, dir_fd=None):
            # Only intercept the first call against this specific artifact
            # directory (thread A's); let every other os.open() call
            # (thread B's, and anything unrelated) through untouched.
            if not intercepted["done"] and Path(path) == artifact_dir:
                intercepted["done"] = True
                mkdir_done.set()
                assert release_open.wait(timeout=_BARRIER_TIMEOUT), (
                    "test setup: release_open never signaled"
                )
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(manifest_module.os, "open", patched_open)

        errors: list[Exception] = []

        def thread_a() -> None:
            try:
                cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)
            except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                errors.append(exc)

        t_a = threading.Thread(target=thread_a)
        t_a.start()

        # Wait for thread A to be parked between mkdir() and open().
        assert mkdir_done.wait(timeout=_BARRIER_TIMEOUT), "thread A never reached os.open()"

        # Thread B (this thread) runs a full, uninterrupted cleanup pass:
        # deletes the manifest and rmdir's the artifact directory.
        cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)
        assert not artifact_dir.exists()

        # Release thread A -- its os.open() now targets a directory that B
        # just removed.
        release_open.set()
        _join_and_assert_exited([t_a])

        assert not errors, f"thread A raised despite the concurrent delete: {errors}"
