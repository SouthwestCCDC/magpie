"""Unit tests for shared GC module."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from magpie.storage.gc import (
    BlobToDelete,
    GCResult,
    SymlinkFixDetail,
    get_blob_age_days,
    run_gc,
)
from magpie.utils.formatting import format_size


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """Create a temporary storage root directory."""
    root = tmp_path / "storage"
    root.mkdir()
    return root


def create_artifact_with_blobs(
    storage_path: Path,
    artifact_path: str,
    tagged_hashes: dict[str, str],
    untagged_hashes: list[str],
    blob_ages_days: dict[str, int] | None = None,
) -> Path:
    """Helper to create an artifact directory with blobs and manifest.

    Simulates the actual storage scheme where:
    - Manifest stores full hashes
    - Blob files use first 8 chars of hash
    - Metadata files use first 8 chars of hash (but contain full hash inside)

    Args:
        storage_path: Base storage path.
        artifact_path: Relative artifact path.
        tagged_hashes: Dict of tag_name -> hash for tagged blobs.
        untagged_hashes: List of hashes for untagged blobs.
        blob_ages_days: Optional dict of hash -> age in days for metadata.

    Returns:
        Path to the artifact directory.
    """
    artifact_dir = storage_path / artifact_path
    blobs_dir = artifact_dir / "blobs"
    metadata_dir = artifact_dir / "metadata"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    # Create manifest (stores full hashes)
    manifest = {
        "version": 1,
        "tags": tagged_hashes,
    }
    manifest_file = artifact_dir / ".magpie"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

    # Create all blobs and their metadata
    all_hashes = list(tagged_hashes.values()) + untagged_hashes
    blob_ages_days = blob_ages_days or {}

    for blob_hash in all_hashes:
        # Blob and metadata files use short hash (first 8 chars)
        short_hash = blob_hash[:8]

        # Create blob file with short hash name
        blob_file = blobs_dir / short_hash
        blob_file.write_bytes(b"test content for " + blob_hash.encode())

        # Create metadata with upload timestamp (short hash filename, full hash inside)
        age_days = blob_ages_days.get(blob_hash, 0)
        upload_time = datetime.now(timezone.utc) - timedelta(days=age_days)
        metadata = {
            "hash": blob_hash,  # Full hash stored inside metadata
            "uploaded_by": "test",
            "uploaded_at": upload_time.isoformat(),
            "source_uri": None,
        }
        metadata_file = metadata_dir / f"{short_hash}.json"
        metadata_file.write_text(json.dumps(metadata), encoding="utf-8")

    return artifact_dir


class TestFormatSize:
    """Tests for format_size function."""

    def test_bytes(self) -> None:
        """Formats bytes correctly."""
        assert format_size(0) == "0 B"
        assert format_size(100) == "100 B"
        assert format_size(1023) == "1023 B"

    def test_kilobytes(self) -> None:
        """Formats kilobytes correctly."""
        assert format_size(1024) == "1.0 KB"
        assert format_size(1536) == "1.5 KB"
        assert format_size(1024 * 1024 - 1) == "1024.0 KB"

    def test_megabytes(self) -> None:
        """Formats megabytes correctly."""
        assert format_size(1024 * 1024) == "1.0 MB"
        assert format_size(1024 * 1024 * 1.5) == "1.5 MB"
        assert format_size(1024 * 1024 * 1024 - 1) == "1024.0 MB"

    def test_gigabytes(self) -> None:
        """Formats gigabytes correctly."""
        assert format_size(1024 * 1024 * 1024) == "1.0 GB"
        assert format_size(1024 * 1024 * 1024 * 2.5) == "2.5 GB"


class TestGetBlobAgeDays:
    """Tests for get_blob_age_days function."""

    def test_gets_age_from_metadata(self, storage_root: Path) -> None:
        """Gets blob age from metadata file."""
        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={},
            untagged_hashes=["hash1234567890"],
            blob_ages_days={"hash1234567890": 30},
        )

        now = datetime.now(timezone.utc)
        age = get_blob_age_days(artifact_dir, "hash1234", now)

        assert age == 30

    def test_falls_back_to_mtime(self, storage_root: Path) -> None:
        """Falls back to file mtime when metadata is missing."""
        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={},
            untagged_hashes=["hash1234567890"],
        )

        # Delete metadata to force mtime fallback
        metadata_file = artifact_dir / "metadata" / "hash1234.json"
        metadata_file.unlink()

        now = datetime.now(timezone.utc)
        age = get_blob_age_days(artifact_dir, "hash1234", now)

        # File was just created, so age should be 0
        assert age == 0

    def test_returns_none_for_missing_blob(self, storage_root: Path) -> None:
        """Returns None when blob doesn't exist."""
        artifact_dir = storage_root / "test/artifact"
        artifact_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        age = get_blob_age_days(artifact_dir, "nonexistent", now)

        assert age is None


class TestGCResult:
    """Tests for GCResult dataclass."""

    def test_default_values(self) -> None:
        """Default values are all zero/empty."""
        result = GCResult()
        assert result.artifacts_scanned == 0
        assert result.blobs_found == 0
        assert result.blobs_deleted == 0
        assert result.space_reclaimed_bytes == 0
        assert result.symlinks_checked == 0
        assert result.symlinks_fixed == 0
        assert result.items_removed == 0
        assert result.symlink_fix_details == []

    def test_to_dict(self) -> None:
        """to_dict returns correct dictionary."""
        result = GCResult(
            artifacts_scanned=5,
            blobs_found=10,
            blobs_deleted=2,
            space_reclaimed_bytes=1024,
            symlinks_checked=5,
            symlinks_fixed=1,
            items_removed=3,
        )
        d = result.to_dict()
        assert d["artifacts_scanned"] == 5
        assert d["blobs_found"] == 10
        assert d["blobs_deleted"] == 2
        assert d["space_reclaimed_bytes"] == 1024
        assert d["symlinks_checked"] == 5
        assert d["symlinks_fixed"] == 1
        assert d["items_removed"] == 3


class TestSymlinkFixDetail:
    """Tests for SymlinkFixDetail dataclass."""

    def test_str_format(self) -> None:
        """String format is correct."""
        detail = SymlinkFixDetail(
            artifact_path="test/artifact",
            created_tags=["latest"],
            removed_tags=["old"],
            updated_tags=["stable"],
        )
        s = str(detail)
        assert "test/artifact" in s
        assert "created 'latest'" in s
        assert "removed 'old'" in s
        assert "updated 'stable'" in s

    def test_str_format_partial(self) -> None:
        """String format works with partial data."""
        detail = SymlinkFixDetail(
            artifact_path="test/artifact",
            created_tags=["latest"],
        )
        s = str(detail)
        assert "test/artifact" in s
        assert "created 'latest'" in s
        assert "removed" not in s
        assert "updated" not in s


class TestRunGC:
    """Tests for run_gc function."""

    def test_raises_for_missing_storage(self, tmp_path: Path) -> None:
        """Raises FileNotFoundError for missing storage path."""
        with pytest.raises(FileNotFoundError):
            run_gc(
                storage_path=tmp_path / "nonexistent",
                retention_days=30,
            )

    def test_scans_empty_storage(self, storage_root: Path) -> None:
        """Handles empty storage gracefully."""
        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
        )

        assert result.artifacts_scanned == 0
        assert result.blobs_found == 0
        assert blobs == []

    def test_identifies_untagged_blobs(self, storage_root: Path) -> None:
        """Identifies untagged blobs correctly."""
        create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["untagged_hash_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "untagged_hash_xyz": 100},
        )

        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
            dry_run=True,
        )

        assert result.artifacts_scanned == 1
        assert result.blobs_found == 2
        assert result.blobs_deleted == 1
        assert len(blobs) == 1
        assert blobs[0].blob_hash == "untagged"

    def test_dry_run_does_not_delete(self, storage_root: Path) -> None:
        """Dry run doesn't delete blobs."""
        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["untagged_hash_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "untagged_hash_xyz": 100},
        )

        blob_file = artifact_dir / "blobs" / "untagged"
        assert blob_file.exists()

        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
            dry_run=True,
        )

        assert result.blobs_deleted == 1
        assert blob_file.exists()  # Still exists after dry run

    def test_deletes_expired_blobs(self, storage_root: Path) -> None:
        """Deletes untagged blobs older than retention period."""
        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["old_untagged_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "old_untagged_xyz": 100},
        )

        blob_file = artifact_dir / "blobs" / "old_unta"
        assert blob_file.exists()

        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
            dry_run=False,
        )

        assert result.blobs_deleted == 1
        assert not blob_file.exists()  # Deleted

    def test_preserves_young_blobs(self, storage_root: Path) -> None:
        """Doesn't delete blobs younger than retention period."""
        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["young_untagged_xyz"],
            blob_ages_days={
                "tagged_hash_abc": 0,
                "young_untagged_xyz": 10,  # Less than 30 day retention
            },
        )

        blob_file = artifact_dir / "blobs" / "young_un"
        assert blob_file.exists()

        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
            dry_run=False,
        )

        assert result.blobs_deleted == 0
        assert blob_file.exists()

    def test_preserves_tagged_blobs(self, storage_root: Path) -> None:
        """Never deletes tagged blobs regardless of age."""
        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={"latest": "old_tagged_hash"},
            untagged_hashes=[],
            blob_ages_days={"old_tagged_hash": 365},  # Very old but tagged
        )

        blob_file = artifact_dir / "blobs" / "old_tagg"
        assert blob_file.exists()

        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
            dry_run=False,
        )

        assert result.blobs_deleted == 0
        assert blob_file.exists()

    def test_unusable_tag_target_does_not_abort_the_run(self, storage_root: Path) -> None:
        """One hand-edited tag target must not stop the whole GC run."""
        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc", "broken": "../../etc/passwd"},
            untagged_hashes=["untagged_hash_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "untagged_hash_xyz": 100},
        )

        result, blobs = run_gc(storage_path=storage_root, retention_days=30, dry_run=False)

        assert result.artifacts_scanned == 1
        assert result.blobs_deleted == 1
        assert (artifact_dir / "blobs" / "tagged_h").exists()

    def test_preserves_tagged_blobs_with_full_sha256_hash(self, storage_root: Path) -> None:
        """Tagged blobs with full SHA-256 hashes are preserved.

        Regression test for issue where GC used full 64-char hashes for tag
        comparison, but blob files use short 8-char hashes. This caused tagged
        blobs to be incorrectly garbage collected because "abcd1234..." (64 chars)
        != "abcd1234" (8 chars).

        The fix is to truncate manifest hashes to 8 chars before comparison:
            tagged_hashes = {h[:8] for h in manifest.tags.values()}
        """
        # Use a realistic full SHA-256 hash (64 hex characters)
        full_hash = "a1b2c3d4e5f67890abcdef1234567890fedcba0987654321a1b2c3d4e5f67890"
        assert len(full_hash) == 64, "Test requires 64-char hash"

        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={"latest": full_hash},
            untagged_hashes=[],
            blob_ages_days={full_hash: 365},  # Very old but tagged
        )

        # Blob file uses short hash (first 8 chars)
        short_hash = full_hash[:8]
        blob_file = artifact_dir / "blobs" / short_hash
        assert blob_file.exists()
        assert blob_file.name == "a1b2c3d4"

        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
            dry_run=False,
        )

        # The blob should NOT be deleted because it's tagged
        # If full hashes were used for comparison, this would fail
        assert result.blobs_deleted == 0, (
            "Tagged blob was deleted! "
            "GC may be using full hashes instead of short hashes for tag lookup."
        )
        assert blob_file.exists(), "Tagged blob file was deleted"

    def test_reconcile_only_skips_deletion(self, storage_root: Path) -> None:
        """reconcile_only mode only fixes symlinks."""
        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["old_untagged_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "old_untagged_xyz": 100},
        )

        blob_file = artifact_dir / "blobs" / "old_unta"
        assert blob_file.exists()

        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
            reconcile_only=True,
        )

        # No blobs should be collected for deletion
        assert result.blobs_found == 0  # Not scanned for deletion
        assert result.blobs_deleted == 0
        assert blobs == []
        assert blob_file.exists()  # Not deleted

        # But symlinks should be checked
        assert result.symlinks_checked > 0

    def test_multiple_artifacts(self, storage_root: Path) -> None:
        """Processes multiple artifacts correctly."""
        create_artifact_with_blobs(
            storage_root,
            "project1/app",
            tagged_hashes={"latest": "hash_a"},
            untagged_hashes=["old_hash_b"],
            blob_ages_days={"hash_a": 0, "old_hash_b": 100},
        )
        create_artifact_with_blobs(
            storage_root,
            "project2/lib",
            tagged_hashes={"latest": "hash_c"},
            untagged_hashes=["old_hash_d"],
            blob_ages_days={"hash_c": 0, "old_hash_d": 100},
        )

        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
            dry_run=False,
        )

        assert result.artifacts_scanned == 2
        assert result.blobs_deleted == 2

    def test_reconciles_symlinks(self, storage_root: Path) -> None:
        """Creates missing symlinks during GC."""
        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=[],
        )

        # Symlink should not exist yet
        symlink = artifact_dir / "latest"
        assert not symlink.exists()

        result, _ = run_gc(
            storage_path=storage_root,
            retention_days=30,
        )

        assert result.symlinks_fixed > 0
        assert symlink.is_symlink()

    def test_progress_callback_called(self, storage_root: Path) -> None:
        """Progress callback is called during scan."""
        create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={"latest": "hash_abc"},
            untagged_hashes=[],
        )

        calls = []

        def callback(phase: str, current: int, total: int) -> None:
            calls.append((phase, current, total))

        run_gc(
            storage_path=storage_root,
            retention_days=30,
            progress_callback=callback,
        )

        assert len(calls) > 0
        assert calls[0][0] == "scan"
        assert calls[0][2] == 1  # Total artifacts

    def test_deletes_metadata_sidecar(self, storage_root: Path) -> None:
        """Deletes metadata sidecar when deleting blob."""
        artifact_dir = create_artifact_with_blobs(
            storage_root,
            "test/artifact",
            tagged_hashes={},
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"old_hash_xyz": 100},
        )

        blob_file = artifact_dir / "blobs" / "old_hash"
        metadata_file = artifact_dir / "metadata" / "old_hash.json"
        assert blob_file.exists()
        assert metadata_file.exists()

        run_gc(
            storage_path=storage_root,
            retention_days=30,
            dry_run=False,
        )

        assert not blob_file.exists()
        assert not metadata_file.exists()


class TestCorruptManifestHandling:
    """Tests for GC handling of corrupt manifests."""

    def test_skips_artifact_with_corrupt_json(self, storage_root: Path) -> None:
        """GC skips blob processing for artifacts with invalid JSON in manifest."""
        # Create valid artifact
        create_artifact_with_blobs(
            storage_root,
            "valid/artifact",
            tagged_hashes={"latest": "hash_abc12345"},
            untagged_hashes=[],
        )

        # Create artifact with corrupt JSON manifest
        corrupt_dir = storage_root / "corrupt/artifact"
        corrupt_dir.mkdir(parents=True)
        manifest_file = corrupt_dir / ".magpie"
        manifest_file.write_text("{invalid json", encoding="utf-8")

        # GC should encounter both artifacts but skip processing corrupt one
        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
        )

        # Both artifacts are scanned (counter increments before read)
        assert result.artifacts_scanned == 2
        # But only valid artifact's blobs are processed
        assert result.blobs_found == 1

    def test_skips_artifact_with_invalid_utf8(self, storage_root: Path) -> None:
        """GC skips blob processing for artifacts with invalid UTF-8 bytes in manifest."""
        # Create valid artifact
        create_artifact_with_blobs(
            storage_root,
            "valid/artifact",
            tagged_hashes={"latest": "hash_abc12345"},
            untagged_hashes=[],
        )

        # Create artifact with invalid UTF-8 in manifest
        corrupt_dir = storage_root / "corrupt/artifact"
        corrupt_dir.mkdir(parents=True)
        manifest_file = corrupt_dir / ".magpie"
        # Write invalid UTF-8 bytes
        manifest_file.write_bytes(b"\xff\xfe{invalid utf8}")

        # GC should encounter both artifacts but skip processing corrupt one
        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
        )

        # Both artifacts are scanned
        assert result.artifacts_scanned == 2
        # But only valid artifact's blobs are processed
        assert result.blobs_found == 1

    def test_skips_artifact_with_permission_denied(self, storage_root: Path) -> None:
        """GC skips blob processing for artifacts with permission denied on manifest."""
        # Create valid artifact
        create_artifact_with_blobs(
            storage_root,
            "valid/artifact",
            tagged_hashes={"latest": "hash_abc12345"},
            untagged_hashes=[],
        )

        # Create artifact with unreadable manifest
        restricted_dir = storage_root / "restricted/artifact"
        restricted_dir.mkdir(parents=True)
        manifest_file = restricted_dir / ".magpie"
        manifest_file.write_text('{"version": 1, "tags": {}}', encoding="utf-8")
        # Make manifest unreadable
        manifest_file.chmod(0o000)

        try:
            # GC should encounter both artifacts but skip processing restricted one
            result, blobs = run_gc(
                storage_path=storage_root,
                retention_days=30,
            )

            # Both artifacts are scanned
            assert result.artifacts_scanned == 2
            # But only valid artifact's blobs are processed
            assert result.blobs_found == 1
        finally:
            # Restore permissions for cleanup
            manifest_file.chmod(0o644)

    def test_skips_artifact_with_invalid_manifest_schema(self, storage_root: Path) -> None:
        """GC skips blob processing for artifacts with Pydantic validation errors."""
        # Create valid artifact
        create_artifact_with_blobs(
            storage_root,
            "valid/artifact",
            tagged_hashes={"latest": "hash_abc12345"},
            untagged_hashes=[],
        )

        # Create artifact with invalid manifest schema (wrong version type)
        invalid_dir = storage_root / "invalid/artifact"
        invalid_dir.mkdir(parents=True)
        manifest_file = invalid_dir / ".magpie"
        manifest_file.write_text('{"version": "not_an_int", "tags": {}}', encoding="utf-8")

        # GC should encounter both artifacts but skip processing invalid one
        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
        )

        # Both artifacts are scanned
        assert result.artifacts_scanned == 2
        # But only valid artifact's blobs are processed
        assert result.blobs_found == 1

    def test_skips_multiple_corrupt_artifacts(self, storage_root: Path) -> None:
        """GC continues scanning despite multiple corrupt artifacts."""
        # Create several artifacts with various corruption types
        corrupt1 = storage_root / "corrupt1/artifact"
        corrupt1.mkdir(parents=True)
        (corrupt1 / ".magpie").write_text("{bad json", encoding="utf-8")

        corrupt2 = storage_root / "corrupt2/artifact"
        corrupt2.mkdir(parents=True)
        (corrupt2 / ".magpie").write_bytes(b"\xff\xfe")

        # Create valid artifacts interspersed
        create_artifact_with_blobs(
            storage_root,
            "valid1/artifact",
            tagged_hashes={"latest": "hash_abc12345"},
            untagged_hashes=[],
        )
        create_artifact_with_blobs(
            storage_root,
            "valid2/artifact",
            tagged_hashes={"stable": "hash_def67890"},
            untagged_hashes=[],
        )

        # GC should encounter all 4 artifacts but only process valid ones
        result, blobs = run_gc(
            storage_path=storage_root,
            retention_days=30,
        )

        # All 4 artifacts are scanned (2 valid + 2 corrupt)
        assert result.artifacts_scanned == 4
        # But only valid artifacts' blobs are processed
        assert result.blobs_found == 2


class TestBlobToDelete:
    """Tests for BlobToDelete dataclass."""

    def test_creation(self, tmp_path: Path) -> None:
        """Can create BlobToDelete instances."""
        blob_file = tmp_path / "blob"
        blob_file.write_bytes(b"test")

        blob = BlobToDelete(
            blob_file=blob_file,
            artifact_path="test/artifact",
            blob_hash="abc12345",
            age_days=100,
            size=4,
            metadata_file=None,
        )

        assert blob.blob_file == blob_file
        assert blob.artifact_path == "test/artifact"
        assert blob.blob_hash == "abc12345"
        assert blob.age_days == 100
        assert blob.size == 4
        assert blob.metadata_file is None
