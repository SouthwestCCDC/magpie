"""Integration tests for StorageService."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from magpie.config import MagpieSettings
from magpie.storage.exceptions import ArtifactNotFoundError
from magpie.storage.hash import HASH_NAME_LENGTH
from magpie.storage.paths import artifact_dir_path
from magpie.storage.service import ArtifactInfo, StorageService


class TestStoreListGetFlow:
    """Tests for the full store -> list -> get workflow."""

    def test_store_list_get_flow(self, storage_service: StorageService) -> None:
        """Test complete artifact lifecycle: store, list, get."""
        artifact_path = "project/component"
        content = b"test artifact content"
        stream = io.BytesIO(content)

        # Store
        info, is_duplicate = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=stream,
            uploaded_by="test-user",
            source_uri="http://example.com/source",
        )

        assert is_duplicate is False
        assert info.uploaded_by == "test-user"
        assert info.source_uri == "http://example.com/source"
        assert "latest" in info.tags
        assert info.hash_ref.startswith("@")

        # List
        artifacts = storage_service.list_artifacts(artifact_path)
        assert len(artifacts) == 1
        assert artifacts[0].hash == info.hash
        assert "latest" in artifacts[0].tags

        # Get by tag
        retrieved = storage_service.get_artifact_info(artifact_path, "latest")
        assert retrieved.hash == info.hash
        assert retrieved.uploaded_by == info.uploaded_by

        # Get by hash_ref
        retrieved_by_hash = storage_service.get_artifact_info(artifact_path, info.hash_ref)
        assert retrieved_by_hash.hash == info.hash

    def test_store_returns_artifact_info(self, storage_service: StorageService) -> None:
        """store_artifact should return complete ArtifactInfo."""
        info, _ = storage_service.store_artifact(
            artifact_path="test/artifact",
            file_stream=io.BytesIO(b"content"),
            uploaded_by="uploader",
            source_uri="s3://bucket/key",
        )

        assert isinstance(info, ArtifactInfo)
        assert len(info.hash) == 64  # SHA-256 hex length
        assert info.hash_ref.startswith("@")
        assert len(info.hash_ref) == HASH_NAME_LENGTH + 1  # @ + hash name
        assert info.uploaded_by == "uploader"
        assert info.source_uri == "s3://bucket/key"
        assert info.uploaded_at is not None


class TestDuplicateHandling:
    """Tests for duplicate artifact detection."""

    def test_duplicate_detection(self, storage_service: StorageService) -> None:
        """Storing same content twice should return is_duplicate=True."""
        artifact_path = "test/duplicate"
        content = b"duplicate content test"

        # First store
        info1, is_dup1 = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(content),
            uploaded_by="user1",
        )
        assert is_dup1 is False

        # Second store of same content
        info2, is_dup2 = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(content),
            uploaded_by="user2",
        )
        assert is_dup2 is True

        # Both should have same hash
        assert info1.hash == info2.hash
        assert info1.hash_ref == info2.hash_ref

    def test_duplicate_preserves_original_metadata(self, storage_service: StorageService) -> None:
        """Duplicate upload should preserve original uploader info."""
        artifact_path = "test/preserve"
        content = b"preserve metadata content"

        # First upload
        info1, _ = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(content),
            uploaded_by="original-uploader",
            source_uri="original-source",
        )

        # Duplicate upload with different metadata
        info2, is_dup = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(content),
            uploaded_by="new-uploader",
            source_uri="new-source",
        )

        assert is_dup is True
        # Original metadata should be preserved
        assert info2.uploaded_by == "original-uploader"
        assert info2.source_uri == "original-source"


class TestArtifactIsolation:
    """Tests for artifact path isolation."""

    def test_different_artifact_paths_isolated(self, storage_service: StorageService) -> None:
        """Different artifact paths should not interfere with each other."""
        content = b"shared content"

        # Store in path A
        info_a, is_dup_a = storage_service.store_artifact(
            artifact_path="project-a/artifact",
            file_stream=io.BytesIO(content),
            uploaded_by="user-a",
        )

        # Store same content in path B (should not be duplicate)
        info_b, is_dup_b = storage_service.store_artifact(
            artifact_path="project-b/artifact",
            file_stream=io.BytesIO(content),
            uploaded_by="user-b",
        )

        # Both should be new (different artifact paths)
        assert is_dup_a is False
        assert is_dup_b is False

        # Each path should have its own artifacts
        list_a = storage_service.list_artifacts("project-a/artifact")
        list_b = storage_service.list_artifacts("project-b/artifact")

        assert len(list_a) == 1
        assert len(list_b) == 1
        assert list_a[0].uploaded_by == "user-a"
        assert list_b[0].uploaded_by == "user-b"


class TestTagResolution:
    """Tests for tag and hash reference resolution."""

    def test_get_by_tag_name(self, storage_service: StorageService) -> None:
        """get_artifact_info should resolve tag names."""
        artifact_path = "test/tags"
        storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )

        info = storage_service.get_artifact_info(artifact_path, "latest")
        assert info is not None
        assert "latest" in info.tags

    def test_get_by_hash_ref(self, storage_service: StorageService) -> None:
        """get_artifact_info should resolve hash references."""
        artifact_path = "test/hash-ref"
        stored_info, _ = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )

        info = storage_service.get_artifact_info(artifact_path, stored_info.hash_ref)
        assert info.hash == stored_info.hash

    def test_get_nonexistent_tag_raises(self, storage_service: StorageService) -> None:
        """get_artifact_info should raise for non-existent tag."""
        artifact_path = "test/nonexistent-tag"
        storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )

        with pytest.raises(ArtifactNotFoundError, match="Tag.*not found"):
            storage_service.get_artifact_info(artifact_path, "nonexistent")

    def test_get_nonexistent_path_raises(self, storage_service: StorageService) -> None:
        """get_artifact_info should raise for non-existent artifact path."""
        with pytest.raises(ArtifactNotFoundError, match="Artifact path not found"):
            storage_service.get_artifact_info("nonexistent/path", "latest")


class TestListArtifacts:
    """Tests for list_artifacts functionality."""

    def test_list_returns_all_versions(self, storage_service: StorageService) -> None:
        """list_artifacts should return all unique blobs with their tags."""
        artifact_path = "test/versions"

        # Store first version
        info1, _ = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"version 1"),
            uploaded_by="user",
        )

        # Store second version (different content)
        info2, _ = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"version 2"),
            uploaded_by="user",
        )

        # List should return both versions
        artifacts = storage_service.list_artifacts(artifact_path)

        # Should have 2 unique blobs (v1 untagged, v2 has latest)
        assert len(artifacts) == 2

        # Find each version by hash
        artifacts_by_hash = {a.hash: a for a in artifacts}

        # v1 should exist but have no tags (empty list)
        assert info1.hash in artifacts_by_hash
        assert artifacts_by_hash[info1.hash].tags == []

        # v2 should have "latest" tag
        assert info2.hash in artifacts_by_hash
        assert "latest" in artifacts_by_hash[info2.hash].tags

    def test_list_empty_path_returns_empty(self, storage_service: StorageService) -> None:
        """list_artifacts should return empty list for non-existent path."""
        artifacts = storage_service.list_artifacts("nonexistent/path")
        assert artifacts == []

    def test_list_groups_tags_by_hash(self, storage_service: StorageService) -> None:
        """list_artifacts should group multiple tags pointing to same blob."""
        artifact_path = "test/multi-tag"

        # Store artifact - gets "latest" tag automatically
        info, _ = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )

        # Manually add another tag pointing to same hash (use full hash)
        from magpie.storage.manifest import update_tag
        from magpie.storage.paths import artifact_dir_path
        from magpie.storage.symlinks import reconcile_symlinks

        artifact_dir = artifact_dir_path(storage_service.config.storage_path, artifact_path)
        manifest = update_tag(artifact_dir, "v1.0", info.hash)  # Use full hash
        reconcile_symlinks(artifact_dir, manifest)

        # List should show one blob with both tags
        artifacts = storage_service.list_artifacts(artifact_path)
        assert len(artifacts) == 1
        assert "latest" in artifacts[0].tags
        assert "v1.0" in artifacts[0].tags

    def test_list_shows_untagged_blobs(self, storage_service: StorageService) -> None:
        """list_artifacts should show blobs that have lost all tags."""
        artifact_path = "test/untagged"

        # Store first version (gets "latest" tag)
        info1, _ = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"version 1"),
            uploaded_by="user1",
        )

        # Store second version (steals "latest" tag, v1 becomes untagged)
        info2, _ = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"version 2"),
            uploaded_by="user2",
        )

        # Both versions should appear in list
        artifacts = storage_service.list_artifacts(artifact_path)
        assert len(artifacts) == 2

        # Verify v1 has empty tags list (not missing, just empty)
        v1 = next(a for a in artifacts if a.hash == info1.hash)
        assert v1.tags == []
        assert v1.uploaded_by == "user1"

        # Verify v2 has latest tag
        v2 = next(a for a in artifacts if a.hash == info2.hash)
        assert v2.tags == ["latest"]
        assert v2.uploaded_by == "user2"

    def test_list_after_untag_shows_orphaned_blob(self, storage_service: StorageService) -> None:
        """list_artifacts should show blobs after their only tag is removed."""
        artifact_path = "test/untag-orphan"

        # Store artifact (gets "latest" tag)
        info, _ = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )

        # Remove the only tag
        storage_service.remove_tag(artifact_path, "latest")

        # Blob should still appear in list with empty tags
        artifacts = storage_service.list_artifacts(artifact_path)
        assert len(artifacts) == 1
        assert artifacts[0].hash == info.hash
        assert artifacts[0].tags == []


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_store_without_source_uri(self, storage_service: StorageService) -> None:
        """store_artifact should work without source_uri."""
        info, _ = storage_service.store_artifact(
            artifact_path="test/no-source",
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )

        assert info.source_uri is None

    def test_store_creates_directories(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """store_artifact should create artifact directory structure."""
        artifact_path = "deep/nested/path/artifact"

        storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )

        expected_dir = test_config.storage_path / artifact_path
        assert expected_dir.exists()
        assert (expected_dir / "blobs").exists()

    def test_symlinks_created(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """store_artifact should create symlinks for tags."""
        artifact_path = "test/symlinks"

        storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )

        artifact_dir = test_config.storage_path / artifact_path
        symlink_path = artifact_dir / "latest"

        assert symlink_path.is_symlink()
        # Symlink should resolve to blob content
        assert symlink_path.read_bytes() == b"content"


class TestListArtifactPaths:
    """Tests for list_artifact_paths functionality."""

    def test_list_all_paths(self, storage_service: StorageService) -> None:
        """list_artifact_paths should return all artifact paths recursively."""
        # Store artifacts at different paths
        storage_service.store_artifact(
            artifact_path="test/artifact1",
            file_stream=io.BytesIO(b"content1"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="test/artifact2",
            file_stream=io.BytesIO(b"content2"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="images/ubuntu",
            file_stream=io.BytesIO(b"content3"),
            uploaded_by="user",
        )

        # List all paths recursively
        paths = storage_service.list_artifact_paths(recursive=True)

        assert len(paths) == 3
        assert "test/artifact1" in paths
        assert "test/artifact2" in paths
        assert "images/ubuntu" in paths
        # Should be sorted
        assert paths == sorted(paths)

    def test_list_paths_with_prefix(self, storage_service: StorageService) -> None:
        """list_artifact_paths should filter by prefix."""
        # Store artifacts at different paths
        storage_service.store_artifact(
            artifact_path="test/artifact1",
            file_stream=io.BytesIO(b"content1"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="test/artifact2",
            file_stream=io.BytesIO(b"content2"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="images/ubuntu",
            file_stream=io.BytesIO(b"content3"),
            uploaded_by="user",
        )

        # List with prefix
        paths = storage_service.list_artifact_paths("test")

        assert len(paths) == 2
        assert "test/artifact1" in paths
        assert "test/artifact2" in paths
        assert "images/ubuntu" not in paths

    def test_list_paths_normalizes_leading_slash(self, storage_service: StorageService) -> None:
        """list_artifact_paths should normalize leading slashes."""
        storage_service.store_artifact(
            artifact_path="test/artifact",
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )

        # Leading slash should be normalized
        paths = storage_service.list_artifact_paths("/test")

        assert len(paths) == 1
        assert "test/artifact" in paths

    def test_list_paths_slash_only_lists_all(self, storage_service: StorageService) -> None:
        """list_artifact_paths should treat slash-only prefix as root listing."""
        storage_service.store_artifact(
            artifact_path="test/artifact1",
            file_stream=io.BytesIO(b"content1"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="other/artifact2",
            file_stream=io.BytesIO(b"content2"),
            uploaded_by="user",
        )

        # Single slash should list all artifacts recursively (normalized to empty prefix)
        paths = storage_service.list_artifact_paths("/", recursive=True)
        assert len(paths) == 2
        assert "test/artifact1" in paths
        assert "other/artifact2" in paths

        # Multiple slashes should also work
        paths = storage_service.list_artifact_paths("//", recursive=True)
        assert len(paths) == 2
        assert "test/artifact1" in paths
        assert "other/artifact2" in paths

    def test_list_paths_empty_returns_empty(self, storage_service: StorageService) -> None:
        """list_artifact_paths should return empty list when no artifacts.

        Note: May show .tmp/ as a virtual directory if temp directory exists.
        """
        paths = storage_service.list_artifact_paths()
        # Filter out .tmp/ which may exist as a system directory
        paths = [p for p in paths if not p.startswith(".tmp")]
        assert paths == []

    def test_list_paths_no_match_returns_empty(self, storage_service: StorageService) -> None:
        """list_artifact_paths should return empty list for non-matching prefix."""
        storage_service.store_artifact(
            artifact_path="test/artifact",
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )

        paths = storage_service.list_artifact_paths("images")
        assert paths == []

    def test_list_paths_prefix_does_not_match_siblings(
        self, storage_service: StorageService
    ) -> None:
        """list_artifact_paths prefix filter should not match sibling paths.

        Regression test: prefix="test" should not match "test2/artifact".
        """
        # Store artifacts with similar prefixes
        storage_service.store_artifact(
            artifact_path="test/artifact",
            file_stream=io.BytesIO(b"content1"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="test2/artifact",
            file_stream=io.BytesIO(b"content2"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="testing/artifact",
            file_stream=io.BytesIO(b"content3"),
            uploaded_by="user",
        )

        # List with prefix "test" should only match "test/artifact"
        paths = storage_service.list_artifact_paths("test")
        assert len(paths) == 1
        assert "test/artifact" in paths
        assert "test2/artifact" not in paths
        assert "testing/artifact" not in paths

    def test_list_paths_prefix_exact_match(self, storage_service: StorageService) -> None:
        """list_artifact_paths should match artifact paths that exactly equal the prefix."""
        # Store an artifact at the exact prefix path
        storage_service.store_artifact(
            artifact_path="myproject",
            file_stream=io.BytesIO(b"content"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="other/artifact",
            file_stream=io.BytesIO(b"content2"),
            uploaded_by="user",
        )

        # List with prefix "myproject" should only match the exact path
        paths = storage_service.list_artifact_paths("myproject")
        assert len(paths) == 1
        assert "myproject" in paths
        assert "other/artifact" not in paths

    def test_list_paths_recursive_prefix_siblings(self, storage_service: StorageService) -> None:
        """list_artifact_paths with recursive=True should not match sibling prefixes."""
        # Store artifacts with similar prefixes
        storage_service.store_artifact(
            artifact_path="test/artifact",
            file_stream=io.BytesIO(b"content1"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="test/sub/deep",
            file_stream=io.BytesIO(b"content2"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="test2/artifact",
            file_stream=io.BytesIO(b"content3"),
            uploaded_by="user",
        )

        # Recursive list with prefix "test" should only match "test/*"
        paths = storage_service.list_artifact_paths("test", recursive=True)
        assert len(paths) == 2
        assert "test/artifact" in paths
        assert "test/sub/deep" in paths
        assert "test2/artifact" not in paths

    def test_list_paths_non_recursive_single_segment(self, storage_service: StorageService) -> None:
        """Non-recursive list should find single-segment artifacts and virtual directories.

        Regression test for issue #329: glob("*/*/.magpie") silently hid
        single-segment artifacts like "simple".

        Updated for virtual directory support: non-recursive mode now also returns
        virtual directory indicators (directories containing artifacts deeper).
        """
        # Store single-segment artifact
        storage_service.store_artifact(
            artifact_path="simple",
            file_stream=io.BytesIO(b"content1"),
            uploaded_by="user",
        )
        # Store multi-segment for comparison
        storage_service.store_artifact(
            artifact_path="ns/artifact",
            file_stream=io.BytesIO(b"content2"),
            uploaded_by="user",
        )

        # Non-recursive list with no prefix should return:
        # - "simple" (immediate child artifact)
        # - "ns/" (virtual directory indicator)
        # - May also include ".tmp/" if temp directory exists
        paths = storage_service.list_artifact_paths(recursive=False)
        paths = [p for p in paths if not p.startswith(".tmp")]  # Filter system directories
        assert len(paths) == 2
        assert "simple" in paths
        assert "ns/" in paths
        assert "ns/artifact" not in paths

        # Non-recursive list with prefix "ns" should find "ns/artifact"
        paths = storage_service.list_artifact_paths(prefix="ns", recursive=False)
        assert len(paths) == 1
        assert "ns/artifact" in paths

    def test_list_paths_non_recursive_deeply_nested(self, storage_service: StorageService) -> None:
        """Non-recursive list returns immediate children and virtual directory indicators.

        Tests that non-recursive listing returns:
        1. Artifacts that are immediate children of the prefix directory
        2. Virtual directory indicators (with trailing /) for subdirectories
           containing artifacts deeper down

        Nested artifacts in subdirectories are not returned directly, but can be
        discovered by listing those subdirectories.
        """
        # Create deeply nested artifacts
        storage_service.store_artifact(
            artifact_path="a/b/c/d",
            file_stream=io.BytesIO(b"deep"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="a/b/c/e",
            file_stream=io.BytesIO(b"deep2"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="a/b/c/sub/deeper",
            file_stream=io.BytesIO(b"deeper"),
            uploaded_by="user",
        )

        # Non-recursive at "a/b/c" should return:
        # - immediate children artifacts (d, e)
        # - virtual directory indicator for "sub/" (contains deeper artifact)
        paths = storage_service.list_artifact_paths(prefix="a/b/c", recursive=False)
        assert len(paths) == 3
        assert "a/b/c/d" in paths
        assert "a/b/c/e" in paths
        assert "a/b/c/sub/" in paths
        assert "a/b/c/sub/deeper" not in paths

    def test_list_paths_non_recursive_various_depths(self, storage_service: StorageService) -> None:
        """Non-recursive mode returns immediate children and virtual directory indicators."""
        # Create artifacts at various depths
        storage_service.store_artifact(
            artifact_path="root",
            file_stream=io.BytesIO(b"r"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="test/one",
            file_stream=io.BytesIO(b"t1"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="test/two",
            file_stream=io.BytesIO(b"t2"),
            uploaded_by="user",
        )
        storage_service.store_artifact(
            artifact_path="test/sub/deep",
            file_stream=io.BytesIO(b"td"),
            uploaded_by="user",
        )

        # No prefix: returns immediate children and virtual directory indicators
        # - "root" (immediate child artifact)
        # - "test/" (virtual directory indicator - contains artifacts deeper)
        # - May also include ".tmp/" if temp directory exists
        paths = storage_service.list_artifact_paths(recursive=False)
        paths = [p for p in paths if not p.startswith(".tmp")]  # Filter system directories
        assert len(paths) == 2
        assert "root" in paths
        assert "test/" in paths
        assert "test/one" not in paths
        assert "test/two" not in paths
        assert "test/sub/deep" not in paths

        # Prefix "test": returns immediate children and virtual directory indicators
        # - "test/one", "test/two" (immediate child artifacts)
        # - "test/sub/" (virtual directory indicator - contains "deep")
        paths = storage_service.list_artifact_paths(prefix="test", recursive=False)
        assert len(paths) == 3
        assert "test/one" in paths
        assert "test/two" in paths
        assert "test/sub/" in paths
        assert "test/sub/deep" not in paths

        # Prefix "test/sub": list immediate children of test/sub/ (deep)
        paths = storage_service.list_artifact_paths(prefix="test/sub", recursive=False)
        assert len(paths) == 1
        assert "test/sub/deep" in paths

    def test_list_paths_non_recursive_returns_only_immediate_children(
        self, storage_service: StorageService
    ) -> None:
        """Non-recursive list returns immediate children and virtual directory indicators.

        Non-recursive mode returns:
        1. Immediate child artifacts (directories with .magpie)
        2. Virtual directory indicators (directories containing artifacts deeper,
           marked with trailing /)

        This enables navigation to discover deeply nested artifacts without full
        tree traversal. The storage layer is O(immediate_children) efficient because
        it does not perform recursive existence checks; all subdirectories are treated
        as virtual directories unconditionally.

        For issue #398 use case (discovering deeply nested artifacts):
        Users can navigate step-by-step through virtual directories.

        Expected behavior:
        - ls (no prefix) -> returns ["builds/"] (virtual dir indicator)
        - ls builds -> returns ["builds/infra/"] (virtual dir indicator)
        - ls builds/infra -> returns ["builds/infra/github-runner.qcow2"] (artifact)
        - ls -r (recursive) -> returns ["builds/infra/github-runner.qcow2"] (all artifacts)
        """
        # Create artifact at deep nesting level only
        storage_service.store_artifact(
            artifact_path="builds/infra/github-runner.qcow2",
            file_stream=io.BytesIO(b"deep artifact"),
            uploaded_by="user",
        )

        # Root level non-recursive: returns virtual directory indicator for "builds/"
        # May also include ".tmp/" if temp directory exists
        paths = storage_service.list_artifact_paths(recursive=False)
        paths = [p for p in paths if not p.startswith(".tmp")]  # Filter system directories
        assert len(paths) == 1
        assert "builds/" in paths, (
            "Non-recursive list at root should return ['builds/'] virtual dir indicator"
        )

        # Prefix "builds" non-recursive: returns virtual directory indicator for "infra/"
        paths = storage_service.list_artifact_paths(prefix="builds", recursive=False)
        assert len(paths) == 1
        assert "builds/infra/" in paths, (
            "Non-recursive list at 'builds' should return ['builds/infra/'] virtual dir indicator"
        )

        # Prefix "builds/infra" non-recursive: finds the immediate child artifact
        paths = storage_service.list_artifact_paths(prefix="builds/infra", recursive=False)
        assert len(paths) == 1
        assert "builds/infra/github-runner.qcow2" in paths

        # Recursive mode from root: finds all artifacts regardless of depth
        paths = storage_service.list_artifact_paths(recursive=True)
        assert len(paths) == 1
        assert "builds/infra/github-runner.qcow2" in paths

        # Recursive mode from "builds": finds all artifacts under builds/
        paths = storage_service.list_artifact_paths(prefix="builds", recursive=True)
        assert len(paths) == 1
        assert "builds/infra/github-runner.qcow2" in paths


class TestLegacyHashNameLayout:
    """Artifacts written under the pre-widening 8-char layout (issue #529).

    Simulates an install upgraded in place: blob and metadata files keep
    their old names and must stay fully usable.
    """

    @staticmethod
    def _plant_legacy_artifact(
        storage_service: StorageService, artifact_path: str, content: bytes
    ) -> str:
        """Store an artifact, then rename its files to the legacy 8-char width."""
        info, _ = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(content),
            uploaded_by="old-release",
        )
        artifact_dir = artifact_dir_path(storage_service.config.storage_path, artifact_path)
        current = info.hash[:HASH_NAME_LENGTH]
        legacy = info.hash[:8]
        (artifact_dir / "blobs" / current).rename(artifact_dir / "blobs" / legacy)
        (artifact_dir / "metadata" / f"{current}.json").rename(
            artifact_dir / "metadata" / f"{legacy}.json"
        )
        latest = artifact_dir / "latest"
        latest.unlink()
        latest.symlink_to(Path("blobs") / legacy)
        return info.hash

    def test_legacy_artifact_resolves_by_tag_and_hash_ref(
        self, storage_service: StorageService
    ) -> None:
        """Tag, legacy ref, and current-width ref all resolve to the legacy files."""
        artifact_path = "legacy/resolve"
        content = b"stored before the hash-name widening"
        full_hash = self._plant_legacy_artifact(storage_service, artifact_path, content)

        by_tag = storage_service.get_artifact_info(artifact_path, "latest")
        assert by_tag.hash == full_hash

        by_legacy_ref = storage_service.get_artifact_info(artifact_path, f"@{full_hash[:8]}")
        assert by_legacy_ref.hash == full_hash

        by_current_ref = storage_service.get_artifact_info(
            artifact_path, f"@{full_hash[:HASH_NAME_LENGTH]}"
        )
        assert by_current_ref.hash == full_hash

        listed = storage_service.list_artifacts(artifact_path)
        assert [a.hash for a in listed] == [full_hash]

    def test_reupload_of_legacy_artifact_is_a_duplicate(
        self, storage_service: StorageService
    ) -> None:
        """Re-uploading legacy content doesn't store a second copy under the new name."""
        artifact_path = "legacy/duplicate"
        content = b"stored before the hash-name widening"
        full_hash = self._plant_legacy_artifact(storage_service, artifact_path, content)

        info, is_duplicate = storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=io.BytesIO(content),
            uploaded_by="new-release",
        )

        assert is_duplicate is True
        assert info.hash == full_hash
        assert info.hash_ref == f"@{full_hash[:8]}"
        artifact_dir = artifact_dir_path(storage_service.config.storage_path, artifact_path)
        assert {p.name for p in (artifact_dir / "blobs").iterdir()} == {full_hash[:8]}

    def test_reported_hash_ref_names_the_file_on_disk(
        self, storage_service: StorageService
    ) -> None:
        """Refs double as download locators, so they must match the stored name."""
        artifact_path = "legacy/hash-ref"
        content = b"stored before the hash-name widening"
        full_hash = self._plant_legacy_artifact(storage_service, artifact_path, content)
        legacy_ref = f"@{full_hash[:8]}"

        assert storage_service.get_artifact_info(artifact_path, "latest").hash_ref == legacy_ref
        assert storage_service.get_artifact_info(artifact_path, legacy_ref).hash_ref == legacy_ref
        assert (
            storage_service.get_artifact_info(
                artifact_path, f"@{full_hash[:HASH_NAME_LENGTH]}"
            ).hash_ref
            == legacy_ref
        )
        assert [a.hash_ref for a in storage_service.list_artifacts(artifact_path)] == [legacy_ref]
        assert storage_service.create_tag(artifact_path, legacy_ref, "stable").hash_ref == (
            legacy_ref
        )
        assert storage_service.amend_metadata(
            artifact_path, legacy_ref, source_uri="https://example.test/x"
        ).hash_ref == (legacy_ref)

    def test_new_artifact_reports_current_width_ref(self, storage_service: StorageService) -> None:
        """Artifacts written by this release keep the widened ref."""
        info, _ = storage_service.store_artifact(
            artifact_path="current/hash-ref",
            file_stream=io.BytesIO(b"written after the widening"),
            uploaded_by="new-release",
        )

        assert info.hash_ref == f"@{info.hash[:HASH_NAME_LENGTH]}"
