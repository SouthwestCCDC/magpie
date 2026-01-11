"""GC endpoint for garbage collecting untagged blobs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from magpie.auth.service import TokenInfo
from magpie.config import MagpieSettings, get_settings
from magpie.server.deps import require_admin_scope
from magpie.storage.cleanup import cleanup_artifact_directories
from magpie.storage.manifest import read_manifest
from magpie.storage.metadata import read_metadata
from magpie.storage.symlinks import reconcile_symlinks

router = APIRouter()


class GCResponse(BaseModel):
    """Response model for GC operation."""

    dry_run: bool
    artifacts_scanned: int
    blobs_found: int
    blobs_deleted: int
    space_reclaimed_bytes: int
    directories_removed: int = 0


@router.post("/api/v1/gc")
async def trigger_gc(
    dry_run: Annotated[bool, Query()] = False,
    retention_days_override: Annotated[int | None, Query(ge=0, alias="retention_days")] = None,
    admin: Annotated[TokenInfo, Depends(require_admin_scope)] = None,
    settings: Annotated[MagpieSettings, Depends(get_settings)] = None,
) -> GCResponse:
    """Trigger garbage collection (admin only).

    Walks all artifact directories and:
    - Identifies blobs not referenced by any tag
    - Deletes untagged blobs older than retention_days (default 90)
    - Reconciles symlinks to match manifests

    Args:
        dry_run: If True, preview what would be deleted without making changes.
        retention_days: Override retention period (days). Defaults to config value.
        admin: Validated admin token (injected by dependency).
        settings: Application settings (injected by dependency).

    Returns:
        GCResponse with GC operation statistics.
    """
    storage_path = settings.storage_path
    # Use query parameter if provided, otherwise fall back to config
    retention_days = (
        retention_days_override if retention_days_override is not None else settings.retention_days
    )

    # Track statistics
    total_artifacts = 0
    total_blobs_found = 0
    deleted_blobs = 0
    deleted_bytes = 0
    directories_removed = 0

    now = datetime.now(timezone.utc)

    # Collect artifact directories for cleanup pass
    artifact_dirs_to_cleanup: list[Path] = []

    # Find all artifact directories (containing .magpie manifest)
    if storage_path.exists():
        for manifest_file in storage_path.rglob(".magpie"):
            artifact_dir = manifest_file.parent
            total_artifacts += 1

            # Read manifest to get tagged hashes
            manifest = read_manifest(artifact_dir)
            tagged_hashes = set(manifest.tags.values())

            # Reconcile symlinks for this artifact
            reconcile_symlinks(artifact_dir, manifest)

            # Track artifact directory for cleanup pass
            artifact_dirs_to_cleanup.append(artifact_dir)

            # Find all blobs in blobs/ directory
            blobs_dir = artifact_dir / "blobs"
            if not blobs_dir.exists():
                continue

            for blob_file in blobs_dir.iterdir():
                if not blob_file.is_file():
                    continue

                total_blobs_found += 1
                blob_hash = blob_file.name

                # Check if blob is tagged
                if blob_hash in tagged_hashes:
                    continue

                # Get blob age from metadata or file mtime
                blob_age_days = _get_blob_age_days(artifact_dir, blob_hash, now)

                if blob_age_days is None:
                    # Can't determine age, skip
                    continue

                # Check if blob is older than retention period
                if blob_age_days < retention_days:
                    continue

                # Delete old untagged blob
                blob_size = blob_file.stat().st_size

                if not dry_run:
                    blob_file.unlink()

                    # Also delete metadata sidecar if it exists
                    metadata_file = artifact_dir / "metadata" / f"{blob_hash}.json"
                    if metadata_file.exists():
                        metadata_file.unlink()

                deleted_blobs += 1
                deleted_bytes += blob_size

        # Cleanup pass: remove empty directories after blob deletion
        for artifact_dir in artifact_dirs_to_cleanup:
            stats = cleanup_artifact_directories(artifact_dir, storage_path, dry_run)
            directories_removed += stats.total_removed

    return GCResponse(
        dry_run=dry_run,
        artifacts_scanned=total_artifacts,
        blobs_found=total_blobs_found,
        blobs_deleted=deleted_blobs,
        space_reclaimed_bytes=deleted_bytes,
        directories_removed=directories_removed,
    )


def _get_blob_age_days(artifact_dir, blob_hash: str, now: datetime) -> int | None:
    """Get blob age in days from metadata or file mtime.

    Args:
        artifact_dir: Path to artifact directory.
        blob_hash: Full blob hash.
        now: Current datetime for comparison.

    Returns:
        Age in days, or None if age cannot be determined.
    """
    try:
        # Try to get age from metadata
        metadata = read_metadata(artifact_dir, blob_hash)
        upload_time = metadata.uploaded_at
        if upload_time.tzinfo is None:
            upload_time = upload_time.replace(tzinfo=timezone.utc)
        age = now - upload_time
        return age.days
    except Exception:
        # Fall back to file mtime
        blob_file = artifact_dir / "blobs" / blob_hash
        if blob_file.exists():
            mtime = datetime.fromtimestamp(blob_file.stat().st_mtime, tz=timezone.utc)
            age = now - mtime
            return age.days
        return None
