"""Manifest management for artifact tag storage."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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


@contextmanager
def artifact_lock(artifact_dir: Path) -> Iterator[None]:
    """Acquire an exclusive advisory lock serializing manifest mutations.

    Uses POSIX ``flock`` on the artifact directory itself (rather than on a
    separate lock file) so that manifest read-modify-write cycles are
    serialized across threads and processes without leaving behind any extra
    file. That matters because GC's empty-directory cleanup (see
    ``storage/cleanup.py``) treats an artifact directory as removable once it
    has no blobs, metadata, or manifest left; a stray lock file would defeat
    that check and leak empty directories.

    This lock also guards ``cleanup_artifact_directories()`` in
    ``storage/cleanup.py``: GC's "is this artifact deletable" check and the
    resulting manifest unlink / directory rmdir must happen under the same
    lock ``update_tag``/``remove_tag`` use, or GC can act on a stale read and
    delete a manifest (and the whole artifact directory) that a concurrent
    tag mutation just wrote to.

    Args:
        artifact_dir: Path to artifact directory to lock.

    Yields:
        None. The lock is held for the duration of the ``with`` block.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(artifact_dir, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def update_tag(artifact_dir: Path, tag_name: str, hash_ref: str) -> Manifest:
    """Update or create a tag in the manifest.

    The read-modify-write cycle is serialized with an exclusive lock on the
    artifact directory, so concurrent callers mutating different tags on the
    same artifact cannot silently lose each other's updates.

    Args:
        artifact_dir: Path to artifact directory.
        tag_name: Name of the tag to update/create.
        hash_ref: Hash reference to associate with the tag.

    Returns:
        Updated Manifest instance.
    """
    with artifact_lock(artifact_dir):
        manifest = read_manifest(artifact_dir)
        manifest.tags[tag_name] = hash_ref
        write_manifest(artifact_dir, manifest)
        return manifest


def remove_tag(artifact_dir: Path, tag_name: str) -> Manifest:
    """Remove a tag from the manifest.

    If the tag doesn't exist, this is a no-op (no error raised). The
    read-modify-write cycle is serialized with an exclusive lock on the
    artifact directory, so concurrent callers mutating different tags on the
    same artifact cannot silently lose each other's updates.

    Args:
        artifact_dir: Path to artifact directory.
        tag_name: Name of the tag to remove.

    Returns:
        Updated Manifest instance.
    """
    with artifact_lock(artifact_dir):
        manifest = read_manifest(artifact_dir)
        manifest.tags.pop(tag_name, None)  # Remove if exists, no error if missing
        write_manifest(artifact_dir, manifest)
        return manifest
