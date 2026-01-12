"""Path utilities for artifact storage layout."""

from __future__ import annotations

import re
from pathlib import Path

from magpie.storage.exceptions import InvalidArtifactPathError


def normalize_artifact_path(path: str) -> str:
    """Normalize artifact path, stripping leading/trailing slashes.

    This function provides consistent path normalization for artifact paths
    across CLI commands and server routes. It handles common input variations
    like leading slashes, trailing slashes, and multiple consecutive slashes.

    Args:
        path: Raw artifact path string from user input.

    Returns:
        Normalized path with leading/trailing slashes removed and
        multiple slashes collapsed to single slashes.

    Raises:
        InvalidArtifactPathError: If path contains traversal (..) or is empty.

    Examples:
        >>> normalize_artifact_path("/test/artifact")
        'test/artifact'
        >>> normalize_artifact_path("test/artifact/")
        'test/artifact'
        >>> normalize_artifact_path("//test//artifact//")
        'test/artifact'
        >>> normalize_artifact_path("test/../other")
        Raises InvalidArtifactPathError
        >>> normalize_artifact_path("")
        Raises InvalidArtifactPathError
    """
    # Strip leading and trailing slashes
    path = path.strip("/")

    # Collapse multiple consecutive slashes to single slash
    path = re.sub(r"/+", "/", path)

    # Check for path traversal attempts
    if ".." in path:
        raise InvalidArtifactPathError("Path traversal '..' is not allowed in artifact paths")

    # Check for empty path after normalization
    if not path:
        raise InvalidArtifactPathError("Artifact path cannot be empty")

    return path


# Reserved directory names that cannot appear in artifact paths
RESERVED_SEGMENTS = {"blobs", "metadata", ".magpie"}


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


def validate_artifact_path(artifact_path: str) -> None:
    """Validate that an artifact path is safe and does not use reserved names.

    This checks that the path does not contain reserved directory names
    (blobs, metadata, .magpie) or hidden segments starting with a dot.

    Args:
        artifact_path: Logical artifact path to validate.

    Raises:
        InvalidArtifactPathError: If path contains reserved or invalid segments.
    """
    if not artifact_path or artifact_path.strip() == "":
        raise InvalidArtifactPathError("Artifact path cannot be empty")

    segments = artifact_path.split("/")

    for segment in segments:
        if not segment or segment.strip() == "":
            raise InvalidArtifactPathError("Artifact path cannot contain empty segments")

        # Reject path traversal attempts
        if segment == "..":
            raise InvalidArtifactPathError("Path traversal '..' is not allowed in artifact paths")

        if segment in RESERVED_SEGMENTS:
            raise InvalidArtifactPathError(
                f"'{segment}' is a reserved name and cannot be used in artifact paths"
            )

        if segment.startswith("."):
            raise InvalidArtifactPathError("Path segments cannot start with '.'")


def check_artifact_nesting(base: Path, artifact_path: str) -> None:
    """Check that artifact path does not nest with existing artifacts.

    Prevents creating artifacts that:
    1. Are children of existing artifacts (e.g., test/myartifact/nested when
       test/myartifact exists)
    2. Are parents of existing artifacts (e.g., test when test/myartifact exists)

    Note:
        The parent check uses rglob to search for nested .magpie manifests.
        For high-level paths with many nested artifacts, this may have
        performance implications. In practice, this is acceptable because:
        1. This check only runs on artifact creation (not reads)
        2. Deep nesting is uncommon in typical usage patterns
        3. The check prevents data corruption from conflicting paths

    Args:
        base: Base storage directory path.
        artifact_path: Logical artifact path to check.

    Raises:
        InvalidArtifactPathError: If path would nest with existing artifacts.
    """
    proposed_dir = artifact_dir_path(base, artifact_path)

    # Check if proposed path is a child of an existing artifact
    # (test/myartifact/nested when test/myartifact exists)
    current = proposed_dir.parent
    while current != base and current.parent != current:
        if manifest_path(current).exists():
            relative = current.relative_to(base)
            raise InvalidArtifactPathError(
                f"Cannot create artifact '{artifact_path}' - "
                f"would be nested under existing artifact '{relative}'"
            )
        current = current.parent

    # Check if proposed path is a parent of existing artifacts
    # (test when test/myartifact exists)
    if proposed_dir.exists():
        for manifest_file in proposed_dir.rglob(".magpie"):
            # Skip if this is the artifact itself
            if manifest_file.parent == proposed_dir:
                continue
            # Found a nested artifact
            nested_artifact = manifest_file.parent.relative_to(base)
            raise InvalidArtifactPathError(
                f"Cannot create artifact '{artifact_path}' - "
                f"existing artifact '{nested_artifact}' would be nested under it"
            )
