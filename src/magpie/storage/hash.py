"""Hash computation utilities for content-addressed storage."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 8192  # 8KB chunks for streaming hash computation


def compute_hash(file_or_path: BinaryIO | Path | bytes) -> str:
    """Compute SHA-256 hash of content.

    Args:
        file_or_path: Content to hash - can be a file-like object (BinaryIO),
            a Path to a file, or raw bytes.

    Returns:
        Full SHA-256 hex digest string.

    Raises:
        TypeError: If input type is not supported.
        FileNotFoundError: If Path does not exist.
    """
    hasher = hashlib.sha256()

    if isinstance(file_or_path, bytes):
        hasher.update(file_or_path)
    elif isinstance(file_or_path, Path):
        with file_or_path.open("rb") as f:
            _hash_file_chunks(f, hasher)
    elif hasattr(file_or_path, "read"):
        _hash_file_chunks(file_or_path, hasher)
    else:
        raise TypeError(f"Expected BinaryIO, Path, or bytes, got {type(file_or_path).__name__}")

    return hasher.hexdigest()


def _hash_file_chunks(file_obj: BinaryIO, hasher: hashlib._Hash) -> None:
    """Read file in chunks and update hasher.

    Args:
        file_obj: File-like object to read from.
        hasher: Hashlib hash object to update.
    """
    while True:
        chunk = file_obj.read(CHUNK_SIZE)
        if not chunk:
            break
        hasher.update(chunk)


def short_hash(full_hash: str) -> str:
    """Create short hash reference from full hash.

    Args:
        full_hash: Full SHA-256 hex digest string.

    Returns:
        Short hash in format '@' + first 8 characters of hash.
    """
    return f"@{full_hash[:8]}"
