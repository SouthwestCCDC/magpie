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
