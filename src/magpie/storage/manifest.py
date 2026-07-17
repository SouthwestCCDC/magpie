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


_LOCK_ACQUIRE_MAX_ATTEMPTS = 5


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

    ``mkdir()`` and ``os.open()`` are two separate, non-atomic syscalls. If
    two concurrent callers race on the same already-empty artifact (e.g. two
    overlapping GC cleanup passes), the first can finish its whole locked
    section -- including deleting the manifest and rmdir'ing the artifact
    directory -- in the gap between the second caller's ``mkdir()`` and
    ``os.open()``, so the second caller's ``open()`` would otherwise raise
    ``FileNotFoundError`` on a directory that no longer exists. This function
    retries the mkdir+open pair (bounded) on that specific error: a
    concurrent deleter removing the directory in that window just triggers a
    re-mkdir and re-open rather than an uncaught crash.

    ``Path.mkdir(parents=True, exist_ok=True)`` has its own narrow internal
    race under adversarial concurrent create/remove of the very same path:
    if its ``os.mkdir()`` raises ``FileExistsError`` (someone else is
    concurrently creating it too) and then, before its own follow-up
    ``is_dir()`` check runs, a *third* caller removes the directory again,
    it re-raises ``FileExistsError`` instead of tolerating it as
    ``exist_ok=True`` promises. That's retried here too, for the same
    reason and via the same bounded loop.

    Args:
        artifact_dir: Path to artifact directory to lock.

    Yields:
        None. The lock is held for the duration of the ``with`` block.

    Raises:
        OSError: If the directory keeps disappearing (or flapping between
            existing and not) out from under us for
            ``_LOCK_ACQUIRE_MAX_ATTEMPTS`` consecutive attempts. This would
            indicate persistent, unusual concurrent create/delete pressure
            rather than the ordinary multi-caller races this retry loop is
            meant to absorb.
    """
    fd: int | None = None
    last_error: OSError | None = None
    for _ in range(_LOCK_ACQUIRE_MAX_ATTEMPTS):
        try:
            artifact_dir.mkdir(parents=True, exist_ok=True)
        except FileExistsError as e:
            # Path.mkdir(exist_ok=True)'s own internal exist_ok recheck lost
            # a race against a concurrent remover. Retry: our next mkdir()
            # attempt will recreate it.
            last_error = e
            continue
        try:
            # O_CLOEXEC prevents the lock fd from leaking into child
            # processes spawned while the lock is held (magpie-ctl shells
            # out for gc/flush-tag operations); without it, a leaked fd in
            # a long-lived child would keep the flock held indefinitely.
            fd = os.open(artifact_dir, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
            break
        except FileNotFoundError as e:
            # A concurrent caller deleted artifact_dir between our mkdir()
            # and open(). Retry: mkdir() will recreate it.
            last_error = e
            continue
    else:
        raise OSError(
            f"Could not acquire artifact lock for {artifact_dir}: directory kept "
            f"flapping after {_LOCK_ACQUIRE_MAX_ATTEMPTS} attempts"
        ) from last_error

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
