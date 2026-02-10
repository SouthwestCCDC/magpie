"""Blob storage operations with streaming upload and duplicate detection."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from magpie.storage.exceptions import ArtifactNotFoundError
from magpie.storage.hash import short_hash
from magpie.storage.paths import blob_path

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

    Blobs are stored using the first 8 characters of the hash as the filename
    to save space and improve readability, while the full hash is preserved
    in metadata for verification.

    Args:
        artifact_dir: Path to artifact directory.
        file_stream: Binary file-like object to read content from.
        config: MagpieSettings instance for temp path configuration.

    Returns:
        Tuple of (full_hash, hash_ref, is_duplicate):
        - full_hash: Full SHA-256 hex digest (64 chars)
        - hash_ref: Short hash reference like '@abc12345'
        - is_duplicate: True if blob already existed, False if newly stored
    """
    temp_dir = get_temp_path(config)

    # Create temp file and stream content while computing hash
    fd, temp_path = tempfile.mkstemp(dir=temp_dir, prefix="blob_", suffix=".tmp")
    temp_file = Path(temp_path)

    try:
        # Stream to temp file and compute hash simultaneously
        full_hash = _stream_to_temp(fd, file_stream)
        hash_ref = short_hash(full_hash)

        # Check if blob already exists (use short hash for storage path)
        dest_path = blob_path(artifact_dir, hash_ref)

        if dest_path.exists():
            # Duplicate detected - clean up temp file
            temp_file.unlink(missing_ok=True)
            return (full_hash, hash_ref, True)

        # New blob - atomic move to destination
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_file), dest_path)
        return (full_hash, hash_ref, False)

    except Exception:
        # Clean up temp file on any error
        temp_file.unlink(missing_ok=True)
        raise


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

    Security note: Uses 8-char hash prefix for filenames (storage efficiency).
    While content-addressable storage makes hash collisions safe (identical content
    → identical hash), this function verifies the full hash of existing blobs to
    detect the astronomically unlikely case of 8-char prefix collision between
    different files (2^32 hash space). This provides defense-in-depth against
    malicious hash collision attacks or implementation bugs.

    Args:
        artifact_dir: Path to artifact directory.
        temp_file_path: Path to pre-written temp file (will be moved or deleted).
        full_hash: Pre-computed SHA-256 hex digest (64 chars).

    Returns:
        Tuple of (hash_ref, is_duplicate):
        - hash_ref: Short hash reference like '@abc12345'
        - is_duplicate: True if blob already existed, False if newly stored

    Raises:
        ValueError: If an existing blob at the same short-hash path has a different
            full hash (indicates hash prefix collision - should never happen with
            SHA-256 in practice).
    """
    import hashlib

    hash_ref = short_hash(full_hash)

    # Check if blob already exists (use short hash for storage path)
    dest_path = blob_path(artifact_dir, hash_ref)

    if dest_path.exists():
        # Defense-in-depth: Verify full hash of existing blob matches
        # This catches the astronomically unlikely case of 8-char prefix collision
        # between different files (2^32 hash space ≈ 4 billion possibilities)
        with dest_path.open("rb") as f:
            hasher = hashlib.sha256()
            while chunk := f.read(CHUNK_SIZE):
                hasher.update(chunk)
            existing_hash = hasher.hexdigest()

        if existing_hash != full_hash:
            # Hash prefix collision detected - this should NEVER happen with SHA-256
            # Log critical error and raise to prevent data corruption
            temp_file_path.unlink(missing_ok=True)
            raise ValueError(
                f"Hash prefix collision detected: {hash_ref} (existing: {existing_hash[:16]}..., "
                f"new: {full_hash[:16]}...). This indicates a severe issue - "
                f"contact system administrator."
            )

        # Hashes match - true duplicate, clean up temp file
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


def check_blob_exists(artifact_dir: Path, hash_ref: str) -> bool:
    """Check if a blob exists.

    Args:
        artifact_dir: Path to artifact directory.
        hash_ref: Hash reference (with or without @ prefix).

    Returns:
        True if blob exists, False otherwise.
    """
    path = blob_path(artifact_dir, hash_ref)
    return path.exists()
