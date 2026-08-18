"""Hash computation utilities for content-addressed storage."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 8192  # 8KB chunks for streaming hash computation

# Number of leading hex characters of the SHA-256 used for blob/metadata
# filenames and for the short hash refs the API and CLI display.
#
# 16 hex chars = 64 bits. The previous width was 8 (32 bits), which is
# small enough that a prefix collision is both plausible at scale (a
# birthday collision around ~2^16 versions in a single artifact path) and
# cheap to manufacture on purpose -- and because a colliding write is
# refused to protect the existing blob, either one permanently blocks
# storing the other file at that path. At 64 bits a birthday collision
# needs ~2^32 versions in one artifact path and grinding a *targeted*
# prefix needs ~2^64 hashes, so neither is reachable. Kept short of the
# full 64-char digest so filenames stay readable and quotable, which is
# the reason the layout truncates at all.
HASH_NAME_LENGTH = 16

# Hash-name widths written by earlier releases, newest first. Reads fall
# back to these so blobs stored before the widening keep resolving; see
# magpie.storage.paths.resolve_blob_name().
LEGACY_HASH_NAME_LENGTHS: tuple[int, ...] = (8,)


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
        Short hash in format '@' + the first :data:`HASH_NAME_LENGTH`
        characters of the hash.
    """
    return f"@{full_hash[:HASH_NAME_LENGTH]}"
