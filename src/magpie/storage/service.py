"""High-level storage service integrating all storage components."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import structlog

from magpie.storage.blob import store_blob, store_blob_from_temp
from magpie.storage.exceptions import (
    ArtifactNotFoundError,
    InvalidArtifactPathError,
    ManifestCorruptError,
)
from magpie.storage.hash import short_hash
from magpie.storage.manifest import read_manifest, remove_tag, update_tag
from magpie.storage.metadata import (
    BlobMetadata,
    read_metadata,
    update_metadata,
    write_metadata,
)
from magpie.storage.paths import (
    artifact_dir_path,
    check_artifact_nesting,
    validate_artifact_path,
    verify_path_is_descendant,
)
from magpie.storage.symlinks import reconcile_symlinks
from magpie.validation import validate_tag_name

logger = structlog.get_logger()

if TYPE_CHECKING:
    from magpie.config import MagpieSettings


@dataclass
class ArtifactInfo:
    """Information about a stored artifact blob."""

    hash: str  # Full SHA-256 hash
    hash_ref: str  # Short hash ref like @abc12345
    tags: list[str]  # Tags pointing to this blob
    uploaded_by: str
    uploaded_at: datetime
    source_uri: str | None


@dataclass
class FlushResult:
    """Result of a flush_tag operation."""

    tag_name: str
    affected_artifacts: list[str]  # List of artifact paths that had tag removed
    count: int  # Number of artifacts affected


class StorageService:
    """High-level storage API combining all storage components.

    This is the main storage interface used by server and CLI.
    Coordinates blob storage, metadata, manifests, and symlinks.
    """

    def __init__(self, config: MagpieSettings) -> None:
        """Initialize storage service.

        Args:
            config: MagpieSettings instance with storage configuration.
        """
        self.config = config

    def _validate_and_prepare_artifact_path(self, artifact_path: str) -> Path:
        """Validate artifact path and return the artifact directory.

        Args:
            artifact_path: Logical path for the artifact.

        Returns:
            Path to artifact directory.

        Raises:
            InvalidArtifactPathError: If path contains reserved names or conflicts
                with existing artifacts.
        """
        # Validate artifact path for reserved names
        validate_artifact_path(artifact_path)

        # Check for nesting conflicts with existing artifacts.
        # Skip security verification here since artifact_dir_path below will
        # perform it; this avoids redundant segment-by-segment symlink resolution.
        check_artifact_nesting(self.config.storage_path, artifact_path, verify_security=False)

        # Security verification happens here via verify_path_is_descendant
        return artifact_dir_path(self.config.storage_path, artifact_path)

    def _finalize_artifact_storage(
        self,
        artifact_dir: Path,
        full_hash: str,
        hash_ref: str,
        is_duplicate: bool,
        uploaded_by: str,
        source_uri: str | None,
        no_latest: bool,
    ) -> tuple[ArtifactInfo, bool]:
        """Finalize artifact storage after blob is written.

        This helper handles the common post-blob logic shared by store_artifact
        and store_artifact_from_temp: metadata writing, tag updates, and
        ArtifactInfo construction.

        Args:
            artifact_dir: Path to artifact directory.
            full_hash: Full SHA-256 hash of the blob.
            hash_ref: Short hash reference (e.g., "@abc12345").
            is_duplicate: Whether the blob already existed.
            uploaded_by: Identity of uploader.
            source_uri: Optional source URI for provenance.
            no_latest: If True, skip creating/updating the "latest" tag.

        Returns:
            Tuple of (ArtifactInfo, is_duplicate).
        """
        # Write metadata sidecar (only for new blobs, write_metadata is write-once)
        # Use hash_ref (short hash) for filename, but store full_hash inside
        metadata = BlobMetadata(
            hash=full_hash,
            uploaded_by=uploaded_by,
            uploaded_at=datetime.now(timezone.utc),
            source_uri=source_uri,
        )
        write_metadata(artifact_dir, hash_ref, metadata)

        # Conditionally update manifest with "latest" tag
        if not no_latest:
            # Store full hash for verification, symlinks will extract first 8 chars
            manifest = update_tag(artifact_dir, "latest", full_hash)
            # Reconcile symlinks to match manifest
            reconcile_symlinks(artifact_dir, manifest)

        # Build artifact info
        tags = self._get_tags_for_hash(artifact_dir, full_hash)

        # Re-read metadata to get actual stored values (in case it was duplicate)
        stored_metadata = read_metadata(artifact_dir, hash_ref)

        info = ArtifactInfo(
            hash=full_hash,
            hash_ref=hash_ref,
            tags=tags,
            uploaded_by=stored_metadata.uploaded_by,
            uploaded_at=stored_metadata.uploaded_at,
            source_uri=stored_metadata.source_uri,
        )

        return (info, is_duplicate)

    def store_artifact(
        self,
        artifact_path: str,
        file_stream: BinaryIO,
        uploaded_by: str,
        source_uri: str | None = None,
        no_latest: bool = False,
    ) -> tuple[ArtifactInfo, bool]:
        """Store an artifact with optional automatic tagging as 'latest'.

        Args:
            artifact_path: Logical path for the artifact (e.g., "project/component").
            file_stream: Binary stream of artifact content.
            uploaded_by: Identity of uploader.
            source_uri: Optional source URI for provenance.
            no_latest: If True, skip creating/updating the "latest" tag (default: False).

        Returns:
            Tuple of (ArtifactInfo, is_duplicate):
            - ArtifactInfo with full artifact details
            - is_duplicate: True if blob already existed

        Raises:
            InvalidArtifactPathError: If path contains reserved names or conflicts
                with existing artifacts.
        """
        # Validate artifact path and check for nesting conflicts
        artifact_dir = self._validate_and_prepare_artifact_path(artifact_path)

        # Store blob (handles streaming and hashing)
        # Returns full hash for metadata, short hash ref for display
        full_hash, hash_ref, is_duplicate = store_blob(artifact_dir, file_stream, self.config)

        # Finalize storage (metadata, tags, info construction)
        return self._finalize_artifact_storage(
            artifact_dir=artifact_dir,
            full_hash=full_hash,
            hash_ref=hash_ref,
            is_duplicate=is_duplicate,
            uploaded_by=uploaded_by,
            source_uri=source_uri,
            no_latest=no_latest,
        )

    def store_artifact_from_temp(
        self,
        artifact_path: str,
        temp_file_path: Path,
        full_hash: str,
        uploaded_by: str,
        source_uri: str | None = None,
        no_latest: bool = False,
    ) -> tuple[ArtifactInfo, bool]:
        """Store an artifact from a pre-written temp file with pre-computed hash.

        This method is used by the streaming upload handler which writes chunks
        directly to a temp file while computing the hash incrementally.

        Args:
            artifact_path: Logical path for the artifact (e.g., "project/component").
            temp_file_path: Path to pre-written temp file.
            full_hash: Pre-computed SHA-256 hash of the file content.
            uploaded_by: Identity of uploader.
            source_uri: Optional source URI for provenance.
            no_latest: If True, skip creating/updating the "latest" tag (default: False).

        Returns:
            Tuple of (ArtifactInfo, is_duplicate):
            - ArtifactInfo with full artifact details
            - is_duplicate: True if blob already existed

        Raises:
            InvalidArtifactPathError: If path contains reserved names or conflicts
                with existing artifacts.
        """
        # Validate artifact path and check for nesting conflicts
        artifact_dir = self._validate_and_prepare_artifact_path(artifact_path)

        # Store blob from temp file (handles atomic move)
        # Returns short hash ref for display
        hash_ref, is_duplicate = store_blob_from_temp(artifact_dir, temp_file_path, full_hash)

        # Finalize storage (metadata, tags, info construction)
        return self._finalize_artifact_storage(
            artifact_dir=artifact_dir,
            full_hash=full_hash,
            hash_ref=hash_ref,
            is_duplicate=is_duplicate,
            uploaded_by=uploaded_by,
            source_uri=source_uri,
            no_latest=no_latest,
        )

    def list_artifacts(self, artifact_path: str) -> list[ArtifactInfo]:
        """List all artifact versions at a path.

        Args:
            artifact_path: Logical path for the artifact.

        Returns:
            List of ArtifactInfo for each unique blob, with tags grouped by hash.
            Includes blobs without tags (shown with empty tags list).
            Returns empty list if artifact path doesn't exist.
        """
        artifact_dir = artifact_dir_path(self.config.storage_path, artifact_path)

        if not artifact_dir.exists():
            return []

        metadata_dir = artifact_dir / "metadata"
        if not metadata_dir.exists():
            return []

        # Read manifest to build hash-to-tags mapping
        manifest = read_manifest(artifact_dir)
        hash_to_tags: dict[str, list[str]] = {}
        for tag_name, full_hash in manifest.tags.items():
            if full_hash not in hash_to_tags:
                hash_to_tags[full_hash] = []
            hash_to_tags[full_hash].append(tag_name)

        # Enumerate all metadata files to find all blobs (including untagged)
        results: list[ArtifactInfo] = []
        for metadata_file in metadata_dir.iterdir():
            if not metadata_file.is_file():
                continue
            if metadata_file.suffix != ".json":
                continue

            # Extract short hash from filename (e.g., "abc12345.json" -> "abc12345")
            short_hash_name = metadata_file.stem
            hash_ref = f"@{short_hash_name}"

            try:
                metadata = read_metadata(artifact_dir, hash_ref)
                full_hash = metadata.hash

                # Look up tags for this hash (empty list if untagged)
                tags = hash_to_tags.get(full_hash, [])

                info = ArtifactInfo(
                    hash=full_hash,
                    hash_ref=hash_ref,
                    tags=sorted(tags),
                    uploaded_by=metadata.uploaded_by,
                    uploaded_at=metadata.uploaded_at,
                    source_uri=metadata.source_uri,
                )
                results.append(info)
            except ArtifactNotFoundError:
                # Skip files that can't be read as metadata
                continue

        return results

    def get_artifact_info(self, artifact_path: str, ref: str) -> ArtifactInfo:
        """Get artifact info by tag name or hash reference.

        Args:
            artifact_path: Logical path for the artifact.
            ref: Tag name or hash reference (prefixed with @).

        Returns:
            ArtifactInfo for the resolved artifact.

        Raises:
            ArtifactNotFoundError: If ref doesn't resolve to an existing blob.
        """
        artifact_dir = artifact_dir_path(self.config.storage_path, artifact_path)

        if not artifact_dir.exists():
            raise ArtifactNotFoundError(f"Artifact path not found: {artifact_path}")

        manifest = read_manifest(artifact_dir)

        # Resolve ref to full hash
        if ref.startswith("@"):
            # Direct hash reference - use as short hash for lookup
            hash_ref = ref
            # Resolve to full hash from metadata (for verification)
            metadata = read_metadata(artifact_dir, hash_ref)
            full_hash = metadata.hash
        else:
            # Tag name - look up in manifest (stores full hash)
            if ref not in manifest.tags:
                raise ArtifactNotFoundError(f"Tag '{ref}' not found in artifact {artifact_path}")
            full_hash = manifest.tags[ref]
            # Convert to short hash for metadata lookup
            hash_ref = short_hash(full_hash)
            metadata = read_metadata(artifact_dir, hash_ref)

        # Get all tags pointing to this hash
        tags = self._get_tags_for_hash(artifact_dir, full_hash)

        return ArtifactInfo(
            hash=full_hash,
            hash_ref=short_hash(full_hash),
            tags=tags,
            uploaded_by=metadata.uploaded_by,
            uploaded_at=metadata.uploaded_at,
            source_uri=metadata.source_uri,
        )

    def create_tag(self, artifact_path: str, hash_ref: str, tag_name: str) -> ArtifactInfo:
        """Create or update a tag pointing to a specific blob.

        Args:
            artifact_path: Logical path for the artifact.
            hash_ref: Hash reference (short @abc123 or full hash) to tag.
            tag_name: Name for the tag (e.g., "stable", "v1.0").

        Returns:
            ArtifactInfo for the tagged blob.

        Raises:
            ArtifactNotFoundError: If hash_ref doesn't resolve to existing blob.
            ValidationError: If tag name fails validation (invalid format/length).
        """
        # Validate tag name (defense in depth - also validated at API layer)
        validate_tag_name(tag_name)

        artifact_dir = artifact_dir_path(self.config.storage_path, artifact_path)

        # Validate blob exists and get short hash for metadata lookup
        short_hash_name = self._validate_blob_exists(artifact_dir, hash_ref)

        # Read metadata to get full hash and other info
        metadata = read_metadata(artifact_dir, short_hash_name)
        full_hash = metadata.hash

        # Update manifest with tag (uses full hash for verification)
        manifest = update_tag(artifact_dir, tag_name, full_hash)

        # Reconcile symlinks to match manifest
        reconcile_symlinks(artifact_dir, manifest)

        # Get all tags pointing to this hash
        tags = self._get_tags_for_hash(artifact_dir, full_hash)

        logger.info(
            "tag_created",
            operation="create_tag",
            tag_name=tag_name,
            artifact_path=artifact_path,
            hash_ref=short_hash_name,
        )

        return ArtifactInfo(
            hash=full_hash,
            hash_ref=short_hash(full_hash),
            tags=tags,
            uploaded_by=metadata.uploaded_by,
            uploaded_at=metadata.uploaded_at,
            source_uri=metadata.source_uri,
        )

    def remove_tag(self, artifact_path: str, tag_name: str) -> bool:
        """Remove a tag from an artifact.

        Args:
            artifact_path: Logical path for the artifact.
            tag_name: Name of tag to remove.

        Returns:
            True if tag was removed, False if tag didn't exist.

        Raises:
            ValidationError: If tag name fails validation (invalid format/length).
        """
        # Validate tag name (defense in depth - also validated at API layer)
        validate_tag_name(tag_name)

        artifact_dir = artifact_dir_path(self.config.storage_path, artifact_path)

        # Read manifest to check if tag exists
        manifest = read_manifest(artifact_dir)

        if tag_name not in manifest.tags:
            logger.warning(
                "tag_not_found",
                tag_name=tag_name,
                artifact_path=artifact_path,
                message="Tag not found, nothing to remove",
            )
            return False

        # Remove tag from manifest
        manifest = remove_tag(artifact_dir, tag_name)

        # Reconcile symlinks to remove the symlink
        reconcile_symlinks(artifact_dir, manifest)

        logger.info(
            "tag_removed",
            tag_name=tag_name,
            artifact_path=artifact_path,
        )

        return True

    def flush_tag(self, tag_name: str, dry_run: bool = False) -> FlushResult:
        """Remove a tag from all artifacts globally.

        This is a potentially destructive operation that walks the entire
        storage tree and removes the specified tag from every artifact.

        Args:
            tag_name: Name of tag to remove globally.
            dry_run: If True, identify affected artifacts without modifying.

        Returns:
            FlushResult with list of affected artifact paths and count.

        Raises:
            ValidationError: If tag name fails validation (invalid format/length).
        """
        # Validate tag name (defense in depth - also validated at API layer)
        validate_tag_name(tag_name)

        affected_artifacts: list[str] = []

        # Find all .magpie manifest files under storage_path
        for manifest_file in self.config.storage_path.rglob(".magpie"):
            # Get artifact directory (parent of .magpie file)
            artifact_dir = manifest_file.parent

            # Compute artifact path relative to storage_path
            artifact_path = str(artifact_dir.relative_to(self.config.storage_path))

            # Read manifest to check if tag exists. Mirrors gc.py's handling: skip
            # artifacts with corrupt manifests or I/O errors and continue the walk,
            # rather than letting one bad artifact fail this global operation for
            # every other artifact in the tree.
            try:
                manifest = read_manifest(artifact_dir)
            except (ManifestCorruptError, OSError) as e:
                logger.warning(
                    "flush_tag_skipping_corrupt_manifest",
                    artifact_path=artifact_path,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                continue

            if tag_name in manifest.tags:
                affected_artifacts.append(artifact_path)

                if not dry_run:
                    # Actually remove the tag
                    self.remove_tag(artifact_path, tag_name)

        logger.info(
            "tag_flushed",
            operation="flush_tag",
            tag_name=tag_name,
            dry_run=dry_run,
            affected_count=len(affected_artifacts),
        )

        return FlushResult(
            tag_name=tag_name,
            affected_artifacts=affected_artifacts,
            count=len(affected_artifacts),
        )

    def amend_metadata(
        self,
        artifact_path: str,
        hash_ref: str,
        source_uri: str | None = None,
    ) -> ArtifactInfo:
        """Update metadata fields on an existing blob.

        Preserves immutable fields (hash, uploaded_by, uploaded_at) while
        allowing updates to mutable fields (source_uri).

        Args:
            artifact_path: Logical path for the artifact.
            hash_ref: Hash reference to the blob.
            source_uri: New source URI (None to leave unchanged).

        Returns:
            Updated ArtifactInfo.

        Raises:
            ArtifactNotFoundError: If hash_ref doesn't resolve.
        """
        artifact_dir = artifact_dir_path(self.config.storage_path, artifact_path)

        # Validate blob exists and get short hash for metadata lookup
        short_hash_name = self._validate_blob_exists(artifact_dir, hash_ref)

        # Update metadata (preserves immutable fields)
        updated_metadata = update_metadata(artifact_dir, short_hash_name, source_uri=source_uri)
        full_hash = updated_metadata.hash

        # Get all tags pointing to this hash
        tags = self._get_tags_for_hash(artifact_dir, full_hash)

        return ArtifactInfo(
            hash=full_hash,
            hash_ref=short_hash(full_hash),
            tags=tags,
            uploaded_by=updated_metadata.uploaded_by,
            uploaded_at=updated_metadata.uploaded_at,
            source_uri=updated_metadata.source_uri,
        )

    def _validate_blob_exists(self, artifact_dir: Path, hash_ref: str) -> str:
        """Validate that a blob exists for the given hash reference.

        Args:
            artifact_dir: Artifact directory path.
            hash_ref: Hash reference (with or without @ prefix). Can be short (8 chars)
                     or full hash (64 chars) - only first 8 chars are used for lookup.

        Returns:
            Short hash (8 chars, no @ prefix) of the matching blob.

        Raises:
            ArtifactNotFoundError: If no matching blob found.
        """
        # Use first 8 chars for lookup (blobs are stored with short hash)
        prefix = hash_ref.lstrip("@")[:8]
        blobs_dir = artifact_dir / "blobs"

        if not blobs_dir.exists():
            raise ArtifactNotFoundError(f"No blobs found for hash ref {hash_ref}")

        # Find blob file matching prefix
        for blob_file in blobs_dir.iterdir():
            if blob_file.name == prefix:
                return blob_file.name

        raise ArtifactNotFoundError(f"Blob not found for hash ref {hash_ref}")

    def list_artifact_paths(self, prefix: str = "", recursive: bool = False) -> list[str]:
        """List artifact paths under a given prefix.

        Args:
            prefix: Path prefix to filter by (empty string lists all).
                   Leading slashes are normalized and path traversal sequences are rejected.
            recursive: If True, list all artifacts recursively under the prefix.
                      If False (default), list immediate children and virtual directories.

                      Non-recursive mode checks each immediate child of the prefix path:
                      - If it's a directory with .magpie → it's an artifact (return it)
                      - If it's a directory without .magpie → return as virtual directory
                        indicator (with trailing /)

                      Note: Empty directories may appear as virtual directories. This is an
                      intentional performance tradeoff to keep listings O(immediate_children)
                      without deep filesystem traversal. The reconciliation job will clean up
                      empty directories over time.

        Returns:
            Sorted list of artifact paths and virtual directory indicators.
            - Artifacts (have .magpie): returned as-is (e.g., "test/artifact1")
            - Virtual directories (subdirectories, may or may not contain artifacts):
              returned with trailing / (e.g., "test/")

        Examples:
            With artifacts at: test/artifact1, test/artifact2, test/sub/deep,
                              images/ubuntu, other/path

            list_artifact_paths("", False) -> ["images/", "other/", "test/"]
            list_artifact_paths("", True) -> ["images/ubuntu", "other/path",
                                               "test/artifact1", "test/artifact2",
                                               "test/sub/deep"]
            list_artifact_paths("test", False) -> ["test/artifact1", "test/artifact2", "test/sub/"]
            list_artifact_paths("test", True) -> ["test/artifact1", "test/artifact2",
                                                   "test/sub/deep"]
            list_artifact_paths("test/sub", False) -> ["test/sub/deep"]

        Note:
            For non-recursive mode, virtual directory indicators enable navigation:
            - Client sees "builds/" in root listing
            - Client can then list with prefix="builds" to navigate deeper
            This allows discovering deeply nested artifacts without full tree traversal.
        """
        # Normalize prefix for listing - allow empty result for root listing
        # Strip leading/trailing slashes and collapse multiple slashes
        normalized_prefix = prefix.strip("/") if prefix else ""
        if normalized_prefix:
            normalized_prefix = re.sub(r"/+", "/", normalized_prefix)

            # Check for path traversal attempts
            segments = normalized_prefix.split("/")
            if any(segment == ".." for segment in segments):
                raise InvalidArtifactPathError(
                    "Path traversal '..' is not allowed in artifact paths"
                )

            # Verify the prefix path is safe (no symlink escapes)
            verify_path_is_descendant(self.config.storage_path, normalized_prefix)

        artifact_paths: list[str] = []

        # Determine search root based on prefix
        search_root = self.config.storage_path
        if normalized_prefix:
            search_root = search_root / normalized_prefix

        # Check if search root exists
        if not search_root.exists() or not search_root.is_dir():
            return []

        if recursive:
            # Recursive mode: use rglob to find all .magpie files at any depth
            manifest_iter = search_root.rglob(".magpie")

            for manifest_file in manifest_iter:
                # Get artifact directory (parent of .magpie file)
                artifact_dir = manifest_file.parent

                # Compute artifact path relative to storage_path
                artifact_path = str(artifact_dir.relative_to(self.config.storage_path))

                # Filter by prefix if provided
                if normalized_prefix:
                    # Treat prefix as a path-segment prefix, not a raw string prefix
                    if not (
                        artifact_path == normalized_prefix
                        or artifact_path.startswith(normalized_prefix + "/")
                    ):
                        continue

                artifact_paths.append(artifact_path)
        else:
            # Non-recursive mode: return immediate children only
            # O(immediate_children) - no recursive filesystem checks
            # Tradeoff: may show virtual directories that don't contain artifacts,
            # but reconciliation job can clean those up

            # Step 1: For non-empty prefixes, check if the prefix path itself is a leaf
            # directory (artifact). For the root prefix (normalized_prefix == ""),
            # we intentionally skip this early-return so that root-level artifacts
            # and virtual directories can be listed together. Root listing needs to
            # continue checking children even if root itself has a .magpie, because
            # root can contain both artifacts and virtual directories.
            prefix_manifest = search_root / ".magpie"
            if prefix_manifest.is_file() and normalized_prefix:
                # The (non-root) prefix itself is an artifact, include it and stop:
                # artifacts are leaf nodes, so there are no child entries to list.
                artifact_paths.append(normalized_prefix)
                return sorted(artifact_paths)

            # Step 2: List immediate children - simple iterdir() only
            try:
                for child in search_root.iterdir():
                    if not child.is_dir():
                        continue

                    # Skip hidden/system directories (e.g., .tmp/)
                    if child.name.startswith("."):
                        continue

                    # Compute full path relative to storage_path
                    child_rel_path = str(child.relative_to(self.config.storage_path))

                    # Check if this immediate child is an artifact (has .magpie)
                    child_manifest = child / ".magpie"
                    if child_manifest.is_file():
                        # It's an artifact - return as-is
                        artifact_paths.append(child_rel_path)
                    else:
                        # It's a directory without .magpie - treat as virtual directory
                        # No recursive checks - just show it unconditionally
                        artifact_paths.append(child_rel_path + "/")
            except (OSError, PermissionError):
                # Handle permission errors or other filesystem issues gracefully
                pass

        return sorted(artifact_paths)

    def _get_tags_for_hash(self, artifact_dir: Path, hash_ref: str) -> list[str]:
        """Get all tags pointing to a given hash reference.

        Args:
            artifact_dir: Artifact directory path.
            hash_ref: Hash reference to find tags for.

        Returns:
            Sorted list of tag names pointing to this hash.
        """
        manifest = read_manifest(artifact_dir)
        tags = [tag_name for tag_name, ref in manifest.tags.items() if ref == hash_ref]
        return sorted(tags)
