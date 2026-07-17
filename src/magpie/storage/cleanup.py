"""Cleanup utilities for removing empty artifact directories after GC."""

from __future__ import annotations

import errno
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from magpie.storage.manifest import artifact_lock, read_manifest
from magpie.storage.paths import manifest_path

logger = structlog.get_logger()


@dataclass
class CleanupStats:
    """Statistics from directory cleanup operation."""

    empty_blobs_dirs: int = 0
    empty_metadata_dirs: int = 0
    empty_manifests: int = 0
    empty_artifact_dirs: int = 0
    empty_parent_dirs: int = 0
    removed_paths: list[str] = field(default_factory=list)

    @property
    def total_removed(self) -> int:
        """Total number of items removed."""
        return (
            self.empty_blobs_dirs
            + self.empty_metadata_dirs
            + self.empty_manifests
            + self.empty_artifact_dirs
            + self.empty_parent_dirs
        )


def is_empty_directory(path: Path) -> bool:
    """Check if a directory is empty.

    Args:
        path: Path to check.

    Returns:
        True if path is an empty directory, False otherwise.
    """
    if not path.is_dir():
        return False
    return not any(path.iterdir())


def cleanup_artifact_directories(
    artifact_dir: Path,
    storage_root: Path,
    dry_run: bool = False,
) -> CleanupStats:
    """Clean up empty directories for a single artifact after GC.

    Removes in order:
    1. Empty blobs/ directory
    2. Empty metadata/ directory
    3. .magpie if no tags remain
    4. Artifact directory if completely empty
    5. Empty parent directories up to storage_root

    The empty/deletable checks and the corresponding deletes are performed
    under the artifact's exclusive lock (the same lock update_tag()/
    remove_tag() use). Without this, GC could read a stale, unlocked "no
    tags" state and then unlink the manifest (and remove the whole artifact
    directory) after a concurrent tag mutation had already won the lock and
    written a new tag into it -- silently destroying that update along with
    the rest of the artifact. Locking here closes that race by making the
    check-then-delete atomic with respect to manifest mutations.

    Args:
        artifact_dir: Path to the artifact directory.
        storage_root: Base storage path (cleanup stops here).
        dry_run: If True, report what would be removed without removing.

    Returns:
        CleanupStats with counts of removed items.
    """
    stats = CleanupStats()

    if artifact_dir.exists():
        with artifact_lock(artifact_dir):
            # Check and remove empty blobs/ directory
            blobs_dir = artifact_dir / "blobs"
            if blobs_dir.exists() and is_empty_directory(blobs_dir):
                rel_path = str(blobs_dir.relative_to(storage_root))
                stats.empty_blobs_dirs += 1
                stats.removed_paths.append(rel_path)
                if not dry_run:
                    logger.debug("cleanup_removing_empty_blobs_dir", path=rel_path)
                    blobs_dir.rmdir()

            # Check and remove empty metadata/ directory
            metadata_dir = artifact_dir / "metadata"
            if metadata_dir.exists() and is_empty_directory(metadata_dir):
                rel_path = str(metadata_dir.relative_to(storage_root))
                stats.empty_metadata_dirs += 1
                stats.removed_paths.append(rel_path)
                if not dry_run:
                    logger.debug("cleanup_removing_empty_metadata_dir", path=rel_path)
                    metadata_dir.rmdir()

            # Check and remove .magpie if no tags remain. This read happens
            # under the lock, so it reflects the latest state -- not a
            # snapshot taken before GC's scan phase.
            manifest_file = manifest_path(artifact_dir)
            if manifest_file.exists():
                manifest = read_manifest(artifact_dir)
                if not manifest.tags:
                    rel_path = str(manifest_file.relative_to(storage_root))
                    stats.empty_manifests += 1
                    stats.removed_paths.append(rel_path)
                    if not dry_run:
                        logger.debug("cleanup_removing_empty_manifest", path=rel_path)
                        manifest_file.unlink()

            # Check and remove artifact directory if completely empty
            if artifact_dir.exists() and is_empty_directory(artifact_dir):
                rel_path = str(artifact_dir.relative_to(storage_root))
                stats.empty_artifact_dirs += 1
                stats.removed_paths.append(rel_path)
                if not dry_run:
                    logger.debug("cleanup_removing_empty_artifact_dir", path=rel_path)
                    artifact_dir.rmdir()

    # Clean up empty parent directories up to storage_root. This runs
    # whenever artifact_dir is (now) gone -- whether we just removed it
    # above, or it was already gone when we were called (e.g. a concurrent
    # GC pass got there first). The latter case is exactly when a parent
    # may have become empty and still need cleaning: bailing out early
    # without running this step would leave the parent chain populated,
    # violating this function's documented contract to clean up to
    # storage_root regardless of who deleted the artifact directory itself.
    if not artifact_dir.exists():
        _cleanup_empty_parents(artifact_dir.parent, storage_root, dry_run, stats)

    return stats


def _cleanup_empty_parents(
    start_dir: Path,
    storage_root: Path,
    dry_run: bool,
    stats: CleanupStats,
) -> None:
    """Recursively remove empty parent directories up to storage_root.

    Parent directories are shared by every artifact underneath them, so
    unlike the per-artifact steps in cleanup_artifact_directories(), this
    climb is not covered by artifact_lock(). Two concurrent GC passes can
    both land here for the same directory -- either cleaning sibling
    artifacts under the same parent, or (since artifact_dir no longer
    existing is exactly what triggers this climb) two passes racing on the
    very same already-removed artifact -- and both decide it's empty and
    try to remove it. There's also a subtler wrinkle: artifact_lock()
    self-heals a concurrently-deleted artifact directory by recreating it
    (see its retry loop), and that mkdir() isn't coordinated with this
    climb at all, so a peer thread reacquiring its lock can repopulate a
    parent directory in the gap between our emptiness check and our
    rmdir(), turning what looked like an empty-directory removal into an
    ENOTEMPTY error.

    Every filesystem check and mutation on `current` below tolerates losing
    those races instead of crashing the whole GC run:
    - `current` already gone (ENOENT, at any point): the goal state --
      `current` being gone -- already holds regardless of who removed it;
      keep climbing.
    - `current` repopulated out from under us (ENOTEMPTY, on rmdir()):
      genuinely no longer empty; undo the optimistic stats bump made just
      before the rmdir() attempt and stop climbing, exactly as if the
      up-front emptiness check had found it non-empty.

    Args:
        start_dir: Directory to start checking from.
        storage_root: Stop when reaching this directory (never removed).
        dry_run: If True, report without removing.
        stats: CleanupStats to update with removed paths.
    """
    current = start_dir

    while current != storage_root and current.is_relative_to(storage_root):
        try:
            if not current.exists():
                # Parent was already removed in a previous iteration, or by
                # a concurrent GC pass.
                current = current.parent
                continue

            if not is_empty_directory(current):
                # Directory is not empty, stop climbing
                break

            rel_path = str(current.relative_to(storage_root))
            stats.empty_parent_dirs += 1
            stats.removed_paths.append(rel_path)

            if not dry_run:
                logger.debug("cleanup_removing_empty_parent_dir", path=rel_path)
                try:
                    current.rmdir()
                except OSError as e:
                    if e.errno != errno.ENOTEMPTY:
                        raise
                    # Repopulated between our emptiness check and this
                    # rmdir() -- e.g. a concurrent artifact_lock() retry
                    # recreating its own artifact directory under this
                    # parent. Not actually empty: undo the optimistic
                    # accounting above and stop climbing.
                    stats.empty_parent_dirs -= 1
                    stats.removed_paths.pop()
                    break
        except FileNotFoundError:
            # A concurrent caller (another GC pass, possibly racing on this
            # exact directory) removed `current` somewhere between our
            # existence check, our emptiness check's directory scan, or our
            # own rmdir() call. Tolerate it and keep climbing.
            pass

        current = current.parent
