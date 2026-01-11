"""Cleanup utilities for removing empty artifact directories."""

from __future__ import annotations

from pathlib import Path


def cleanup_empty_artifact_dir(artifact_dir: Path, dry_run: bool = False) -> bool:
    """Clean up empty directories in artifact directory structure.

    Removes empty directories in this order:
    1. Empty blobs/ directory
    2. Empty metadata/ directory
    3. .magpie manifest if no tags remain (manifest with no tags)
    4. The artifact directory itself if completely empty
    5. Recursively remove empty parent directories

    Args:
        artifact_dir: Path to artifact directory to clean.
        dry_run: If True, only check if cleanup would occur without making changes.

    Returns:
        True if directory was cleaned up (or would be in dry-run), False otherwise.
    """
    if not artifact_dir.exists():
        return False

    # Track if we made any changes
    cleaned_up = False

    # 1. Remove empty blobs/ directory
    blobs_dir = artifact_dir / "blobs"
    if blobs_dir.exists() and blobs_dir.is_dir():
        if _is_empty_dir(blobs_dir):
            if not dry_run:
                blobs_dir.rmdir()
            cleaned_up = True

    # 2. Remove empty metadata/ directory
    metadata_dir = artifact_dir / "metadata"
    if metadata_dir.exists() and metadata_dir.is_dir():
        if _is_empty_dir(metadata_dir):
            if not dry_run:
                metadata_dir.rmdir()
            cleaned_up = True

    # 3. Remove .magpie manifest if no tags remain
    manifest_file = artifact_dir / ".magpie"
    if manifest_file.exists() and manifest_file.is_file():
        if _manifest_has_no_tags(manifest_file):
            if not dry_run:
                manifest_file.unlink()
            cleaned_up = True

    # 4. Remove artifact directory if completely empty
    if artifact_dir.exists() and artifact_dir.is_dir():
        if _is_empty_dir(artifact_dir):
            if not dry_run:
                artifact_dir.rmdir()
            cleaned_up = True

            # 5. Recursively remove empty parent directories
            if not dry_run:
                _cleanup_empty_parents(artifact_dir.parent)

    return cleaned_up


def _is_empty_dir(dir_path: Path) -> bool:
    """Check if directory is empty.

    Args:
        dir_path: Path to directory to check.

    Returns:
        True if directory exists and is empty, False otherwise.
    """
    if not dir_path.exists() or not dir_path.is_dir():
        return False

    try:
        # Check if any items exist in the directory
        next(dir_path.iterdir())
        return False
    except StopIteration:
        return True


def _manifest_has_no_tags(manifest_file: Path) -> bool:
    """Check if manifest file has no tags.

    Args:
        manifest_file: Path to .magpie manifest file.

    Returns:
        True if manifest exists and has no tags, False otherwise.
    """
    if not manifest_file.exists() or not manifest_file.is_file():
        return False

    try:
        import json

        content = manifest_file.read_text(encoding="utf-8")
        data = json.loads(content)

        # Check if tags dict is empty or missing
        tags = data.get("tags", {})
        return len(tags) == 0
    except Exception:
        # If we can't read/parse the manifest, don't delete it
        return False


def _cleanup_empty_parents(dir_path: Path) -> None:
    """Recursively remove empty parent directories.

    Stops when reaching a non-empty directory or the storage root.

    Args:
        dir_path: Path to start cleaning from.
    """
    if not dir_path.exists() or not dir_path.is_dir():
        return

    # Don't remove the root storage directory
    # Check if this looks like a storage root (contains many artifact dirs)
    # or if we've reached a system boundary
    if _is_storage_root(dir_path):
        return

    # Only remove if empty
    if _is_empty_dir(dir_path):
        try:
            dir_path.rmdir()
            # Recurse to parent
            _cleanup_empty_parents(dir_path.parent)
        except OSError:
            # If we can't remove (permissions, etc.), stop
            pass


def _is_storage_root(dir_path: Path) -> bool:
    """Check if directory appears to be storage root.

    Heuristics:
    - Has many direct subdirectories (likely artifact paths)
    - Path depth suggests it's near the storage root

    Args:
        dir_path: Path to check.

    Returns:
        True if directory appears to be storage root, False otherwise.
    """
    if not dir_path.exists() or not dir_path.is_dir():
        return False

    # Conservative check: if we have more than 3 subdirectories,
    # it's likely a storage root or major branch
    try:
        subdirs = [p for p in dir_path.iterdir() if p.is_dir()]
        if len(subdirs) > 3:
            return True
    except OSError:
        pass

    # Also check if the directory name suggests it's a storage root
    # (e.g., "storage", "artifacts", etc.)
    if dir_path.name in ("storage", "artifacts", "data"):
        return True

    return False
