"""Blob integrity verification (scrub) against recorded SHA-256 hashes.

Magpie records the full SHA-256 of every blob in its metadata sidecar. This
module re-reads stored blobs and compares their content against that recorded
hash, detecting bit-rot, truncation, tampering, and missing files.

The walk is streaming and memory-bounded: blobs are hashed in fixed-size chunks
and never loaded into memory in full. Only issues (not OK blobs) are retained
in the result, so a scrub of a large store stays bounded in memory as well.

Blob and metadata locations are always derived through the helpers in
:mod:`magpie.storage.paths` rather than assuming a particular hash-prefix
length, so this module keeps working if the addressing scheme changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator

import structlog
from pydantic import ValidationError

from magpie.storage.exceptions import ArtifactNotFoundError, ManifestCorruptError
from magpie.storage.manifest import Manifest, read_manifest
from magpie.storage.metadata import read_metadata
from magpie.storage.paths import (
    blob_path,
    metadata_path,
    normalize_artifact_path,
    verify_path_is_descendant,
)

logger = structlog.get_logger()

# Chunk size for streaming blob reads. Larger than the upload path's 8KB chunks
# because a scrub is throughput-bound while staying memory-bounded.
VERIFY_CHUNK_SIZE = 1024 * 1024  # 1MB

# Progress callback: callback(phase, current, total) where phase is "verify".
VerifyProgressCallback = Callable[[str, int, int], None]


class VerifyStatus(str, Enum):
    """Outcome for a single blob checked during verification."""

    OK = "ok"
    MISMATCH = "mismatch"
    MISSING_BLOB = "missing_blob"
    MISSING_METADATA = "missing_metadata"
    CORRUPT_METADATA = "corrupt_metadata"
    ERROR = "error"


@dataclass
class VerifyIssue:
    """A single problem found during verification.

    Only non-OK outcomes are recorded, so the issue list stays bounded by the
    number of actual problems rather than the size of the store.
    """

    artifact_path: str
    blob_ref: str
    status: VerifyStatus
    message: str
    expected_hash: str | None = None
    actual_hash: str | None = None
    size_bytes: int | None = None

    def to_dict(self) -> dict:
        """Convert to a JSON-serializable dictionary."""
        return {
            "artifact_path": self.artifact_path,
            "blob_ref": self.blob_ref,
            "status": self.status.value,
            "message": self.message,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "size_bytes": self.size_bytes,
        }


@dataclass
class VerifyResult:
    """Aggregate statistics from a verification run."""

    artifacts_scanned: int = 0
    blobs_scanned: int = 0
    bytes_read: int = 0
    ok: int = 0
    mismatched: int = 0
    missing_blob: int = 0
    missing_metadata: int = 0
    corrupt_metadata: int = 0
    errors: int = 0
    stopped_early: bool = False
    issues: list[VerifyIssue] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        """Total number of problems found."""
        return (
            self.mismatched
            + self.missing_blob
            + self.missing_metadata
            + self.corrupt_metadata
            + self.errors
        )

    def to_dict(self) -> dict:
        """Convert to a dictionary for JSON serialization."""
        return {
            "artifacts_scanned": self.artifacts_scanned,
            "blobs_scanned": self.blobs_scanned,
            "bytes_read": self.bytes_read,
            "ok": self.ok,
            "mismatched": self.mismatched,
            "missing_blob": self.missing_blob,
            "missing_metadata": self.missing_metadata,
            "corrupt_metadata": self.corrupt_metadata,
            "errors": self.errors,
            "stopped_early": self.stopped_early,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def resolve_scope(storage_path: Path, path_prefix: str | None) -> Path:
    """Resolve the directory a verification run should walk.

    Args:
        storage_path: Base storage path.
        path_prefix: Optional logical path prefix to scope the walk to. May name
            an artifact or any parent directory of artifacts.

    Returns:
        Directory to walk, fully resolved (the storage directory itself when no
        prefix is given).

    Raises:
        FileNotFoundError: If the resolved directory does not exist.
        InvalidArtifactPathError: If the prefix escapes the storage directory.
    """
    if not storage_path.exists():
        raise FileNotFoundError(f"Storage path does not exist: {storage_path}")

    if path_prefix is None:
        return storage_path.resolve()

    normalized = normalize_artifact_path(path_prefix)
    scope = verify_path_is_descendant(storage_path, normalized)

    if not scope.is_dir():
        raise FileNotFoundError(f"Path not found in storage: {normalized}")

    return scope


def _hash_blob(blob_file: Path) -> tuple[str, int]:
    """Stream a blob and return its SHA-256 digest and size.

    Args:
        blob_file: Path to the blob file.

    Returns:
        Tuple of (hex digest, bytes read).
    """
    hasher = hashlib.sha256()
    size = 0

    with blob_file.open("rb") as f:
        while chunk := f.read(VERIFY_CHUNK_SIZE):
            hasher.update(chunk)
            size += len(chunk)

    return hasher.hexdigest(), size


def _iter_blob_files(artifact_dir: Path) -> Iterator[Path]:
    """Yield regular blob files for an artifact in stable order.

    Symlinks are skipped: tag symlinks live in the artifact directory, and a
    symlink inside ``blobs/`` is not a stored blob.
    """
    blobs_dir = artifact_dir / "blobs"
    if not blobs_dir.is_dir():
        return

    for blob_file in sorted(blobs_dir.iterdir()):
        if blob_file.is_symlink() or not blob_file.is_file():
            continue
        yield blob_file


def _expected_hash(artifact_dir: Path, blob_ref: str) -> str:
    """Read the recorded full hash for a blob from its metadata sidecar.

    Raises:
        ArtifactNotFoundError: If the sidecar is absent.
        ManifestCorruptError: If the sidecar cannot be parsed or validated.
    """
    try:
        return read_metadata(artifact_dir, blob_ref).hash
    except (json.JSONDecodeError, ValidationError, UnicodeDecodeError) as e:
        raise ManifestCorruptError(
            f"Metadata sidecar is unreadable at {metadata_path(artifact_dir, blob_ref)}: {e}"
        ) from e


def _verify_blob(
    artifact_dir: Path, artifact_path: str, blob_file: Path
) -> tuple[VerifyIssue | None, int]:
    """Verify a single blob against its recorded hash.

    Returns:
        Tuple of (issue or None if the blob verified OK, bytes read).
    """
    blob_ref = blob_file.name

    try:
        expected = _expected_hash(artifact_dir, blob_ref)
    except ArtifactNotFoundError:
        return (
            VerifyIssue(
                artifact_path=artifact_path,
                blob_ref=blob_ref,
                status=VerifyStatus.MISSING_METADATA,
                message="No metadata sidecar; recorded hash is unknown",
            ),
            0,
        )
    except ManifestCorruptError as e:
        return (
            VerifyIssue(
                artifact_path=artifact_path,
                blob_ref=blob_ref,
                status=VerifyStatus.CORRUPT_METADATA,
                message=str(e),
            ),
            0,
        )
    except OSError as e:
        return (
            VerifyIssue(
                artifact_path=artifact_path,
                blob_ref=blob_ref,
                status=VerifyStatus.ERROR,
                message=f"Failed to read metadata: {e}",
            ),
            0,
        )

    try:
        actual, size = _hash_blob(blob_file)
    except OSError as e:
        return (
            VerifyIssue(
                artifact_path=artifact_path,
                blob_ref=blob_ref,
                status=VerifyStatus.ERROR,
                message=f"Failed to read blob: {e}",
                expected_hash=expected,
            ),
            0,
        )

    if actual == expected:
        return None, size

    return (
        VerifyIssue(
            artifact_path=artifact_path,
            blob_ref=blob_ref,
            status=VerifyStatus.MISMATCH,
            message="Stored content does not match recorded SHA-256",
            expected_hash=expected,
            actual_hash=actual,
            size_bytes=size,
        ),
        size,
    )


def _missing_blob_refs(artifact_dir: Path, manifest: Manifest | None) -> list[str]:
    """Find blob references that are recorded but whose blob file is gone.

    Considers both tags in the manifest and metadata sidecars. References are
    derived via the path helpers so the hash-prefix scheme stays authoritative.

    Args:
        artifact_dir: Path to the artifact directory.
        manifest: Parsed manifest, or None if it could not be read.
    """
    refs: dict[str, None] = {}

    if manifest is not None:
        for hash_ref in manifest.tags.values():
            refs[blob_path(artifact_dir, hash_ref).name] = None

    metadata_dir = artifact_dir / "metadata"
    if metadata_dir.is_dir():
        for sidecar in sorted(metadata_dir.glob("*.json")):
            refs[sidecar.stem] = None

    return [ref for ref in refs if not blob_path(artifact_dir, ref).is_file()]


def _record(result: VerifyResult, issue: VerifyIssue, max_issues: int | None) -> None:
    """Update counters for an issue and retain it if within the retention cap."""
    if issue.status == VerifyStatus.MISMATCH:
        result.mismatched += 1
    elif issue.status == VerifyStatus.MISSING_BLOB:
        result.missing_blob += 1
    elif issue.status == VerifyStatus.MISSING_METADATA:
        result.missing_metadata += 1
    elif issue.status == VerifyStatus.CORRUPT_METADATA:
        result.corrupt_metadata += 1
    else:
        result.errors += 1

    logger.warning(
        "verify_issue",
        artifact_path=issue.artifact_path,
        blob_ref=issue.blob_ref,
        status=issue.status.value,
        expected_hash=issue.expected_hash,
        actual_hash=issue.actual_hash,
        message=issue.message,
    )

    if max_issues is None or len(result.issues) < max_issues:
        result.issues.append(issue)


def run_verify(
    storage_path: Path,
    path_prefix: str | None = None,
    limit: int | None = None,
    max_bytes: int | None = None,
    max_issues: int | None = None,
    progress_callback: VerifyProgressCallback | None = None,
) -> VerifyResult:
    """Verify stored blobs against their recorded SHA-256 hashes.

    Walks every artifact directory under the (optionally scoped) storage path,
    streams each blob, and compares its digest to the hash recorded in the
    blob's metadata sidecar. Blobs that are referenced but absent, and blobs
    whose metadata is missing or corrupt, are reported as well.

    Args:
        storage_path: Base storage path containing artifacts.
        path_prefix: Optional logical path prefix to scope the walk to.
        limit: Stop after examining this many blobs (bounds a partial scrub).
        max_bytes: Stop once this many bytes have been read.
        max_issues: Retain at most this many issues in the result. Counters
            always reflect every issue found; None retains all.
        progress_callback: Optional callback invoked as
            ``callback("verify", artifacts_done, artifacts_total)``.

    Returns:
        VerifyResult with counters and the retained issues.

    Raises:
        FileNotFoundError: If storage_path or the scoped path does not exist.
        InvalidArtifactPathError: If path_prefix escapes the storage directory.
    """
    scope = resolve_scope(storage_path, path_prefix)
    # Scopes are always resolved, so name artifacts relative to the resolved
    # base: a storage path that is relative or contains a symlinked component
    # otherwise has a different spelling than the walked directories.
    base = storage_path.resolve()

    result = VerifyResult()
    manifest_files = sorted(scope.rglob(".magpie"))
    total_artifacts = len(manifest_files)

    for idx, manifest_file in enumerate(manifest_files):
        artifact_dir = manifest_file.parent
        artifact_path = str(artifact_dir.relative_to(base))
        result.artifacts_scanned += 1

        manifest: Manifest | None = None
        try:
            manifest = read_manifest(artifact_dir)
        except (ManifestCorruptError, OSError) as e:
            _record(
                result,
                VerifyIssue(
                    artifact_path=artifact_path,
                    blob_ref="",
                    status=VerifyStatus.ERROR,
                    message=f"Failed to read manifest: {e}",
                ),
                max_issues,
            )

        for ref in _missing_blob_refs(artifact_dir, manifest):
            _record(
                result,
                VerifyIssue(
                    artifact_path=artifact_path,
                    blob_ref=ref,
                    status=VerifyStatus.MISSING_BLOB,
                    message="Referenced blob file is missing from storage",
                ),
                max_issues,
            )

        for blob_file in _iter_blob_files(artifact_dir):
            if limit is not None and result.blobs_scanned >= limit:
                result.stopped_early = True
                break

            issue, bytes_read = _verify_blob(artifact_dir, artifact_path, blob_file)
            result.blobs_scanned += 1
            result.bytes_read += bytes_read

            if issue is None:
                result.ok += 1
            else:
                _record(result, issue, max_issues)

            if max_bytes is not None and result.bytes_read >= max_bytes:
                result.stopped_early = True
                break

        if progress_callback is not None:
            progress_callback("verify", idx + 1, total_artifacts)

        if result.stopped_early:
            break

    logger.info(
        "verify_complete",
        path_prefix=path_prefix,
        artifacts_scanned=result.artifacts_scanned,
        blobs_scanned=result.blobs_scanned,
        bytes_read=result.bytes_read,
        ok=result.ok,
        mismatched=result.mismatched,
        missing_blob=result.missing_blob,
        missing_metadata=result.missing_metadata,
        corrupt_metadata=result.corrupt_metadata,
        errors=result.errors,
        stopped_early=result.stopped_early,
    )

    return result
