"""Unit tests for manifest management operations."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from magpie.storage.exceptions import ManifestCorruptError
from magpie.storage.manifest import (
    Manifest,
    read_manifest,
    remove_tag,
    update_tag,
    write_manifest,
)
from magpie.storage.paths import manifest_path


class TestManifestModel:
    """Tests for Manifest Pydantic model."""

    def test_default_manifest(self) -> None:
        """Default manifest should have version=1 and empty tags."""
        manifest = Manifest()
        assert manifest.version == 1
        assert manifest.tags == {}

    def test_manifest_with_tags(self) -> None:
        """Manifest should accept tags dictionary."""
        manifest = Manifest(tags={"latest": "@abc12345", "v1.0": "@def67890"})
        assert manifest.tags["latest"] == "@abc12345"
        assert manifest.tags["v1.0"] == "@def67890"


class TestReadWriteManifest:
    """Tests for read_manifest and write_manifest functions."""

    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        """Writing and reading manifest should preserve data."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(version=1, tags={"latest": "@abc12345"})

        write_manifest(artifact_dir, manifest)
        loaded = read_manifest(artifact_dir)

        assert loaded.version == manifest.version
        assert loaded.tags == manifest.tags

    def test_read_missing_manifest_returns_default(self, tmp_path: Path) -> None:
        """Reading non-existent manifest should return default Manifest."""
        artifact_dir = tmp_path / "nonexistent"
        artifact_dir.mkdir()

        manifest = read_manifest(artifact_dir)

        assert manifest.version == 1
        assert manifest.tags == {}

    def test_write_creates_artifact_dir(self, tmp_path: Path) -> None:
        """write_manifest should create artifact_dir if it doesn't exist."""
        artifact_dir = tmp_path / "new" / "nested" / "artifact"
        manifest = Manifest(tags={"test": "@123"})

        write_manifest(artifact_dir, manifest)

        assert artifact_dir.exists()
        assert manifest_path(artifact_dir).exists()

    def test_write_manifest_json_format(self, tmp_path: Path) -> None:
        """Written manifest should be valid JSON."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"latest": "@abc12345"})

        write_manifest(artifact_dir, manifest)

        path = manifest_path(artifact_dir)
        content = path.read_text()
        data = json.loads(content)

        assert data["version"] == 1
        assert data["tags"]["latest"] == "@abc12345"


class TestAtomicWrite:
    """Tests for atomic write behavior."""

    def test_no_temp_files_after_successful_write(self, tmp_path: Path) -> None:
        """Successful write should not leave temp files behind."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"test": "@abc"})

        write_manifest(artifact_dir, manifest)

        # Check for leftover temp files
        temp_files = list(artifact_dir.glob(".magpie_*.tmp"))
        assert len(temp_files) == 0

    def test_manifest_file_created(self, tmp_path: Path) -> None:
        """Write should create .magpie file."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"test": "@abc"})

        write_manifest(artifact_dir, manifest)

        path = manifest_path(artifact_dir)
        assert path.exists()
        assert path.name == ".magpie"


class TestUpdateTag:
    """Tests for update_tag function."""

    def test_update_tag_creates_new_tag(self, tmp_path: Path) -> None:
        """update_tag should create a new tag in empty manifest."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()

        result = update_tag(artifact_dir, "latest", "@abc12345")

        assert result.tags["latest"] == "@abc12345"
        # Verify persisted
        loaded = read_manifest(artifact_dir)
        assert loaded.tags["latest"] == "@abc12345"

    def test_update_tag_updates_existing_tag(self, tmp_path: Path) -> None:
        """update_tag should update an existing tag."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"latest": "@old12345"})
        write_manifest(artifact_dir, manifest)

        result = update_tag(artifact_dir, "latest", "@new67890")

        assert result.tags["latest"] == "@new67890"
        # Verify persisted
        loaded = read_manifest(artifact_dir)
        assert loaded.tags["latest"] == "@new67890"

    def test_update_tag_preserves_other_tags(self, tmp_path: Path) -> None:
        """update_tag should not affect other tags."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"v1.0": "@version1", "v2.0": "@version2"})
        write_manifest(artifact_dir, manifest)

        update_tag(artifact_dir, "latest", "@newlatest")

        loaded = read_manifest(artifact_dir)
        assert loaded.tags["v1.0"] == "@version1"
        assert loaded.tags["v2.0"] == "@version2"
        assert loaded.tags["latest"] == "@newlatest"


class TestRemoveTag:
    """Tests for remove_tag function."""

    def test_remove_tag_removes_existing_tag(self, tmp_path: Path) -> None:
        """remove_tag should remove an existing tag."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"latest": "@abc12345", "v1.0": "@def67890"})
        write_manifest(artifact_dir, manifest)

        result = remove_tag(artifact_dir, "latest")

        assert "latest" not in result.tags
        assert result.tags["v1.0"] == "@def67890"
        # Verify persisted
        loaded = read_manifest(artifact_dir)
        assert "latest" not in loaded.tags

    def test_remove_tag_nonexistent_no_error(self, tmp_path: Path) -> None:
        """remove_tag on non-existent tag should not raise error."""
        artifact_dir = tmp_path / "artifact"
        manifest = Manifest(tags={"v1.0": "@abc12345"})
        write_manifest(artifact_dir, manifest)

        # Should not raise
        result = remove_tag(artifact_dir, "nonexistent")

        assert result.tags == {"v1.0": "@abc12345"}

    def test_remove_tag_from_empty_manifest(self, tmp_path: Path) -> None:
        """remove_tag from empty manifest should not raise error."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()

        # Should not raise
        result = remove_tag(artifact_dir, "nonexistent")

        assert result.tags == {}


class TestConcurrentManifestUpdates:
    """Regression tests for issue #527.

    Before per-artifact locking was added, update_tag()'s read-modify-write
    cycle (read_manifest -> mutate dict -> write_manifest) was not
    serialized. Two concurrent writers setting *different* tags on the same
    artifact could both read the manifest before either had written, so
    whichever writer finished last would silently overwrite (and lose) the
    other's tag. These tests force that interleaving with a barrier and
    assert no tag is ever lost.
    """

    def test_concurrent_different_tags_no_lost_update(self, tmp_path: Path) -> None:
        """Two threads racing to set different tags must both survive.

        Runs many rounds, starting both writers at the same instant via a
        barrier each round to maximize the chance of the interleaving that
        caused #527 (both threads reading the manifest before either
        writes). Every tag set across every round must be present in the
        final manifest.
        """
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        rounds = 50
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def writer(worker_id: int) -> None:
            for round_num in range(rounds):
                barrier.wait()
                try:
                    update_tag(
                        artifact_dir,
                        f"worker{worker_id}-round{round_num}",
                        f"@{worker_id}{round_num:07d}",
                    )
                except BaseException as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

        threads = [threading.Thread(target=writer, args=(worker_id,)) for worker_id in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors during concurrent tag updates: {errors}"

        loaded = read_manifest(artifact_dir)
        expected_tags = {
            f"worker{worker_id}-round{round_num}"
            for worker_id in (0, 1)
            for round_num in range(rounds)
        }
        missing = expected_tags - set(loaded.tags.keys())
        assert not missing, f"Lost tag updates under concurrency: {sorted(missing)}"
        assert len(loaded.tags) == len(expected_tags)

    def test_concurrent_update_and_remove_different_tags(self, tmp_path: Path) -> None:
        """Concurrent update_tag and remove_tag on different tags don't race.

        One worker repeatedly adds a new tag while another repeatedly
        removes and re-adds a *different*, pre-existing tag. The tag being
        added by the first worker must never be lost due to the second
        worker's unrelated read-modify-write cycle.
        """
        artifact_dir = tmp_path / "artifact"
        write_manifest(artifact_dir, Manifest(tags={"stable": "@stable01"}))

        rounds = 50
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def adder() -> None:
            for round_num in range(rounds):
                barrier.wait()
                try:
                    update_tag(artifact_dir, f"new-tag-{round_num}", f"@new{round_num:06d}")
                except BaseException as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

        def remover() -> None:
            for round_num in range(rounds):
                barrier.wait()
                try:
                    remove_tag(artifact_dir, "stable")
                    update_tag(artifact_dir, "stable", "@stable01")
                except BaseException as exc:  # noqa: BLE001 - surfaced via assertion below
                    errors.append(exc)

        threads = [threading.Thread(target=adder), threading.Thread(target=remover)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors during concurrent updates: {errors}"

        loaded = read_manifest(artifact_dir)
        assert loaded.tags.get("stable") == "@stable01"
        missing = {f"new-tag-{i}" for i in range(rounds)} - set(loaded.tags.keys())
        assert not missing, f"Lost tag updates under concurrency: {sorted(missing)}"


class TestCorruptManifest:
    """Tests for corrupt manifest handling."""

    def test_corrupt_json_raises_manifest_corrupt_error(self, tmp_path: Path) -> None:
        """Invalid JSON should raise ManifestCorruptError."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        path = manifest_path(artifact_dir)
        path.write_text("{ invalid json }", encoding="utf-8")

        with pytest.raises(ManifestCorruptError, match="Invalid JSON"):
            read_manifest(artifact_dir)

    def test_truncated_json_raises_manifest_corrupt_error(self, tmp_path: Path) -> None:
        """Truncated JSON should raise ManifestCorruptError."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        path = manifest_path(artifact_dir)
        path.write_text('{"version": 1, "tags":', encoding="utf-8")

        with pytest.raises(ManifestCorruptError):
            read_manifest(artifact_dir)

    def test_empty_file_raises_manifest_corrupt_error(self, tmp_path: Path) -> None:
        """Empty manifest file should raise ManifestCorruptError."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        path = manifest_path(artifact_dir)
        path.write_text("", encoding="utf-8")

        with pytest.raises(ManifestCorruptError):
            read_manifest(artifact_dir)
