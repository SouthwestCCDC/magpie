"""Manifest management for artifact tag storage."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pydantic import BaseModel

from magpie.storage.exceptions import ManifestCorruptError
from magpie.storage.paths import manifest_path


class Manifest(BaseModel):
    """Manifest model representing tag-to-hash mappings for an artifact.

    The manifest is stored as a `.magpie` JSON file in each artifact directory
    and serves as the source of truth for tag resolution.
    """

    version: int = 1
    tags: dict[str, str] = {}  # Maps tag name -> hash ref (e.g., "latest" -> "@abc12345")


def read_manifest(artifact_dir: Path) -> Manifest:
    """Read manifest from artifact directory.

    Args:
        artifact_dir: Path to artifact directory.

    Returns:
        Manifest instance. Returns default empty manifest if file doesn't exist.

    Raises:
        ManifestCorruptError: If manifest file contains invalid JSON.
    """
    path = manifest_path(artifact_dir)

    if not path.exists():
        return Manifest()

    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        return Manifest.model_validate(data)
    except json.JSONDecodeError as e:
        raise ManifestCorruptError(f"Invalid JSON in manifest at {path}: {e}") from e
    except Exception as e:
        raise ManifestCorruptError(f"Failed to parse manifest at {path}: {e}") from e


def write_manifest(artifact_dir: Path, manifest: Manifest) -> None:
    """Write manifest to artifact directory using atomic write.

    Creates artifact_dir if it doesn't exist. Uses atomic write pattern
    (write to temp file, then rename) to prevent corruption.

    Args:
        artifact_dir: Path to artifact directory.
        manifest: Manifest instance to write.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_path(artifact_dir)

    content = manifest.model_dump_json(indent=2)

    # Atomic write: write to temp file in same directory, then rename
    fd, temp_path = tempfile.mkstemp(
        dir=artifact_dir,
        prefix=".magpie_",
        suffix=".tmp",
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        # Atomic rename (on POSIX systems)
        Path(temp_path).replace(path)
    except Exception:
        # Clean up temp file on failure
        Path(temp_path).unlink(missing_ok=True)
        raise


def update_tag(artifact_dir: Path, tag_name: str, hash_ref: str) -> Manifest:
    """Update or create a tag in the manifest.

    Args:
        artifact_dir: Path to artifact directory.
        tag_name: Name of the tag to update/create.
        hash_ref: Hash reference to associate with the tag.

    Returns:
        Updated Manifest instance.
    """
    manifest = read_manifest(artifact_dir)
    manifest.tags[tag_name] = hash_ref
    write_manifest(artifact_dir, manifest)
    return manifest


def remove_tag(artifact_dir: Path, tag_name: str) -> Manifest:
    """Remove a tag from the manifest.

    If the tag doesn't exist, this is a no-op (no error raised).

    Args:
        artifact_dir: Path to artifact directory.
        tag_name: Name of the tag to remove.

    Returns:
        Updated Manifest instance.
    """
    manifest = read_manifest(artifact_dir)
    manifest.tags.pop(tag_name, None)  # Remove if exists, no error if missing
    write_manifest(artifact_dir, manifest)
    return manifest
