"""Unit tests for blob integrity verification (scrub)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.paths import blob_path, metadata_path
from magpie.storage.verify import (
    VerifyStatus,
    resolve_scope,
    run_verify,
)


def write_blob(
    storage_path: Path,
    artifact_path: str,
    content: bytes,
    tag: str | None = "latest",
    metadata: bool = True,
    stored_content: bytes | None = None,
    recorded_hash: str | None = None,
) -> str:
    """Create a stored blob (and optionally its tag and metadata sidecar).

    Args:
        storage_path: Base storage path.
        artifact_path: Logical artifact path.
        content: Content whose hash is recorded in metadata.
        tag: Tag name to record in the manifest (None for an untagged blob).
        metadata: Whether to write the metadata sidecar.
        stored_content: Content actually written to disk, if it should differ
            from ``content`` (simulates corruption or truncation).
        recorded_hash: Override the hash written into the metadata sidecar.

    Returns:
        Full SHA-256 hex digest of ``content``.
    """
    artifact_dir = storage_path / artifact_path
    artifact_dir.mkdir(parents=True, exist_ok=True)

    full_hash = hashlib.sha256(content).hexdigest()

    blob_file = blob_path(artifact_dir, full_hash)
    blob_file.parent.mkdir(parents=True, exist_ok=True)
    blob_file.write_bytes(content if stored_content is None else stored_content)

    if metadata:
        sidecar = metadata_path(artifact_dir, full_hash)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps(
                {
                    "hash": recorded_hash or full_hash,
                    "uploaded_by": "test",
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    "source_uri": None,
                }
            ),
            encoding="utf-8",
        )

    manifest_file = artifact_dir / ".magpie"
    manifest = {"version": 1, "tags": {}}
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if tag is not None:
        manifest["tags"][tag] = full_hash
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

    return full_hash


@pytest.fixture
def storage_path(tmp_path: Path) -> Path:
    """Create an empty storage directory."""
    path = tmp_path / "storage"
    path.mkdir()
    return path


class TestRunVerifyHealthy:
    """Verification of intact storage."""

    def test_empty_storage_is_clean(self, storage_path: Path) -> None:
        result = run_verify(storage_path)

        assert result.artifacts_scanned == 0
        assert result.blobs_scanned == 0
        assert result.total_issues == 0

    def test_intact_blobs_verify_ok(self, storage_path: Path) -> None:
        write_blob(storage_path, "images/ubuntu", b"hello world")
        write_blob(storage_path, "certs/ca", b"-----BEGIN CERTIFICATE-----")

        result = run_verify(storage_path)

        assert result.artifacts_scanned == 2
        assert result.blobs_scanned == 2
        assert result.ok == 2
        assert result.total_issues == 0
        assert result.issues == []
        assert result.bytes_read == len(b"hello world") + len(b"-----BEGIN CERTIFICATE-----")

    def test_untagged_blob_is_still_verified(self, storage_path: Path) -> None:
        write_blob(storage_path, "images/ubuntu", b"untagged content", tag=None)

        result = run_verify(storage_path)

        assert result.blobs_scanned == 1
        assert result.ok == 1

    def test_tag_symlinks_are_not_counted_as_blobs(self, storage_path: Path) -> None:
        full_hash = write_blob(storage_path, "images/ubuntu", b"content")
        artifact_dir = storage_path / "images/ubuntu"
        (artifact_dir / "latest").symlink_to(blob_path(artifact_dir, full_hash))

        result = run_verify(storage_path)

        assert result.blobs_scanned == 1
        assert result.ok == 1


class TestRunVerifyCorruption:
    """Detection of damaged or missing storage."""

    def test_corrupted_blob_is_reported(self, storage_path: Path) -> None:
        content = b"competition-critical key material"
        full_hash = write_blob(
            storage_path,
            "openvpn/ca",
            content,
            stored_content=b"X" * len(content),
        )

        result = run_verify(storage_path)

        assert result.blobs_scanned == 1
        assert result.ok == 0
        assert result.mismatched == 1
        issue = result.issues[0]
        assert issue.status == VerifyStatus.MISMATCH
        assert issue.artifact_path == "openvpn/ca"
        assert issue.expected_hash == full_hash
        assert issue.actual_hash == hashlib.sha256(b"X" * len(content)).hexdigest()
        assert issue.size_bytes == len(content)

    def test_truncated_blob_is_reported(self, storage_path: Path) -> None:
        write_blob(storage_path, "openvpn/ca", b"full content here", stored_content=b"full con")

        result = run_verify(storage_path)

        assert result.mismatched == 1
        assert result.issues[0].status == VerifyStatus.MISMATCH
        assert result.issues[0].size_bytes == len(b"full con")

    def test_missing_blob_is_reported(self, storage_path: Path) -> None:
        full_hash = write_blob(storage_path, "openvpn/ca", b"content")
        blob_path(storage_path / "openvpn/ca", full_hash).unlink()

        result = run_verify(storage_path)

        assert result.blobs_scanned == 0
        assert result.missing_blob == 1
        issue = result.issues[0]
        assert issue.status == VerifyStatus.MISSING_BLOB
        assert issue.blob_ref == blob_path(storage_path / "openvpn/ca", full_hash).name

    def test_missing_blob_reported_once_when_tagged_and_has_metadata(
        self, storage_path: Path
    ) -> None:
        full_hash = write_blob(storage_path, "openvpn/ca", b"content")
        blob_path(storage_path / "openvpn/ca", full_hash).unlink()

        result = run_verify(storage_path)

        assert result.missing_blob == 1
        assert len(result.issues) == 1

    def test_missing_metadata_is_reported(self, storage_path: Path) -> None:
        write_blob(storage_path, "openvpn/ca", b"content", metadata=False)

        result = run_verify(storage_path)

        assert result.blobs_scanned == 1
        assert result.ok == 0
        assert result.missing_metadata == 1
        assert result.issues[0].status == VerifyStatus.MISSING_METADATA

    def test_corrupt_metadata_is_reported(self, storage_path: Path) -> None:
        full_hash = write_blob(storage_path, "openvpn/ca", b"content")
        metadata_path(storage_path / "openvpn/ca", full_hash).write_text(
            "{not json", encoding="utf-8"
        )

        result = run_verify(storage_path)

        assert result.corrupt_metadata == 1
        assert result.issues[0].status == VerifyStatus.CORRUPT_METADATA

    def test_metadata_missing_required_field_is_corrupt(self, storage_path: Path) -> None:
        full_hash = write_blob(storage_path, "openvpn/ca", b"content")
        metadata_path(storage_path / "openvpn/ca", full_hash).write_text(
            json.dumps({"uploaded_by": "test"}), encoding="utf-8"
        )

        result = run_verify(storage_path)

        assert result.corrupt_metadata == 1

    def test_corrupt_manifest_is_reported_as_error(self, storage_path: Path) -> None:
        write_blob(storage_path, "openvpn/ca", b"content")
        (storage_path / "openvpn/ca/.magpie").write_text("{broken", encoding="utf-8")

        result = run_verify(storage_path)

        # Blobs are still verified even when the manifest is unreadable.
        assert result.ok == 1
        assert result.errors == 1
        assert result.issues[0].status == VerifyStatus.ERROR

    def test_metadata_recording_wrong_hash_is_mismatch(self, storage_path: Path) -> None:
        other_hash = hashlib.sha256(b"something else").hexdigest()
        write_blob(storage_path, "openvpn/ca", b"content", recorded_hash=other_hash)

        result = run_verify(storage_path)

        assert result.mismatched == 1
        assert result.issues[0].expected_hash == other_hash


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
class TestRunVerifyUnreadable:
    """Storage the scrub cannot read must be reported, never silently skipped."""

    def test_unreadable_subtree_is_reported_as_an_error(self, storage_path: Path) -> None:
        write_blob(storage_path, "images/ubuntu", b"image content")
        write_blob(storage_path, "openvpn/ca", b"ca content")
        locked = storage_path / "openvpn"
        locked.chmod(0o000)

        try:
            result = run_verify(storage_path)
        finally:
            locked.chmod(0o755)

        assert result.artifacts_scanned == 1
        assert result.errors == 1
        assert result.issues[0].status == VerifyStatus.ERROR
        assert "openvpn" in result.issues[0].artifact_path

    def test_unreadable_blobs_directory_is_reported_as_an_error(self, storage_path: Path) -> None:
        write_blob(storage_path, "openvpn/ca", b"ca content")
        locked = storage_path / "openvpn/ca/blobs"
        locked.chmod(0o000)

        try:
            result = run_verify(storage_path)
        finally:
            locked.chmod(0o755)

        assert result.artifacts_scanned == 1
        assert result.blobs_scanned == 0
        assert result.errors >= 1
        assert any(issue.status == VerifyStatus.ERROR for issue in result.issues)


class TestRunVerifyConcurrentCollection:
    """A blob nothing references anymore was collected, not lost."""

    def test_unreferenced_missing_blob_is_not_reported(self, storage_path: Path) -> None:
        full_hash = write_blob(storage_path, "openvpn/ca", b"content", tag=None)
        artifact_dir = storage_path / "openvpn/ca"
        blob_path(artifact_dir, full_hash).unlink()
        metadata_path(artifact_dir, full_hash).unlink()

        result = run_verify(storage_path)

        assert result.missing_blob == 0
        assert result.total_issues == 0

    def test_tagged_missing_blob_is_reported_without_its_sidecar(self, storage_path: Path) -> None:
        full_hash = write_blob(storage_path, "openvpn/ca", b"content")
        artifact_dir = storage_path / "openvpn/ca"
        blob_path(artifact_dir, full_hash).unlink()
        metadata_path(artifact_dir, full_hash).unlink()

        result = run_verify(storage_path)

        assert result.missing_blob == 1
        assert result.issues[0].status == VerifyStatus.MISSING_BLOB


class TestRunVerifyScoping:
    """Path-prefix scoping."""

    def test_prefix_limits_the_walk(self, storage_path: Path) -> None:
        write_blob(storage_path, "openvpn/ca", b"ca content")
        write_blob(storage_path, "images/ubuntu", b"image content")

        result = run_verify(storage_path, path_prefix="openvpn")

        assert result.artifacts_scanned == 1
        assert result.blobs_scanned == 1
        assert result.bytes_read == len(b"ca content")

    def test_prefix_may_name_an_artifact(self, storage_path: Path) -> None:
        write_blob(storage_path, "openvpn/ca", b"ca content")
        write_blob(storage_path, "openvpn/client", b"client content")

        result = run_verify(storage_path, path_prefix="openvpn/ca")

        assert result.artifacts_scanned == 1
        assert result.ok == 1

    def test_prefix_is_normalized(self, storage_path: Path) -> None:
        write_blob(storage_path, "openvpn/ca", b"ca content")

        result = run_verify(storage_path, path_prefix="/openvpn//ca/")

        assert result.artifacts_scanned == 1

    def test_unknown_prefix_raises(self, storage_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            run_verify(storage_path, path_prefix="nope")

    def test_traversal_prefix_is_rejected(self, storage_path: Path) -> None:
        with pytest.raises(InvalidArtifactPathError):
            run_verify(storage_path, path_prefix="../etc")

    def test_missing_storage_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            run_verify(tmp_path / "absent")

    def test_resolve_scope_defaults_to_storage_root(self, storage_path: Path) -> None:
        assert resolve_scope(storage_path, None) == storage_path.resolve()

    def test_symlinked_storage_path_names_artifacts_relative_to_the_base(
        self, storage_path: Path, tmp_path: Path
    ) -> None:
        write_blob(storage_path, "openvpn/ca", b"ca content", stored_content=b"tampered!!")
        link = tmp_path / "storage-link"
        link.symlink_to(storage_path)

        result = run_verify(link, path_prefix="openvpn")

        assert result.mismatched == 1
        assert result.issues[0].artifact_path == "openvpn/ca"


class TestRunVerifyBounds:
    """Work-bounding options."""

    def test_limit_stops_after_n_blobs(self, storage_path: Path) -> None:
        for i in range(5):
            write_blob(storage_path, f"images/img{i}", f"content {i}".encode())

        result = run_verify(storage_path, limit=2)

        assert result.blobs_scanned == 2
        assert result.stopped_early is True

    def test_limit_not_reached_does_not_flag_stopped_early(self, storage_path: Path) -> None:
        write_blob(storage_path, "images/img", b"content")

        result = run_verify(storage_path, limit=10)

        assert result.blobs_scanned == 1
        assert result.stopped_early is False

    def test_max_bytes_stops_the_walk(self, storage_path: Path) -> None:
        for i in range(4):
            write_blob(storage_path, f"images/img{i}", b"0123456789")

        result = run_verify(storage_path, max_bytes=15)

        assert result.blobs_scanned == 2
        assert result.bytes_read == 20
        assert result.stopped_early is True

    def test_max_issues_caps_reported_issues_but_not_counts(self, storage_path: Path) -> None:
        for i in range(3):
            write_blob(
                storage_path,
                f"images/img{i}",
                f"content {i}".encode(),
                stored_content=b"corrupt",
            )

        result = run_verify(storage_path, max_issues=1)

        assert result.mismatched == 3
        assert len(result.issues) == 1


class TestRunVerifyProgress:
    """Progress reporting."""

    def test_progress_callback_reports_artifact_completion(self, storage_path: Path) -> None:
        write_blob(storage_path, "images/a", b"a")
        write_blob(storage_path, "images/b", b"b")
        calls: list[tuple[str, int, int]] = []

        run_verify(storage_path, progress_callback=lambda *args: calls.append(args))

        assert calls == [("verify", 1, 2), ("verify", 2, 2)]


class TestVerifyResultSerialization:
    """JSON serialization of results."""

    def test_to_dict_includes_counters_and_issues(self, storage_path: Path) -> None:
        write_blob(storage_path, "openvpn/ca", b"content", stored_content=b"other")

        data = run_verify(storage_path).to_dict()

        assert data["blobs_scanned"] == 1
        assert data["mismatched"] == 1
        assert data["stopped_early"] is False
        assert data["issues"][0]["status"] == "mismatch"
        assert data["issues"][0]["artifact_path"] == "openvpn/ca"
        assert json.dumps(data)
