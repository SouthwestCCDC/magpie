"""Path utilities for artifact storage layout."""

from __future__ import annotations

from pathlib import Path


def artifact_dir_path(base: Path, artifact_path: str) -> Path:
    """Construct artifact directory path from base and artifact path.

    Args:
        base: Base storage directory path.
        artifact_path: Logical artifact path (e.g., "project/component/artifact").

    Returns:
        Full path to artifact directory.
    """
    return base / artifact_path


def blob_path(artifact_dir: Path, hash_ref: str) -> Path:
    """Get path to blob file for a given hash reference.

    Args:
        artifact_dir: Artifact directory path.
        hash_ref: Hash reference string (e.g., '@abc12345' or full hash).

    Returns:
        Path to the blob file.
    """
    # Strip @ prefix if present for filename
    hash_name = hash_ref.lstrip("@")
    return artifact_dir / "blobs" / hash_name


def metadata_path(artifact_dir: Path, hash_ref: str) -> Path:
    """Get path to metadata JSON sidecar for a given hash reference.

    Args:
        artifact_dir: Artifact directory path.
        hash_ref: Hash reference string (e.g., '@abc12345' or full hash).

    Returns:
        Path to the metadata JSON file.
    """
    # Strip @ prefix if present for filename
    hash_name = hash_ref.lstrip("@")
    return artifact_dir / "metadata" / f"{hash_name}.json"


def manifest_path(artifact_dir: Path) -> Path:
    """Get path to manifest file for an artifact directory.

    Args:
        artifact_dir: Artifact directory path.

    Returns:
        Path to the .magpie manifest file.
    """
    return artifact_dir / ".magpie"
