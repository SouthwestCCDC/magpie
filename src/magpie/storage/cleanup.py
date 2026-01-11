"""Cleanup utilities for removing empty artifact directories after GC."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from magpie.storage.manifest import read_manifest
from magpie.storage.paths import manifest_path

logger = logging.getLogger(__name__)


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

    Args:
        artifact_dir: Path to the artifact directory.
        storage_root: Base storage path (cleanup stops here).
        dry_run: If True, report what would be removed without removing.

    Returns:
        CleanupStats with counts of removed items.
    """
    stats = CleanupStats()

    # Check and remove empty blobs/ directory
    blobs_dir = artifact_dir / "blobs"
    if blobs_dir.exists() and is_empty_directory(blobs_dir):
        rel_path = str(blobs_dir.relative_to(storage_root))
        stats.empty_blobs_dirs += 1
        stats.removed_paths.append(rel_path)
        if not dry_run:
            logger.debug("Removing empty blobs directory: %s", rel_path)
            blobs_dir.rmdir()

    # Check and remove empty metadata/ directory
    metadata_dir = artifact_dir / "metadata"
    if metadata_dir.exists() and is_empty_directory(metadata_dir):
        rel_path = str(metadata_dir.relative_to(storage_root))
        stats.empty_metadata_dirs += 1
        stats.removed_paths.append(rel_path)
        if not dry_run:
            logger.debug("Removing empty metadata directory: %s", rel_path)
            metadata_dir.rmdir()

    # Check and remove .magpie if no tags remain
    manifest_file = manifest_path(artifact_dir)
    if manifest_file.exists():
        manifest = read_manifest(artifact_dir)
        if not manifest.tags:
            rel_path = str(manifest_file.relative_to(storage_root))
            stats.empty_manifests += 1
            stats.removed_paths.append(rel_path)
            if not dry_run:
                logger.debug("Removing empty manifest: %s", rel_path)
                manifest_file.unlink()

    # Check and remove artifact directory if completely empty
    if artifact_dir.exists() and is_empty_directory(artifact_dir):
        rel_path = str(artifact_dir.relative_to(storage_root))
        stats.empty_artifact_dirs += 1
        stats.removed_paths.append(rel_path)
        if not dry_run:
            logger.debug("Removing empty artifact directory: %s", rel_path)
            artifact_dir.rmdir()

        # Clean up empty parent directories up to storage_root
        _cleanup_empty_parents(artifact_dir.parent, storage_root, dry_run, stats)

    return stats


def _cleanup_empty_parents(
    start_dir: Path,
    storage_root: Path,
    dry_run: bool,
    stats: CleanupStats,
) -> None:
    """Recursively remove empty parent directories up to storage_root.

    Args:
        start_dir: Directory to start checking from.
        storage_root: Stop when reaching this directory (never removed).
        dry_run: If True, report without removing.
        stats: CleanupStats to update with removed paths.
    """
    current = start_dir

    while current != storage_root and current.is_relative_to(storage_root):
        if not current.exists():
            # Parent was already removed in a previous iteration
            current = current.parent
            continue

        if not is_empty_directory(current):
            # Directory is not empty, stop climbing
            break

        rel_path = str(current.relative_to(storage_root))
        stats.empty_parent_dirs += 1
        stats.removed_paths.append(rel_path)

        if not dry_run:
            logger.debug("Removing empty parent directory: %s", rel_path)
            current.rmdir()

        current = current.parent
