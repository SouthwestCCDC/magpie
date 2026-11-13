"""Unit tests for artifact reference parsing."""

from __future__ import annotations

import pytest

from magpie.cli.commands.parse import (
    ArtifactRef,
    ParseError,
    parse_artifact_path,
    parse_artifact_ref,
)


class TestParseArtifactRef:
    """Tests for parse_artifact_ref function."""

    def test_parse_with_tag_ref(self) -> None:
        """Parse artifact reference with tag ref."""
        result = parse_artifact_ref("images/ubuntu:latest")
        assert result.path == "images/ubuntu"
        assert result.ref == "latest"

    def test_parse_with_hash_ref(self) -> None:
        """Parse artifact reference with hash ref."""
        result = parse_artifact_ref("images/ubuntu:@abc12345")
        assert result.path == "images/ubuntu"
        assert result.ref == "@abc12345"

    def test_parse_without_ref_defaults_to_latest(self) -> None:
        """Parse artifact reference without ref defaults to latest."""
        result = parse_artifact_ref("images/ubuntu")
        assert result.path == "images/ubuntu"
        assert result.ref == "latest"

    def test_parse_with_custom_default_ref(self) -> None:
        """Parse with custom default ref."""
        result = parse_artifact_ref("images/ubuntu", default_ref="stable")
        assert result.path == "images/ubuntu"
        assert result.ref == "stable"

    def test_parse_nested_path_with_ref(self) -> None:
        """Parse nested path with ref."""
        result = parse_artifact_ref("project/images/ubuntu:v1.0")
        assert result.path == "project/images/ubuntu"
        assert result.ref == "v1.0"

    def test_parse_deeply_nested_path(self) -> None:
        """Parse deeply nested path."""
        result = parse_artifact_ref("org/team/project/component:release-2.0")
        assert result.path == "org/team/project/component"
        assert result.ref == "release-2.0"

    def test_parse_simple_path(self) -> None:
        """Parse simple single-component path."""
        result = parse_artifact_ref("myartifact:v1")
        assert result.path == "myartifact"
        assert result.ref == "v1"

    def test_parse_path_with_dashes_and_underscores(self) -> None:
        """Parse path with dashes and underscores."""
        result = parse_artifact_ref("my-project/my_artifact:stable-release")
        assert result.path == "my-project/my_artifact"
        assert result.ref == "stable-release"

    def test_parse_path_with_dots(self) -> None:
        """Parse path with dots."""
        result = parse_artifact_ref("com.example/artifact.v2:1.0.0")
        assert result.path == "com.example/artifact.v2"
        assert result.ref == "1.0.0"


class TestParseArtifactRefErrors:
    """Tests for error handling in parse_artifact_ref."""

    def test_empty_reference_raises_error(self) -> None:
        """Empty reference raises ParseError."""
        with pytest.raises(ParseError, match="cannot be empty"):
            parse_artifact_ref("")

    def test_double_colon_raises_error(self) -> None:
        """Double colon raises ParseError with helpful message."""
        with pytest.raises(ParseError, match="Multiple colons"):
            parse_artifact_ref("images/ubuntu:latest:latest")

    def test_empty_path_raises_error(self) -> None:
        """Empty path (just :ref) raises ParseError."""
        with pytest.raises(ParseError, match="Empty path"):
            parse_artifact_ref(":latest")

    def test_empty_ref_after_colon_raises_error(self) -> None:
        """Empty ref after colon raises ParseError."""
        with pytest.raises(ParseError, match="Empty ref after colon"):
            parse_artifact_ref("images/ubuntu:")

    def test_double_slash_in_path_is_normalized(self) -> None:
        """Double slash in path is normalized (collapsed to single slash)."""
        # With path normalization, double slashes are collapsed rather than rejected
        result = parse_artifact_ref("images//ubuntu:latest")
        assert result.path == "images/ubuntu"
        assert result.ref == "latest"

    def test_invalid_path_component_starting_char(self) -> None:
        """Path component starting with non-alphanumeric raises error."""
        with pytest.raises(ParseError, match="Invalid path component"):
            parse_artifact_ref("-invalid/path:latest")

    def test_invalid_path_component_special_char(self) -> None:
        """Path component with special characters raises error."""
        with pytest.raises(ParseError, match="Invalid path component"):
            parse_artifact_ref("invalid@path/artifact:latest")

    def test_invalid_tag_name_starting_char(self) -> None:
        """Tag starting with non-alphanumeric raises error."""
        with pytest.raises(ParseError, match="Invalid tag name"):
            parse_artifact_ref("images/ubuntu:-invalid")

    def test_invalid_tag_name_special_char(self) -> None:
        """Tag with special characters raises error."""
        with pytest.raises(ParseError, match="Invalid tag name"):
            parse_artifact_ref("images/ubuntu:my@tag")


class TestParseArtifactRefHashRefs:
    """Tests for hash ref parsing."""

    def test_valid_hash_ref(self) -> None:
        """Valid 8-char hex hash ref parses correctly."""
        result = parse_artifact_ref("images/ubuntu:@a1b2c3d4")
        assert result.ref == "@a1b2c3d4"

    def test_valid_hash_ref_all_digits(self) -> None:
        """Hash ref with all digits parses correctly."""
        result = parse_artifact_ref("images/ubuntu:@12345678")
        assert result.ref == "@12345678"

    def test_valid_hash_ref_all_letters(self) -> None:
        """Hash ref with all hex letters parses correctly."""
        result = parse_artifact_ref("images/ubuntu:@abcdefab")
        assert result.ref == "@abcdefab"

    def test_hash_ref_too_short_raises_error(self) -> None:
        """Hash ref shorter than 8 chars raises error."""
        with pytest.raises(ParseError, match="at least 8 hex characters"):
            parse_artifact_ref("images/ubuntu:@abc123")

    def test_hash_ref_wider_than_legacy_prefix_accepted(self) -> None:
        """Refs wider than the legacy 8-char prefix are accepted."""
        result = parse_artifact_ref("images/ubuntu:@a1b2c3d4e5f67890")
        assert result.ref == "@a1b2c3d4e5f67890"

    def test_full_hash_ref_accepted(self) -> None:
        """A full 64-char digest is a valid hash ref."""
        full_hash = "a" * 64
        result = parse_artifact_ref(f"images/ubuntu:@{full_hash}")
        assert result.ref == f"@{full_hash}"

    def test_hash_ref_longer_than_digest_raises_error(self) -> None:
        """Hash ref longer than a SHA-256 digest raises error with suggestion."""
        with pytest.raises(ParseError, match="too long.*64 hex characters"):
            parse_artifact_ref(f"images/ubuntu:@{'a' * 65}")

    def test_hash_ref_invalid_chars_raises_error(self) -> None:
        """Hash ref with non-hex chars raises error."""
        with pytest.raises(ParseError, match="hex characters"):
            parse_artifact_ref("images/ubuntu:@abcdefgh")

    def test_hash_ref_uppercase_raises_error(self) -> None:
        """Hash ref with uppercase raises error (must be lowercase)."""
        with pytest.raises(ParseError, match="hex characters"):
            parse_artifact_ref("images/ubuntu:@ABCD1234")


class TestParseArtifactPath:
    """Tests for parse_artifact_path function."""

    def test_parse_simple_path(self) -> None:
        """Parse simple path."""
        path = parse_artifact_path("images/ubuntu")
        assert path == "images/ubuntu"

    def test_parse_path_strips_ref(self) -> None:
        """Parse path with ref strips the ref."""
        path = parse_artifact_path("images/ubuntu:latest")
        assert path == "images/ubuntu"

    def test_parse_path_strips_hash_ref(self) -> None:
        """Parse path with hash ref strips the ref."""
        path = parse_artifact_path("images/ubuntu:@abc12345")
        assert path == "images/ubuntu"

    def test_parse_nested_path(self) -> None:
        """Parse nested path."""
        path = parse_artifact_path("project/images/ubuntu")
        assert path == "project/images/ubuntu"

    def test_parse_nested_path_strips_ref(self) -> None:
        """Parse nested path with ref strips the ref."""
        path = parse_artifact_path("project/images/ubuntu:v1.0")
        assert path == "project/images/ubuntu"

    def test_empty_path_raises_error(self) -> None:
        """Empty path raises ParseError."""
        with pytest.raises(ParseError, match="cannot be empty"):
            parse_artifact_path("")

    def test_only_ref_raises_error(self) -> None:
        """Only a ref (no path) raises ParseError."""
        with pytest.raises(ParseError, match="Empty path"):
            parse_artifact_path(":latest")

    def test_invalid_path_component_raises_error(self) -> None:
        """Invalid path component raises ParseError."""
        with pytest.raises(ParseError, match="Invalid path component"):
            parse_artifact_path("-invalid/path")

    def test_double_colon_raises_error(self) -> None:
        """Double colon raises ParseError for parse_artifact_path."""
        with pytest.raises(ParseError, match="Multiple colons"):
            parse_artifact_path("images/ubuntu:latest:v1")


class TestArtifactRefDataclass:
    """Tests for ArtifactRef dataclass."""

    def test_is_hash_ref_true_for_hash(self) -> None:
        """is_hash_ref returns True for hash refs."""
        result = parse_artifact_ref("images/ubuntu:@abc12345")
        assert result.is_hash_ref is True

    def test_is_hash_ref_false_for_tag(self) -> None:
        """is_hash_ref returns False for tag refs."""
        result = parse_artifact_ref("images/ubuntu:latest")
        assert result.is_hash_ref is False

    def test_artifact_ref_equality(self) -> None:
        """ArtifactRef dataclass supports equality."""
        ref1 = ArtifactRef(path="images/ubuntu", ref="latest")
        ref2 = ArtifactRef(path="images/ubuntu", ref="latest")
        assert ref1 == ref2

    def test_artifact_ref_repr(self) -> None:
        """ArtifactRef has readable repr."""
        ref = ArtifactRef(path="images/ubuntu", ref="latest")
        assert "images/ubuntu" in repr(ref)
        assert "latest" in repr(ref)


class TestParseEdgeCases:
    """Edge case tests for parsing."""

    def test_single_component_path(self) -> None:
        """Single component (no slashes) path."""
        result = parse_artifact_ref("artifact:v1")
        assert result.path == "artifact"
        assert result.ref == "v1"

    def test_single_component_path_no_ref(self) -> None:
        """Single component path without ref."""
        result = parse_artifact_ref("artifact")
        assert result.path == "artifact"
        assert result.ref == "latest"

    def test_numeric_path_component(self) -> None:
        """Numeric path component is valid."""
        result = parse_artifact_ref("v2/images/ubuntu:latest")
        assert result.path == "v2/images/ubuntu"
        assert result.ref == "latest"

    def test_numeric_tag(self) -> None:
        """Numeric tag is valid."""
        result = parse_artifact_ref("images/ubuntu:20230101")
        assert result.path == "images/ubuntu"
        assert result.ref == "20230101"

    def test_very_long_path(self) -> None:
        """Very long path parses correctly."""
        long_path = "/".join(f"component{i}" for i in range(10))
        result = parse_artifact_ref(f"{long_path}:latest")
        assert result.path == long_path
        assert result.ref == "latest"

    def test_tag_with_version_dots(self) -> None:
        """Tag with version-style dots parses correctly."""
        result = parse_artifact_ref("images/ubuntu:22.04.1")
        assert result.path == "images/ubuntu"
        assert result.ref == "22.04.1"

    def test_semver_tag(self) -> None:
        """Semantic version tag parses correctly."""
        result = parse_artifact_ref("app/release:v1.2.3-rc1")
        assert result.path == "app/release"
        assert result.ref == "v1.2.3-rc1"


class TestParsePathNormalization:
    """Tests for path normalization in parse functions."""

    def test_parse_ref_strips_leading_slash(self) -> None:
        """parse_artifact_ref strips leading slash from path."""
        result = parse_artifact_ref("/images/ubuntu:latest")
        assert result.path == "images/ubuntu"
        assert result.ref == "latest"

    def test_parse_ref_strips_trailing_slash(self) -> None:
        """parse_artifact_ref strips trailing slash from path."""
        result = parse_artifact_ref("images/ubuntu/:latest")
        assert result.path == "images/ubuntu"
        assert result.ref == "latest"

    def test_parse_ref_strips_both_slashes(self) -> None:
        """parse_artifact_ref strips both leading and trailing slashes."""
        result = parse_artifact_ref("/images/ubuntu/:latest")
        assert result.path == "images/ubuntu"
        assert result.ref == "latest"

    def test_parse_ref_collapses_multiple_slashes(self) -> None:
        """parse_artifact_ref collapses multiple consecutive slashes."""
        result = parse_artifact_ref("images//ubuntu:latest")
        assert result.path == "images/ubuntu"
        assert result.ref == "latest"

    def test_parse_ref_full_normalization(self) -> None:
        """parse_artifact_ref applies all normalizations."""
        result = parse_artifact_ref("//images//ubuntu//:latest")
        assert result.path == "images/ubuntu"
        assert result.ref == "latest"

    def test_parse_ref_rejects_path_traversal(self) -> None:
        """parse_artifact_ref rejects path traversal."""
        with pytest.raises(ParseError, match="Path traversal"):
            parse_artifact_ref("images/../etc:latest")

    def test_parse_path_strips_leading_slash(self) -> None:
        """parse_artifact_path strips leading slash."""
        result = parse_artifact_path("/images/ubuntu")
        assert result == "images/ubuntu"

    def test_parse_path_strips_trailing_slash(self) -> None:
        """parse_artifact_path strips trailing slash."""
        result = parse_artifact_path("images/ubuntu/")
        assert result == "images/ubuntu"

    def test_parse_path_collapses_multiple_slashes(self) -> None:
        """parse_artifact_path collapses multiple consecutive slashes."""
        result = parse_artifact_path("images//ubuntu")
        assert result == "images/ubuntu"

    def test_parse_path_full_normalization(self) -> None:
        """parse_artifact_path applies all normalizations."""
        result = parse_artifact_path("//images//ubuntu//")
        assert result == "images/ubuntu"

    def test_parse_path_rejects_path_traversal(self) -> None:
        """parse_artifact_path rejects path traversal."""
        with pytest.raises(ParseError, match="Path traversal"):
            parse_artifact_path("images/../etc")

    def test_parse_path_strips_ref_with_normalization(self) -> None:
        """parse_artifact_path strips ref and normalizes path."""
        result = parse_artifact_path("/images//ubuntu/:latest")
        assert result == "images/ubuntu"

    def test_parse_ref_without_ref_normalizes(self) -> None:
        """parse_artifact_ref without explicit ref still normalizes path."""
        result = parse_artifact_ref("/images//ubuntu/")
        assert result.path == "images/ubuntu"
        assert result.ref == "latest"
