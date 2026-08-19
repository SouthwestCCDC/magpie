"""Custom exceptions for storage operations."""

from __future__ import annotations


class StorageError(Exception):
    """Base exception for storage operations."""

    pass


class ArtifactNotFoundError(StorageError):
    """Artifact or blob does not exist."""

    pass


class BlobExistsError(StorageError):
    """Blob already exists (duplicate detection)."""

    pass


class ManifestCorruptError(StorageError):
    """Manifest file is corrupted or invalid."""

    pass


class HashMismatchError(StorageError):
    """Computed hash does not match expected hash."""

    pass


class HashPrefixCollisionError(StorageError):
    """Two different blobs share the stored hash-name prefix.

    Blobs are stored under a truncated SHA-256 (see
    :data:`magpie.storage.hash.HASH_NAME_LENGTH`), so two distinct files
    could in principle share a filename. Storing either one would
    silently corrupt the other, so the write is refused instead.
    """

    pass


class AmbiguousHashRefError(StorageError):
    """An abbreviated hash reference matches more than one stored blob."""

    pass


class InvalidArtifactPathError(StorageError):
    """Artifact path is invalid or conflicts with existing artifacts."""

    pass
