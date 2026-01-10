"""Metadata sidecar operations for blob metadata storage."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from magpie.storage.exceptions import ArtifactNotFoundError
from magpie.storage.paths import metadata_path


class BlobMetadata(BaseModel):
    """Metadata for a stored blob.

    Each blob has a companion JSON sidecar file containing upload information.
    Metadata is write-once to preserve original upload provenance.
    """

    hash: str  # Full SHA-256 hex digest
    uploaded_by: str  # Uploader identity
    uploaded_at: datetime  # Upload timestamp (UTC)
    source_uri: str | None = None  # Optional source URI


def write_metadata(
    artifact_dir: Path, hash_ref: str, metadata: BlobMetadata
) -> None:
    """Write metadata sidecar file for a blob.

    Metadata is write-once: if the file already exists, this function
    silently returns without overwriting to preserve original upload info.

    Args:
        artifact_dir: Path to artifact directory.
        hash_ref: Hash reference (with or without @ prefix).
        metadata: BlobMetadata instance to write.
    """
    path = metadata_path(artifact_dir, hash_ref)

    # Write-once: do not overwrite existing metadata
    if path.exists():
        return

    # Ensure metadata directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize with ISO 8601 datetime format
    content = metadata.model_dump_json(indent=2)
    path.write_text(content, encoding="utf-8")


def read_metadata(artifact_dir: Path, hash_ref: str) -> BlobMetadata:
    """Read metadata sidecar file for a blob.

    Args:
        artifact_dir: Path to artifact directory.
        hash_ref: Hash reference (with or without @ prefix).

    Returns:
        BlobMetadata instance.

    Raises:
        ArtifactNotFoundError: If metadata file doesn't exist.
    """
    path = metadata_path(artifact_dir, hash_ref)

    if not path.exists():
        raise ArtifactNotFoundError(f"Metadata not found for hash {hash_ref} at {path}")

    content = path.read_text(encoding="utf-8")
    data = json.loads(content)
    return BlobMetadata.model_validate(data)


def update_metadata(
    artifact_dir: Path,
    hash_ref: str,
    source_uri: str | None = None,
) -> BlobMetadata:
    """Update mutable fields in metadata while preserving immutable fields.

    Reads existing metadata and updates only the specified mutable fields.
    Immutable fields (hash, uploaded_by, uploaded_at) are always preserved.

    Args:
        artifact_dir: Path to artifact directory.
        hash_ref: Hash reference (with or without @ prefix).
        source_uri: New source URI value. If None, field is left unchanged.

    Returns:
        Updated BlobMetadata instance.

    Raises:
        ArtifactNotFoundError: If metadata file doesn't exist.
    """
    # Read existing metadata
    existing = read_metadata(artifact_dir, hash_ref)

    # Update mutable fields if provided
    new_source_uri = source_uri if source_uri is not None else existing.source_uri

    # Create updated metadata preserving immutable fields
    updated = BlobMetadata(
        hash=existing.hash,
        uploaded_by=existing.uploaded_by,
        uploaded_at=existing.uploaded_at,
        source_uri=new_source_uri,
    )

    # Write updated metadata (overwrite existing)
    path = metadata_path(artifact_dir, hash_ref)
    content = updated.model_dump_json(indent=2)
    path.write_text(content, encoding="utf-8")

    return updated
