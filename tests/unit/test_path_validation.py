"""Unit tests for artifact path validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.paths import check_artifact_nesting, validate_artifact_path


class TestValidateArtifactPath:
    """Tests for validate_artifact_path function."""

    def test_valid_simple_path(self) -> None:
        """Valid simple path passes validation."""
        validate_artifact_path("myartifact")

    def test_valid_nested_path(self) -> None:
        """Valid nested path passes validation."""
        validate_artifact_path("project/component/artifact")

    def test_valid_deep_nesting(self) -> None:
        """Valid deeply nested path passes validation."""
        validate_artifact_path("org/team/project/component/subcomponent/artifact")

    def test_reject_reserved_blobs(self) -> None:
        """Reject path containing 'blobs' segment."""
        with pytest.raises(
            InvalidArtifactPathError, match="'blobs' is a reserved name"
        ):
            validate_artifact_path("test/artifact/blobs")

    def test_reject_reserved_metadata(self) -> None:
        """Reject path containing 'metadata' segment."""
        with pytest.raises(
            InvalidArtifactPathError, match="'metadata' is a reserved name"
        ):
            validate_artifact_path("test/metadata/file")

    def test_reject_reserved_magpie(self) -> None:
        """Reject path containing '.magpie' segment."""
        with pytest.raises(
            InvalidArtifactPathError, match="'.magpie' is a reserved name"
        ):
            validate_artifact_path("test/.magpie")

    def test_reject_hidden_segment(self) -> None:
        """Reject path with segment starting with dot."""
        with pytest.raises(
            InvalidArtifactPathError, match="Path segments cannot start with '.'"
        ):
            validate_artifact_path("test/.hidden/file")

    def test_reject_empty_path(self) -> None:
        """Reject empty path."""
        with pytest.raises(
            InvalidArtifactPathError, match="Artifact path cannot be empty"
        ):
            validate_artifact_path("")

    def test_reject_whitespace_only(self) -> None:
        """Reject whitespace-only path."""
        with pytest.raises(
            InvalidArtifactPathError, match="Artifact path cannot be empty"
        ):
            validate_artifact_path("   ")

    def test_reject_empty_segment(self) -> None:
        """Reject path with empty segment."""
        with pytest.raises(
            InvalidArtifactPathError, match="cannot contain empty segments"
        ):
            validate_artifact_path("test//artifact")

    def test_reserved_at_start(self) -> None:
        """Reject reserved name at start of path."""
        with pytest.raises(
            InvalidArtifactPathError, match="'blobs' is a reserved name"
        ):
            validate_artifact_path("blobs/something")

    def test_reserved_in_middle(self) -> None:
        """Reject reserved name in middle of path."""
        with pytest.raises(
            InvalidArtifactPathError, match="'metadata' is a reserved name"
        ):
            validate_artifact_path("project/metadata/artifact")


class TestCheckArtifactNesting:
    """Tests for check_artifact_nesting function."""

    def test_no_conflict_new_artifact(self, tmp_path: Path) -> None:
        """No conflict when creating first artifact."""
        check_artifact_nesting(tmp_path, "project/artifact")

    def test_no_conflict_sibling_artifacts(self, tmp_path: Path) -> None:
        """No conflict between sibling artifacts."""
        # Create first artifact
        artifact1 = tmp_path / "project" / "artifact1"
        artifact1.mkdir(parents=True)
        (artifact1 / ".magpie").write_text("{}")

        # Check sibling doesn't conflict
        check_artifact_nesting(tmp_path, "project/artifact2")

    def test_no_conflict_different_branches(self, tmp_path: Path) -> None:
        """No conflict between artifacts in different directories."""
        # Create artifact in one directory
        artifact1 = tmp_path / "project1" / "artifact"
        artifact1.mkdir(parents=True)
        (artifact1 / ".magpie").write_text("{}")

        # Check artifact in different directory doesn't conflict
        check_artifact_nesting(tmp_path, "project2/artifact")

    def test_reject_child_of_existing(self, tmp_path: Path) -> None:
        """Reject creating artifact as child of existing artifact."""
        # Create parent artifact
        parent = tmp_path / "test" / "myartifact"
        parent.mkdir(parents=True)
        (parent / ".magpie").write_text("{}")

        # Try to create child artifact
        with pytest.raises(
            InvalidArtifactPathError,
            match="would be nested under existing artifact 'test/myartifact'",
        ):
            check_artifact_nesting(tmp_path, "test/myartifact/nested")

    def test_reject_deep_child_of_existing(self, tmp_path: Path) -> None:
        """Reject creating artifact deeply nested under existing artifact."""
        # Create parent artifact
        parent = tmp_path / "project" / "component"
        parent.mkdir(parents=True)
        (parent / ".magpie").write_text("{}")

        # Try to create deeply nested child
        with pytest.raises(
            InvalidArtifactPathError,
            match="would be nested under existing artifact 'project/component'",
        ):
            check_artifact_nesting(tmp_path, "project/component/sub/nested/deep")

    def test_reject_parent_of_existing(self, tmp_path: Path) -> None:
        """Reject creating artifact as parent of existing artifact."""
        # Create child artifact first
        child = tmp_path / "test" / "myartifact" / "nested"
        child.mkdir(parents=True)
        (child / ".magpie").write_text("{}")

        # Try to create parent artifact
        with pytest.raises(
            InvalidArtifactPathError,
            match="existing artifact 'test/myartifact/nested' would be nested under it",
        ):
            check_artifact_nesting(tmp_path, "test/myartifact")

    def test_reject_grandparent_of_existing(self, tmp_path: Path) -> None:
        """Reject creating artifact as grandparent of existing artifact."""
        # Create deeply nested artifact
        nested = tmp_path / "a" / "b" / "c" / "artifact"
        nested.mkdir(parents=True)
        (nested / ".magpie").write_text("{}")

        # Try to create grandparent
        with pytest.raises(
            InvalidArtifactPathError,
            match="existing artifact 'a/b/c/artifact' would be nested under it",
        ):
            check_artifact_nesting(tmp_path, "a/b")

    def test_allow_same_path_reupload(self, tmp_path: Path) -> None:
        """Allow re-uploading to the same artifact path (not nesting)."""
        # Create artifact
        artifact = tmp_path / "test" / "artifact"
        artifact.mkdir(parents=True)
        (artifact / ".magpie").write_text("{}")

        # Should allow uploading to same path again
        check_artifact_nesting(tmp_path, "test/artifact")

    def test_complex_nesting_scenario(self, tmp_path: Path) -> None:
        """Test complex directory structure with multiple artifacts."""
        # Create multiple artifacts at different levels
        artifact1 = tmp_path / "org" / "team1" / "project1"
        artifact1.mkdir(parents=True)
        (artifact1 / ".magpie").write_text("{}")

        artifact2 = tmp_path / "org" / "team2" / "project2"
        artifact2.mkdir(parents=True)
        (artifact2 / ".magpie").write_text("{}")

        # Should reject parent of both
        with pytest.raises(InvalidArtifactPathError):
            check_artifact_nesting(tmp_path, "org")

        # Should allow sibling
        check_artifact_nesting(tmp_path, "org/team1/project2")
