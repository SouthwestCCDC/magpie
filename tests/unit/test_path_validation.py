"""Unit tests for artifact path validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.paths import (
    check_artifact_nesting,
    normalize_artifact_path,
    validate_artifact_path,
    verify_path_is_descendant,
)


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
        with pytest.raises(InvalidArtifactPathError, match="'blobs' is a reserved name"):
            validate_artifact_path("test/artifact/blobs")

    def test_reject_reserved_metadata(self) -> None:
        """Reject path containing 'metadata' segment."""
        with pytest.raises(InvalidArtifactPathError, match="'metadata' is a reserved name"):
            validate_artifact_path("test/metadata/file")

    def test_reject_reserved_magpie(self) -> None:
        """Reject path containing '.magpie' segment."""
        with pytest.raises(InvalidArtifactPathError, match="'.magpie' is a reserved name"):
            validate_artifact_path("test/.magpie")

    def test_reject_hidden_segment(self) -> None:
        """Reject path with segment starting with dot."""
        with pytest.raises(InvalidArtifactPathError, match="Path segments cannot start with '.'"):
            validate_artifact_path("test/.hidden/file")

    def test_reject_empty_path(self) -> None:
        """Reject empty path."""
        with pytest.raises(InvalidArtifactPathError, match="Artifact path cannot be empty"):
            validate_artifact_path("")

    def test_reject_whitespace_only(self) -> None:
        """Reject whitespace-only path."""
        with pytest.raises(InvalidArtifactPathError, match="Artifact path cannot be empty"):
            validate_artifact_path("   ")

    def test_reject_empty_segment(self) -> None:
        """Reject path with empty segment."""
        with pytest.raises(InvalidArtifactPathError, match="cannot contain empty segments"):
            validate_artifact_path("test//artifact")

    def test_reserved_at_start(self) -> None:
        """Reject reserved name at start of path."""
        with pytest.raises(InvalidArtifactPathError, match="'blobs' is a reserved name"):
            validate_artifact_path("blobs/something")

    def test_reserved_in_middle(self) -> None:
        """Reject reserved name in middle of path."""
        with pytest.raises(InvalidArtifactPathError, match="'metadata' is a reserved name"):
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


class TestNormalizeArtifactPath:
    """Tests for normalize_artifact_path function."""

    def test_simple_path_unchanged(self) -> None:
        """Simple path without special characters is unchanged."""
        assert normalize_artifact_path("test/artifact") == "test/artifact"

    def test_single_component_path(self) -> None:
        """Single component path is unchanged."""
        assert normalize_artifact_path("artifact") == "artifact"

    def test_strip_leading_slash(self) -> None:
        """Leading slash is stripped."""
        assert normalize_artifact_path("/test/artifact") == "test/artifact"

    def test_strip_trailing_slash(self) -> None:
        """Trailing slash is stripped."""
        assert normalize_artifact_path("test/artifact/") == "test/artifact"

    def test_strip_both_slashes(self) -> None:
        """Both leading and trailing slashes are stripped."""
        assert normalize_artifact_path("/test/artifact/") == "test/artifact"

    def test_collapse_multiple_slashes(self) -> None:
        """Multiple consecutive slashes are collapsed to single slash."""
        assert normalize_artifact_path("test//artifact") == "test/artifact"

    def test_collapse_many_slashes(self) -> None:
        """Many consecutive slashes are collapsed."""
        assert normalize_artifact_path("test////artifact") == "test/artifact"

    def test_combined_normalization(self) -> None:
        """All normalizations applied together."""
        assert normalize_artifact_path("//test//artifact//") == "test/artifact"

    def test_complex_path_normalization(self) -> None:
        """Complex path with multiple issues is normalized."""
        assert normalize_artifact_path("///foo///bar///baz///") == "foo/bar/baz"

    def test_reject_path_traversal_double_dot(self) -> None:
        """Path traversal with .. is rejected."""
        with pytest.raises(InvalidArtifactPathError, match="Path traversal.*not allowed"):
            normalize_artifact_path("test/../other")

    def test_reject_path_traversal_at_start(self) -> None:
        """Path traversal at start is rejected."""
        with pytest.raises(InvalidArtifactPathError, match="Path traversal.*not allowed"):
            normalize_artifact_path("../test")

    def test_reject_path_traversal_at_end(self) -> None:
        """Path traversal at end is rejected."""
        with pytest.raises(InvalidArtifactPathError, match="Path traversal.*not allowed"):
            normalize_artifact_path("test/..")

    def test_reject_only_double_dot(self) -> None:
        """Just .. is rejected."""
        with pytest.raises(InvalidArtifactPathError, match="Path traversal.*not allowed"):
            normalize_artifact_path("..")

    def test_reject_empty_path(self) -> None:
        """Empty path is rejected."""
        with pytest.raises(InvalidArtifactPathError, match="cannot be empty"):
            normalize_artifact_path("")

    def test_reject_only_slashes(self) -> None:
        """Path with only slashes is rejected (becomes empty after strip)."""
        with pytest.raises(InvalidArtifactPathError, match="cannot be empty"):
            normalize_artifact_path("///")

    def test_reject_only_leading_slash(self) -> None:
        """Path with only leading slash is rejected."""
        with pytest.raises(InvalidArtifactPathError, match="cannot be empty"):
            normalize_artifact_path("/")

    def test_deeply_nested_path(self) -> None:
        """Deeply nested path is normalized correctly."""
        assert normalize_artifact_path("/a/b/c/d/e/") == "a/b/c/d/e"

    def test_path_with_dots_allowed(self) -> None:
        """Single dots in path components are allowed (not traversal)."""
        # This tests that single dots within names are ok (e.g., version numbers)
        # The ".." traversal is blocked, but "v1.0" or "file.tar" should work
        # However, note that validate_artifact_path blocks segments starting with "."
        # The normalize function only checks for ".." traversal
        assert normalize_artifact_path("v1.0/artifact") == "v1.0/artifact"

    def test_double_dot_in_filename_allowed(self) -> None:
        """Double dots within filenames are allowed (not path traversal).

        This tests that segment-based validation correctly allows paths like
        "v1..2" or "test..file" which contain ".." as part of a filename
        but are not path traversal attempts.
        """
        assert normalize_artifact_path("v1..2/artifact") == "v1..2/artifact"
        assert normalize_artifact_path("test..file") == "test..file"
        assert normalize_artifact_path("project/name..ext") == "project/name..ext"

    def test_double_dot_as_segment_rejected(self) -> None:
        """Double dots as a complete path segment are rejected."""
        with pytest.raises(InvalidArtifactPathError, match="Path traversal.*not allowed"):
            normalize_artifact_path("test/../other")
        with pytest.raises(InvalidArtifactPathError, match="Path traversal.*not allowed"):
            normalize_artifact_path("../test")
        with pytest.raises(InvalidArtifactPathError, match="Path traversal.*not allowed"):
            normalize_artifact_path("test/..")


class TestVerifyPathIsDescendant:
    """Tests for verify_path_is_descendant function."""

    def test_valid_simple_descendant(self, tmp_path: Path) -> None:
        """Valid simple path is a descendant of base."""
        result = verify_path_is_descendant(tmp_path, "artifact")
        assert result == tmp_path / "artifact"

    def test_valid_nested_descendant(self, tmp_path: Path) -> None:
        """Valid nested path is a descendant of base."""
        result = verify_path_is_descendant(tmp_path, "project/component/artifact")
        assert result == tmp_path / "project" / "component" / "artifact"

    def test_reject_traversal_escape(self, tmp_path: Path) -> None:
        """Path traversal that escapes base is rejected."""
        with pytest.raises(InvalidArtifactPathError, match="resolves outside"):
            verify_path_is_descendant(tmp_path, "../escape")

    def test_reject_complex_traversal(self, tmp_path: Path) -> None:
        """Complex path traversal that escapes base is rejected."""
        with pytest.raises(InvalidArtifactPathError, match="resolves outside"):
            verify_path_is_descendant(tmp_path, "project/../../escape")

    def test_reject_root_path(self, tmp_path: Path) -> None:
        """Path that resolves to base directory itself is rejected."""
        with pytest.raises(InvalidArtifactPathError, match="resolve to storage root"):
            verify_path_is_descendant(tmp_path, ".")

    def test_double_dot_in_filename_allowed(self, tmp_path: Path) -> None:
        """Double dots within filenames are allowed (not traversal)."""
        result = verify_path_is_descendant(tmp_path, "v1..2/artifact")
        assert result == tmp_path / "v1..2" / "artifact"

    def test_path_stays_within_base(self, tmp_path: Path) -> None:
        """Path that goes up then down but stays in base is valid."""
        # Note: This tests that "project/../other" resolves to "other" which is valid
        # The segment-based check in normalize_artifact_path would catch ".."
        # but this test verifies the resolution behavior
        result = verify_path_is_descendant(tmp_path, "project/../other")
        assert result == tmp_path / "other"
