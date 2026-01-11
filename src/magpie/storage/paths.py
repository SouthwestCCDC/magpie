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

    Blobs are stored using only the first 8 characters of the hash.
    This function accepts either a short hash (@abc12345 or abc12345)
    or a full 64-character hash and normalizes to the 8-char filename.

    Args:
        artifact_dir: Artifact directory path.
        hash_ref: Hash reference string (e.g., '@abc12345' or full hash).

    Returns:
        Path to the blob file.
    """
    # Strip @ prefix if present, then use first 8 chars
    hash_name = hash_ref.lstrip("@")[:8]
    return artifact_dir / "blobs" / hash_name


def metadata_path(artifact_dir: Path, hash_ref: str) -> Path:
    """Get path to metadata JSON sidecar for a given hash reference.

    Metadata files are stored using only the first 8 characters of the hash,
    matching the blob storage scheme. This function accepts either a short
    hash (@abc12345 or abc12345) or a full 64-character hash.

    Args:
        artifact_dir: Artifact directory path.
        hash_ref: Hash reference string (e.g., '@abc12345' or full hash).

    Returns:
        Path to the metadata JSON file.
    """
    # Strip @ prefix if present, then use first 8 chars
    hash_name = hash_ref.lstrip("@")[:8]
    return artifact_dir / "metadata" / f"{hash_name}.json"


def manifest_path(artifact_dir: Path) -> Path:
    """Get path to manifest file for an artifact directory.

    Args:
        artifact_dir: Artifact directory path.

    Returns:
        Path to the .magpie manifest file.
    """
    return artifact_dir / ".magpie"
