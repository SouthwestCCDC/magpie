"""Core garbage collection logic for artifact storage.

This module provides the shared GC implementation used by CLI, CTL, and server
entry points. The entry points are thin wrappers that handle I/O formatting
while this module contains the core logic.
"""

from __future__ import annotations

import json
import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from magpie.storage.cleanup import CleanupStats, cleanup_artifact_directories
from magpie.storage.exceptions import ArtifactNotFoundError
from magpie.storage.manifest import read_manifest
from magpie.storage.metadata import read_metadata
from magpie.storage.symlinks import ReconcileStats, reconcile_symlinks

logger = structlog.get_logger()


@dataclass
class BlobToDelete:
    """Information about a blob scheduled for deletion."""

    blob_file: Path
    artifact_path: str
    blob_hash: str
    age_days: int
    size: int
    metadata_file: Path | None


@dataclass
class SymlinkFixDetail:
    """Details about symlink fixes for a single artifact."""

    artifact_path: str
    created_tags: list[str] = field(default_factory=list)
    removed_tags: list[str] = field(default_factory=list)
    updated_tags: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        """Format as human-readable string."""
        parts = []
        if self.created_tags:
            parts.append(f"created '{', '.join(self.created_tags)}'")
        if self.removed_tags:
            parts.append(f"removed '{', '.join(self.removed_tags)}'")
        if self.updated_tags:
            parts.append(f"updated '{', '.join(self.updated_tags)}'")
        return f"{self.artifact_path}: {', '.join(parts)}"


@dataclass
class GCResult:
    """Result of a garbage collection operation.

    This dataclass contains all statistics from a GC run, suitable for
    JSON serialization (server) or formatted output (CLI/CTL).
    """

    artifacts_scanned: int = 0
    blobs_found: int = 0
    blobs_deleted: int = 0
    space_reclaimed_bytes: int = 0
    symlinks_checked: int = 0
    symlinks_fixed: int = 0
    items_removed: int = 0
    symlink_fix_details: list[SymlinkFixDetail] = field(default_factory=list)
    cleanup_stats: CleanupStats = field(default_factory=CleanupStats)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization.

        Note: symlink_fix_details and cleanup_stats are intentionally excluded
        from the JSON output. These fields contain detailed information used
        only for CLI/CTL display (e.g., which specific tags were fixed). The
        server API returns just the summary statistics.
        """
        return {
            "artifacts_scanned": self.artifacts_scanned,
            "blobs_found": self.blobs_found,
            "blobs_deleted": self.blobs_deleted,
            "space_reclaimed_bytes": self.space_reclaimed_bytes,
            "symlinks_checked": self.symlinks_checked,
            "symlinks_fixed": self.symlinks_fixed,
            "items_removed": self.items_removed,
        }


# Type alias for progress callbacks
# callback(phase: str, current: int, total: int) where phase is "scan" or "delete"
ProgressCallback = Callable[[str, int, int], None]


def get_blob_age_days(artifact_dir: Path, blob_hash: str, now: datetime) -> int | None:
    """Get blob age in days from metadata or file mtime.

    Args:
        artifact_dir: Path to artifact directory.
        blob_hash: Blob hash (short 8-char format used for file lookup).
        now: Current datetime for comparison.

    Returns:
        Age in days, or None if age cannot be determined.
    """
    blob_file = artifact_dir / "blobs" / blob_hash

    try:
        # Try to get age from metadata
        metadata = read_metadata(artifact_dir, blob_hash)
        upload_time = metadata.uploaded_at
        if upload_time.tzinfo is None:
            upload_time = upload_time.replace(tzinfo=timezone.utc)
        age = now - upload_time
        return age.days
    except (FileNotFoundError, ArtifactNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        # Expected cases: metadata doesn't exist, is malformed, or missing fields
        pass
    except (PermissionError, OSError) as e:
        # Unexpected I/O errors - log and fall back
        logger.warning("gc_metadata_error", blob_hash=blob_hash[:12], error=str(e))

    # Fall back to file mtime
    if blob_file.exists():
        mtime = datetime.fromtimestamp(blob_file.stat().st_mtime, tz=timezone.utc)
        age = now - mtime
        return age.days
    return None


def _scan_artifacts(
    storage_path: Path,
    retention_days: int,
    reconcile_only: bool,
    now: datetime,
    progress_callback: ProgressCallback | None,
) -> tuple[GCResult, list[BlobToDelete], list[Path]]:
    """Scan all artifacts and identify blobs for deletion.

    Args:
        storage_path: Base storage path.
        retention_days: Delete untagged blobs older than this.
        reconcile_only: If True, only reconcile symlinks, don't collect blobs.
        now: Current datetime for age calculations.
        progress_callback: Optional callback for progress updates.

    Returns:
        Tuple of (partial GCResult, blobs to delete, artifact dirs for cleanup).
    """
    result = GCResult()
    blobs_to_delete: list[BlobToDelete] = []
    artifact_dirs_to_cleanup: list[Path] = []

    # Find all artifact directories (containing .magpie manifest)
    manifest_files = list(storage_path.rglob(".magpie"))
    total_manifests = len(manifest_files)

    for idx, manifest_file in enumerate(manifest_files):
        artifact_dir = manifest_file.parent
        artifact_path = str(artifact_dir.relative_to(storage_path))
        result.artifacts_scanned += 1

        logger.debug("gc_processing_artifact", artifact_path=artifact_path)

        # Read manifest to get tagged hashes
        # Manifest stores full hashes, but blobs are stored with short hashes (8 chars)
        manifest = read_manifest(artifact_dir)
        tagged_hashes = {h[:8] for h in manifest.tags.values()}

        # Track artifact directory for cleanup pass
        artifact_dirs_to_cleanup.append(artifact_dir)

        # Reconcile symlinks for this artifact
        stats: ReconcileStats = reconcile_symlinks(artifact_dir, manifest)
        result.symlinks_checked += stats.checked
        result.symlinks_fixed += stats.fixed

        # Record fix details if there were fixes
        if stats.fixed > 0:
            result.symlink_fix_details.append(
                SymlinkFixDetail(
                    artifact_path=artifact_path,
                    created_tags=list(stats.created_tags),
                    removed_tags=list(stats.removed_tags),
                    updated_tags=list(stats.updated_tags),
                )
            )

        if not reconcile_only:
            # Find all blobs in blobs/ directory
            blobs_dir = artifact_dir / "blobs"
            if blobs_dir.exists():
                for blob_file in blobs_dir.iterdir():
                    if not blob_file.is_file():
                        continue

                    result.blobs_found += 1
                    blob_hash = blob_file.name

                    # Check if blob is tagged
                    if blob_hash in tagged_hashes:
                        continue

                    # Get blob age from metadata or file mtime
                    blob_age_days = get_blob_age_days(artifact_dir, blob_hash, now)

                    if blob_age_days is None:
                        logger.debug("gc_skipping_blob", blob_hash=blob_hash[:12], reason="unknown_age")
                        continue

                    # Check if blob is older than retention period
                    if blob_age_days < retention_days:
                        logger.debug(
                            "gc_keeping_blob",
                            blob_hash=blob_hash[:12],
                            age_days=blob_age_days,
                            retention_days=retention_days,
                        )
                        continue

                    # Mark blob for deletion
                    blob_size = blob_file.stat().st_size
                    metadata_file = artifact_dir / "metadata" / f"{blob_hash}.json"

                    blobs_to_delete.append(
                        BlobToDelete(
                            blob_file=blob_file,
                            artifact_path=artifact_path,
                            blob_hash=blob_hash,
                            age_days=blob_age_days,
                            size=blob_size,
                            metadata_file=metadata_file if metadata_file.exists() else None,
                        )
                    )

        # Report progress
        if progress_callback is not None:
            progress_callback("scan", idx + 1, total_manifests)

    return result, blobs_to_delete, artifact_dirs_to_cleanup


def _delete_blobs(
    blobs_to_delete: list[BlobToDelete],
    dry_run: bool,
    progress_callback: ProgressCallback | None,
) -> tuple[int, int]:
    """Delete the collected blobs.

    Args:
        blobs_to_delete: List of blobs to delete.
        dry_run: If True, don't actually delete.
        progress_callback: Optional callback for progress updates.

    Returns:
        Tuple of (blobs deleted count, bytes reclaimed).
    """
    deleted_blobs = 0
    deleted_bytes = 0
    total_blobs = len(blobs_to_delete)

    for idx, blob in enumerate(blobs_to_delete):
        if not dry_run:
            logger.debug(
                "gc_deleting_blob",
                blob_hash=blob.blob_hash[:12],
                age_days=blob.age_days,
                artifact_path=blob.artifact_path,
            )

            blob.blob_file.unlink()

            # Also delete metadata sidecar if it exists
            if blob.metadata_file is not None:
                blob.metadata_file.unlink()

        deleted_blobs += 1
        deleted_bytes += blob.size

        # Report progress
        if progress_callback is not None:
            progress_callback("delete", idx + 1, total_blobs)

    return deleted_blobs, deleted_bytes


def _cleanup_directories(
    artifact_dirs: list[Path],
    storage_path: Path,
    dry_run: bool,
) -> CleanupStats:
    """Clean up empty directories after blob deletion.

    Args:
        artifact_dirs: List of artifact directories to check.
        storage_path: Base storage path.
        dry_run: If True, don't actually remove.

    Returns:
        Combined CleanupStats from all artifacts.
    """
    combined_stats = CleanupStats()

    for artifact_dir in artifact_dirs:
        stats = cleanup_artifact_directories(artifact_dir, storage_path, dry_run)
        combined_stats.empty_blobs_dirs += stats.empty_blobs_dirs
        combined_stats.empty_metadata_dirs += stats.empty_metadata_dirs
        combined_stats.empty_manifests += stats.empty_manifests
        combined_stats.empty_artifact_dirs += stats.empty_artifact_dirs
        combined_stats.empty_parent_dirs += stats.empty_parent_dirs
        combined_stats.removed_paths.extend(stats.removed_paths)

    return combined_stats


def run_gc(
    storage_path: Path,
    retention_days: int,
    dry_run: bool = False,
    reconcile_only: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> tuple[GCResult, list[BlobToDelete]]:
    """Run garbage collection on artifact storage.

    This is the core GC implementation. It:
    1. Scans all artifact directories
    2. Reconciles symlinks to match manifests
    3. Identifies untagged blobs older than retention period
    4. Deletes those blobs (unless dry_run or reconcile_only)
    5. Cleans up empty directories

    Args:
        storage_path: Base storage path containing artifacts.
        retention_days: Delete untagged blobs older than this many days.
        dry_run: If True, preview what would be deleted without making changes.
        reconcile_only: If True, only reconcile symlinks, don't delete blobs.
        progress_callback: Optional callback for progress updates.
            Called with (phase, current, total) where phase is "scan" or "delete".

    Returns:
        Tuple of (GCResult with statistics, list of blobs deleted/to-delete).
        The blob list is useful for dry-run reporting.

    Raises:
        FileNotFoundError: If storage_path does not exist.
    """
    if not storage_path.exists():
        raise FileNotFoundError(f"Storage path does not exist: {storage_path}")

    now = datetime.now(timezone.utc)

    # Phase 1: Scan artifacts
    result, blobs_to_delete, artifact_dirs = _scan_artifacts(
        storage_path=storage_path,
        retention_days=retention_days,
        reconcile_only=reconcile_only,
        now=now,
        progress_callback=progress_callback,
    )

    # Phase 2: Delete blobs (if not reconcile-only)
    if not reconcile_only and blobs_to_delete:
        deleted_blobs, deleted_bytes = _delete_blobs(
            blobs_to_delete=blobs_to_delete,
            dry_run=dry_run,
            progress_callback=progress_callback,
        )
        result.blobs_deleted = deleted_blobs
        result.space_reclaimed_bytes = deleted_bytes

    # Phase 3: Cleanup empty directories
    if not reconcile_only:
        cleanup_stats = _cleanup_directories(artifact_dirs, storage_path, dry_run)
        result.cleanup_stats = cleanup_stats
        result.items_removed = cleanup_stats.total_removed

    # Log GC completion with summary
    logger.info(
        "gc_complete",
        dry_run=dry_run,
        reconcile_only=reconcile_only,
        artifacts_scanned=result.artifacts_scanned,
        blobs_found=result.blobs_found,
        blobs_deleted=result.blobs_deleted,
        space_reclaimed_bytes=result.space_reclaimed_bytes,
        symlinks_checked=result.symlinks_checked,
        symlinks_fixed=result.symlinks_fixed,
        items_removed=result.items_removed,
    )

    return result, blobs_to_delete
