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

    # Check for path traversal attempts using segment-based validation
    # This avoids false positives for legitimate paths like "v1..2" or "test..file"
    segments = path.split("/") if path else []
    if any(segment == ".." for segment in segments):
        raise InvalidArtifactPathError("Path traversal '..' is not allowed in artifact paths")

    # Check for empty path after normalization
    if not path:
        raise InvalidArtifactPathError("Artifact path cannot be empty")

    return path


def verify_path_is_descendant(base: Path, artifact_path: str) -> Path:
    """Verify that a constructed path is strictly a descendant of the base directory.

    This provides defense-in-depth against path traversal attacks by checking
    the resolved path rather than just token-based validation. After constructing
    the full path, this function verifies it remains within the base directory.

    Note:
        This function expects artifact_path to be pre-normalized via
        normalize_artifact_path(). Paths containing ".." segments should be
        rejected by normalize_artifact_path before reaching this function.
        This function provides an additional safety layer to catch any
        traversal attempts that might bypass token-based validation.

    Args:
        base: Base storage directory path (must be absolute).
        artifact_path: Normalized artifact path string (no ".." segments).

    Returns:
        The unresolved (logical) path after verifying that any existing symlinks
        in the path do not escape the base directory. Returns ``base / artifact_path``
        rather than a resolved path, since the artifact may not exist yet.

    Raises:
        InvalidArtifactPathError: If the resolved path escapes the base directory,
            or if the path contains cyclic symlinks.

    Example:
        >>> base = Path("/storage/artifacts")
        >>> verify_path_is_descendant(base, "project/artifact")
        PosixPath('/storage/artifacts/project/artifact')
    """
    # Resolve base path first (must exist)
    try:
        base_resolved = base.resolve(strict=True)
    except OSError as e:
        raise InvalidArtifactPathError(f"Base path cannot be resolved: {e}")

    # Walk through each segment of the artifact path and verify that
    # any existing symlinks don't escape the base directory.
    # This catches both symlink escapes AND cyclic symlinks.
    current_path = base_resolved
    segments = artifact_path.split("/") if artifact_path else []

    for segment in segments:
        current_path = current_path / segment

        # If this component exists, resolve it strictly to detect:
        # 1. Symlinks that escape the base directory
        # 2. Cyclic symlinks (will raise OSError with ELOOP)
        if current_path.exists() or current_path.is_symlink():
            try:
                resolved = current_path.resolve(strict=True)
            except OSError as e:
                # ELOOP (cyclic symlinks) or other resolution errors
                raise InvalidArtifactPathError(f"Path '{artifact_path}' cannot be resolved: {e}")

            # Verify the resolved path stays within base
            try:
                resolved.relative_to(base_resolved)
            except ValueError:
                raise InvalidArtifactPathError(
                    f"Path '{artifact_path}' resolves outside the storage directory"
                )

    # Final path construction (may include non-existent components)
    full_path = base_resolved / artifact_path

    # For non-existent paths, also check using non-strict resolve to catch
    # ".." segments that could escape the base directory
    try:
        resolved_full = full_path.resolve(strict=False)
    except OSError as e:
        raise InvalidArtifactPathError(f"Path '{artifact_path}' cannot be resolved: {e}")

    try:
        resolved_full.relative_to(base_resolved)
    except ValueError:
        raise InvalidArtifactPathError(
            f"Path '{artifact_path}' resolves outside the storage directory"
        )

    # Additional check: ensure it's not the base directory itself
    if resolved_full == base_resolved:
        raise InvalidArtifactPathError("Artifact path cannot resolve to storage root")

    return full_path


# Reserved directory names that cannot appear in artifact paths
RESERVED_SEGMENTS = {"blobs", "metadata", ".magpie"}


def artifact_dir_path(base: Path, artifact_path: str, verify_security: bool = True) -> Path:
    """Construct artifact directory path from base and artifact path.

    Security: By default, this function verifies that the resolved path remains
    within the storage base directory. This protects against symlink attacks
    where a symlink inside storage could point to files outside storage.

    Args:
        base: Base storage directory path.
        artifact_path: Logical artifact path (e.g., "project/component/artifact").
        verify_security: If True (default), verify the resolved path (following
            symlinks) stays within ``base``. This must remain enabled for any
            path derived from user input or external callers.

            Set to False only for trusted internal operations that:

            * never accept untrusted/user-supplied paths, and
            * operate on paths that have already been validated and persisted
              (for example, paths recovered from an internal index or metadata
              store that was created using :func:`verify_path_is_descendant`).

            Example (internal maintenance job)::

                base = Path("/var/lib/magpie/storage")
                # 'stored_path' is read from Magpie's own metadata and was
                # originally created via normalize_artifact_path() and
                # verify_path_is_descendant(), not from user input.
                stored_path = "project/component/artifact"
                artifact_dir_path(base, stored_path, verify_security=False)
                # Returns: PosixPath('/var/lib/magpie/storage/project/component/artifact')

            Do not disable security checks for raw request parameters or CLI
            arguments.

    Returns:
        Full path to artifact directory.

    Raises:
        InvalidArtifactPathError: If verify_security is True and the resolved
            path would escape the base directory (e.g., via symlink), or if
            the path contains cyclic symlinks.
    """
    result = base / artifact_path
    if verify_security:
        # Delegate security verification to shared helper to avoid duplication.
        # This will raise InvalidArtifactPathError if the resolved path escapes base
        # or if there are cyclic symlinks.
        verify_path_is_descendant(base, artifact_path)
    return result


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
