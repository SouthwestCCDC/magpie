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
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, NamedTuple

import structlog
from pydantic import ValidationError

from magpie.storage.exceptions import ArtifactNotFoundError, ManifestCorruptError
from magpie.storage.manifest import Manifest, read_manifest
from magpie.storage.metadata import read_metadata
from magpie.storage.paths import (
    blob_path,
    manifest_path,
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

# Manifest file name, derived from the path helpers rather than hardcoded.
MANIFEST_FILENAME = manifest_path(Path()).name

# Uploads store a blob before writing its metadata sidecar, and the sidecar is
# not written atomically, so a scrub can observe a blob whose records are still
# in flight. Records that look wrong are re-read once after this pause, bounded
# across a run so a genuinely damaged store is not slowed down.
RECHECK_DELAY_SECONDS = 0.1
RECHECK_BUDGET_SECONDS = 5.0


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


def _relative_name(directory: Path, base: Path) -> str:
    """Name a directory relative to the storage base, falling back to its path."""
    try:
        return str(directory.relative_to(base))
    except ValueError:
        return str(directory)


def _find_manifests(scope: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Find artifact manifests under a scope, reporting unreadable directories.

    ``Path.rglob`` silently swallows errors raised while descending, which would
    let a scrub skip an unreadable subtree and still report a clean store. Walk
    explicitly instead so those directories become findings.

    Returns:
        Tuple of (manifest files in stable order, (directory, message) pairs for
        directories that could not be read).
    """
    manifest_files: list[Path] = []
    errors: list[tuple[Path, str]] = []

    def on_error(e: OSError) -> None:
        errors.append((Path(e.filename or scope), f"Failed to read directory: {e}"))

    for dirpath, dirnames, filenames in os.walk(scope, onerror=on_error):
        dirnames.sort()
        if MANIFEST_FILENAME in filenames:
            manifest_files.append(Path(dirpath) / MANIFEST_FILENAME)

    return sorted(manifest_files), errors


def count_artifacts(scope: Path) -> int:
    """Count artifacts under a scope, for sizing a progress bar."""
    return len(_find_manifests(scope)[0])


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


def _list_blob_files(artifact_dir: Path) -> tuple[list[Path], list[str]]:
    """List regular blob files for an artifact in stable order.

    Symlinks are skipped: tag symlinks live in the artifact directory, and a
    symlink inside ``blobs/`` is not a stored blob.

    Returns:
        Tuple of (blob files, (blob ref, message) pairs for entries that could not
        be read; the ref is empty when the whole directory is unreadable).
    """
    blobs_dir = artifact_dir / "blobs"

    try:
        if not blobs_dir.is_dir():
            return [], []
        entries = sorted(blobs_dir.iterdir())
    except OSError as e:
        return [], [("", f"Failed to list blobs directory: {e}")]

    blob_files = []
    errors: list[tuple[str, str]] = []
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
        except OSError as e:
            errors.append((entry.name, f"Failed to stat blob: {e}"))
            continue
        blob_files.append(entry)

    return blob_files, errors


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


class _RecheckBudget:
    """Bounded total time a run may spend *waiting* before it re-reads records.

    Only the pause is budgeted; a re-read itself is always worth doing, so a run
    that has spent its budget still looks again, just without giving the writer
    extra time to finish.
    """

    def __init__(self, seconds: float = RECHECK_BUDGET_SECONDS) -> None:
        self.remaining = seconds

    def wait(self) -> None:
        """Pause for the recheck delay, unless the budget is spent."""
        if self.remaining < RECHECK_DELAY_SECONDS:
            return
        self.remaining -= RECHECK_DELAY_SECONDS
        time.sleep(RECHECK_DELAY_SECONDS)


def _reread_expected_hash(artifact_dir: Path, blob_ref: str, budget: _RecheckBudget) -> str | None:
    """Re-read a sidecar that looked absent or unparseable.

    Returns the recorded hash if the sidecar has since become readable (the
    upload that was writing it finished), else None.
    """
    budget.wait()

    try:
        return _expected_hash(artifact_dir, blob_ref)
    except (ArtifactNotFoundError, ManifestCorruptError, OSError):
        return None


class _BlobOutcome(NamedTuple):
    """Result of checking one blob.

    Attributes:
        issue: The problem found, or None when the blob verified OK.
        bytes_read: Bytes hashed.
        present: False when the blob disappeared mid-walk and nothing references
            it anymore, i.e. it was collected rather than verified. Such a blob
            is neither counted nor reported.
    """

    issue: VerifyIssue | None
    bytes_read: int
    present: bool = True


def _verify_blob(
    artifact_dir: Path, artifact_path: str, blob_file: Path, budget: _RecheckBudget
) -> _BlobOutcome:
    """Verify a single blob against its recorded hash."""
    blob_ref = blob_file.name

    try:
        expected = _expected_hash(artifact_dir, blob_ref)
    except ArtifactNotFoundError:
        reread = _reread_expected_hash(artifact_dir, blob_ref, budget)
        if reread is None:
            if not _exists(blob_file):
                return _BlobOutcome(None, 0, present=False)
            return _BlobOutcome(
                VerifyIssue(
                    artifact_path=artifact_path,
                    blob_ref=blob_ref,
                    status=VerifyStatus.MISSING_METADATA,
                    message="No metadata sidecar; recorded hash is unknown",
                ),
                0,
            )
        expected = reread
    except ManifestCorruptError as e:
        reread = _reread_expected_hash(artifact_dir, blob_ref, budget)
        if reread is None:
            if not _exists(blob_file):
                return _BlobOutcome(None, 0, present=False)
            return _BlobOutcome(
                VerifyIssue(
                    artifact_path=artifact_path,
                    blob_ref=blob_ref,
                    status=VerifyStatus.CORRUPT_METADATA,
                    message=str(e),
                ),
                0,
            )
        expected = reread
    except OSError as e:
        return _BlobOutcome(
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
    except FileNotFoundError:
        # The blob was unlinked between listing and hashing. Only a tagged blob
        # is data loss; an untagged one was collected by GC.
        if not _is_still_tagged(artifact_dir, blob_ref, budget):
            return _BlobOutcome(None, 0, present=False)
        return _BlobOutcome(
            VerifyIssue(
                artifact_path=artifact_path,
                blob_ref=blob_ref,
                status=VerifyStatus.MISSING_BLOB,
                message="Referenced blob file is missing from storage",
                expected_hash=expected,
            ),
            0,
        )
    except OSError as e:
        return _BlobOutcome(
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
        return _BlobOutcome(None, size)

    return _BlobOutcome(
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


def _exists(path: Path) -> bool:
    """Test for a path, treating an unreadable parent as "still there"."""
    try:
        return path.is_file()
    except OSError:
        return True


def _is_still_tagged(artifact_dir: Path, blob_ref: str, budget: _RecheckBudget) -> bool:
    """Re-read the manifest to see whether a tag still points at a blob.

    A tag is what makes a blob durable: GC never collects a tagged blob, so a
    tagged blob whose file is gone is data loss. Re-reading the manifest after a
    short pause (drawn from a per-run budget) collapses the window in which a
    scrub sees a tag that concurrent activity has already moved.
    """
    budget.wait()

    try:
        manifest = read_manifest(artifact_dir)
    except (ArtifactNotFoundError, ManifestCorruptError, OSError):
        return False

    return any(blob_path(artifact_dir, h).name == blob_ref for h in manifest.tags.values())


class _MissingRefs(NamedTuple):
    """Blob references whose file is gone, split by what still records them.

    Attributes:
        tagged: Refs a tag points at. GC never collects a tagged blob, so these
            are data loss.
        orphaned: Refs only a metadata sidecar records. The blob was untagged, so
            this is a bookkeeping inconsistency (typically a sidecar left behind
            by GC) rather than lost content.
        errors: (blob ref, message) pairs for records that could not be read. The
            ref is empty when the failure is not about one blob.
    """

    tagged: list[str]
    orphaned: list[str]
    errors: list[tuple[str, str]]


def _missing_blob_refs(
    artifact_dir: Path, manifest: Manifest | None, budget: _RecheckBudget
) -> _MissingRefs:
    """Find blob references that are recorded but whose blob file is gone.

    Considers both tags in the manifest and metadata sidecars. References are
    derived via the path helpers so the hash-prefix scheme stays authoritative.

    Args:
        artifact_dir: Path to the artifact directory.
        manifest: Parsed manifest, or None if it could not be read.
        budget: Recheck budget for confirming a finding.
    """
    tagged_refs: set[str] = set()
    refs: dict[str, None] = {}
    errors: list[tuple[str, str]] = []

    if manifest is not None:
        for hash_ref in manifest.tags.values():
            ref = blob_path(artifact_dir, hash_ref).name
            tagged_refs.add(ref)
            refs[ref] = None

    metadata_dir = artifact_dir / "metadata"
    try:
        if metadata_dir.is_dir():
            for sidecar in sorted(metadata_dir.glob("*.json")):
                refs[sidecar.stem] = None
    except OSError as e:
        errors.append(("", f"Failed to list metadata sidecars: {e}"))

    missing_tagged: list[str] = []
    orphaned: list[str] = []
    for ref in refs:
        try:
            present = blob_path(artifact_dir, ref).is_file()
        except OSError as e:
            errors.append((ref, f"Failed to stat blob: {e}"))
            continue
        if present:
            continue

        if ref in tagged_refs:
            if _is_still_tagged(artifact_dir, ref, budget):
                missing_tagged.append(ref)
        elif _exists(metadata_path(artifact_dir, ref)):
            orphaned.append(ref)

    return _MissingRefs(missing_tagged, orphaned, errors)


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
    budget = _RecheckBudget()
    manifest_files, walk_errors = _find_manifests(scope)
    total_artifacts = len(manifest_files)

    for directory, message in walk_errors:
        _record(
            result,
            VerifyIssue(
                artifact_path=_relative_name(directory, base),
                blob_ref="",
                status=VerifyStatus.ERROR,
                message=message,
            ),
            max_issues,
        )

    for idx, manifest_file in enumerate(manifest_files):
        artifact_dir = manifest_file.parent
        artifact_path = _relative_name(artifact_dir, base)
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

        missing = _missing_blob_refs(artifact_dir, manifest, budget)
        for ref in missing.tagged:
            _record(
                result,
                VerifyIssue(
                    artifact_path=artifact_path,
                    blob_ref=ref,
                    status=VerifyStatus.MISSING_BLOB,
                    message="Tagged blob file is missing from storage",
                ),
                max_issues,
            )

        orphan_messages = [
            (
                ref,
                "Orphan metadata sidecar: the blob is gone and no tag "
                "references it (usually left behind by GC)",
            )
            for ref in missing.orphaned
        ]

        blob_files, listing_errors = _list_blob_files(artifact_dir)
        for ref, message in (*missing.errors, *orphan_messages, *listing_errors):
            _record(
                result,
                VerifyIssue(
                    artifact_path=artifact_path,
                    blob_ref=ref,
                    status=VerifyStatus.ERROR,
                    message=message,
                ),
                max_issues,
            )

        for blob_file in blob_files:
            if limit is not None and result.blobs_scanned >= limit:
                result.stopped_early = True
                break

            outcome = _verify_blob(artifact_dir, artifact_path, blob_file, budget)
            if not outcome.present:
                continue

            result.blobs_scanned += 1
            result.bytes_read += outcome.bytes_read

            if outcome.issue is None:
                result.ok += 1
            else:
                _record(result, outcome.issue, max_issues)

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
