"""Blob storage operations with streaming upload and duplicate detection."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from magpie.storage.exceptions import ArtifactNotFoundError, HashPrefixCollisionError
from magpie.storage.hash import HASH_NAME_LENGTH, compute_hash, short_hash
from magpie.storage.paths import blob_path, canonical_blob_path, resolve_blob_name

if TYPE_CHECKING:
    from magpie.config import MagpieSettings

CHUNK_SIZE = 8192  # 8KB chunks for streaming


def get_temp_path(config: MagpieSettings) -> Path:
    """Get temporary directory path, creating it if needed.

    Args:
        config: MagpieSettings instance with temp_path configured.

    Returns:
        Path to temporary directory.
    """
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config.temp_path


def store_blob(
    artifact_dir: Path, file_stream: BinaryIO, config: MagpieSettings
) -> tuple[str, str, bool]:
    """Store blob content with streaming upload and duplicate detection.

    Streams content to a temp file while computing SHA-256 hash, then
    atomically moves to final location. Detects duplicates by checking
    if blob already exists.

    Blobs are stored under a truncated hash as the filename (see
    :data:`magpie.storage.hash.HASH_NAME_LENGTH`) for readability, while the
    full hash is preserved in metadata for verification. Like
    :func:`store_blob_from_temp`, a filename that is already taken is only
    treated as a duplicate once the existing blob's *full* hash is verified
    to match.

    Args:
        artifact_dir: Path to artifact directory.
        file_stream: Binary file-like object to read content from.
        config: MagpieSettings instance for temp path configuration.

    Returns:
        Tuple of (full_hash, hash_ref, is_duplicate):
        - full_hash: Full SHA-256 hex digest (64 chars)
        - hash_ref: Short hash reference like '@abc1234567890def'
        - is_duplicate: True if blob already existed, False if newly stored

    Raises:
        HashPrefixCollisionError: If a different file is already stored under
            this hash's filename.
    """
    temp_dir = get_temp_path(config)

    # Create temp file and stream content while computing hash
    fd, temp_path = tempfile.mkstemp(dir=temp_dir, prefix="blob_", suffix=".tmp")
    temp_file = Path(temp_path)

    try:
        # Stream to temp file and compute hash simultaneously
        full_hash = _stream_to_temp(fd, file_stream)
    except Exception:
        temp_file.unlink(missing_ok=True)
        raise

    try:
        hash_ref, is_duplicate = store_blob_from_temp(artifact_dir, temp_file, full_hash)
    except Exception:
        temp_file.unlink(missing_ok=True)
        raise
    return (full_hash, hash_ref, is_duplicate)


def _stream_to_temp(fd: int, file_stream: BinaryIO) -> str:
    """Stream content to temp file while computing hash.

    Args:
        fd: File descriptor of temp file.
        file_stream: Source file stream to read from.

    Returns:
        Full SHA-256 hex digest of content.
    """
    import hashlib
    import os

    hasher = hashlib.sha256()

    try:
        while True:
            chunk = file_stream.read(CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
            os.write(fd, chunk)
    finally:
        os.close(fd)

    return hasher.hexdigest()


def read_blob(artifact_dir: Path, hash_ref: str) -> Path:
    """Get path to an existing blob.

    Args:
        artifact_dir: Path to artifact directory.
        hash_ref: Hash reference (with or without @ prefix).

    Returns:
        Path to the blob file.

    Raises:
        ArtifactNotFoundError: If blob doesn't exist.
    """
    path = blob_path(artifact_dir, hash_ref)

    if not path.exists():
        raise ArtifactNotFoundError(f"Blob not found: {hash_ref} at {path}")

    return path


def store_blob_from_temp(
    artifact_dir: Path, temp_file_path: Path, full_hash: str
) -> tuple[str, bool]:
    """Store blob from pre-written temp file with pre-computed hash.

    This function is used when the temp file has already been written and the
    hash has been computed incrementally during streaming. It handles duplicate
    detection and atomic move to the final blob location.

    Security note: filenames are a truncated hash (see
    :data:`magpie.storage.hash.HASH_NAME_LENGTH`), so an existing file at the
    destination is only a duplicate if its *full* hash matches; that is
    verified here rather than assumed from the filename. Blobs written by
    releases that used a narrower filename are honored as duplicates too,
    but new content is always stored at the current width -- so content that
    merely shares a narrow prefix with an older blob gets its own filename
    instead of being permanently un-storable.

    Args:
        artifact_dir: Path to artifact directory.
        temp_file_path: Path to pre-written temp file (will be moved or deleted).
        full_hash: Pre-computed SHA-256 hex digest (64 chars).

    Returns:
        Tuple of (hash_ref, is_duplicate):
        - hash_ref: Short hash reference like '@abc1234567890def'
        - is_duplicate: True if blob already existed, False if newly stored

    Raises:
        HashPrefixCollisionError: If a different file is already stored under
            this hash's filename at the current width.
    """
    hash_ref = short_hash(full_hash)

    # New content always lands at the current hash-name width, so a blob
    # stored under an older, narrower name never blocks it.
    dest_path = canonical_blob_path(artifact_dir, hash_ref)

    if _existing_blob_matches(dest_path, full_hash, temp_file_path):
        temp_file_path.unlink(missing_ok=True)
        return (hash_ref, True)

    # Same content may already be stored under a narrower filename written
    # by an earlier release; honor that rather than storing a second copy.
    legacy_name = resolve_blob_name(artifact_dir, full_hash)
    if legacy_name is not None:
        legacy_path = artifact_dir / "blobs" / legacy_name
        if _existing_blob_matches(legacy_path, full_hash, temp_file_path, strict=False):
            temp_file_path.unlink(missing_ok=True)
            return (hash_ref, True)

    # New blob - ensure destination directory exists, then atomic move
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_file_path), dest_path)
        return (hash_ref, False)
    except Exception:
        # Clean up temp file if move fails (permissions, disk full, etc.)
        temp_file_path.unlink(missing_ok=True)
        raise


def _existing_blob_matches(
    dest_path: Path,
    full_hash: str,
    temp_file_path: Path,
    strict: bool = True,
) -> bool:
    """Check whether an existing blob file holds exactly this content.

    Args:
        dest_path: Candidate blob file (may not exist).
        full_hash: Full SHA-256 hex digest of the content being stored.
        temp_file_path: Temp file to clean up before raising, so a refused
            write doesn't leak it.
        strict: If True, a hash mismatch is a collision on the filename this
            build writes and is refused. If False (a narrower filename from
            an earlier release, which the caller will not write to) a
            mismatch just means "not this blob".

    Returns:
        True if the file exists and its full hash matches.

    Raises:
        HashPrefixCollisionError: If ``strict`` and the file holds different content.
    """
    if not dest_path.exists():
        return False

    existing_hash = compute_hash(dest_path)
    if existing_hash == full_hash:
        return True

    if not strict:
        return False

    temp_file_path.unlink(missing_ok=True)
    raise HashPrefixCollisionError(
        f"Blob filename '{dest_path.name}' is already used by different content "
        f"(stored: {existing_hash}, uploading: {full_hash}). Refusing to overwrite "
        f"the stored blob. Report this to the Magpie maintainers: a collision in "
        f"{HASH_NAME_LENGTH} hex characters of SHA-256 should not be reachable."
    )


def check_blob_exists(artifact_dir: Path, hash_ref: str) -> bool:
    """Check if a blob exists.

    Args:
        artifact_dir: Path to artifact directory.
        hash_ref: Hash reference (with or without @ prefix).

    Returns:
        True if blob exists, False otherwise.

    Raises:
        AmbiguousHashRefError: If an abbreviated reference matches multiple blobs
            (an unanswerable question, not a "no").
        InvalidArtifactPathError: If the reference is not usable as a filename.
    """
    path = blob_path(artifact_dir, hash_ref)
    return path.exists()
