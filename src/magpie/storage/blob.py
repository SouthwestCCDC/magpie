"""Blob storage operations with streaming upload and duplicate detection."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from magpie.storage.exceptions import ArtifactNotFoundError
from magpie.storage.hash import compute_hash, short_hash
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
) -> tuple[str, bool]:
    """Store blob content with streaming upload and duplicate detection.

    Streams content to a temp file while computing SHA-256 hash, then
    atomically moves to final location. Detects duplicates by checking
    if blob already exists.

    Args:
        artifact_dir: Path to artifact directory.
        file_stream: Binary file-like object to read content from.
        config: MagpieSettings instance for temp path configuration.

    Returns:
        Tuple of (hash_ref, is_duplicate):
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

        # Check if blob already exists
        dest_path = blob_path(artifact_dir, full_hash)

        if dest_path.exists():
            # Duplicate detected - clean up temp file
            temp_file.unlink(missing_ok=True)
            return (hash_ref, True)

        # New blob - atomic move to destination
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_file), dest_path)
        return (hash_ref, False)

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
