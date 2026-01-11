"""Unit tests for artifact reference parsing."""

from __future__ import annotations

import pytest

from magpie.cli.commands.parse import (
    ParseError,
    parse_artifact_path,
    parse_artifact_ref,
)


class TestParseArtifactRef:
    """Tests for parse_artifact_ref function."""

    def test_parse_with_tag_ref(self) -> None:
        """Parse artifact reference with tag ref."""
        path, ref = parse_artifact_ref("images/ubuntu:latest")
        assert path == "images/ubuntu"
        assert ref == "latest"

    def test_parse_with_hash_ref(self) -> None:
        """Parse artifact reference with hash ref."""
        path, ref = parse_artifact_ref("images/ubuntu:@abc12345")
        assert path == "images/ubuntu"
        assert ref == "@abc12345"

    def test_parse_without_ref_defaults_to_latest(self) -> None:
        """Parse artifact reference without ref defaults to latest."""
        path, ref = parse_artifact_ref("images/ubuntu")
        assert path == "images/ubuntu"
        assert ref == "latest"

    def test_parse_with_custom_default_ref(self) -> None:
        """Parse with custom default ref."""
        path, ref = parse_artifact_ref("images/ubuntu", default_ref="stable")
        assert path == "images/ubuntu"
        assert ref == "stable"

    def test_parse_nested_path_with_ref(self) -> None:
        """Parse nested path with ref."""
        path, ref = parse_artifact_ref("project/images/ubuntu:v1.0")
        assert path == "project/images/ubuntu"
        assert ref == "v1.0"

    def test_parse_deeply_nested_path(self) -> None:
        """Parse deeply nested path."""
        path, ref = parse_artifact_ref("org/team/project/component:release-2.0")
        assert path == "org/team/project/component"
        assert ref == "release-2.0"

    def test_parse_simple_path(self) -> None:
        """Parse simple single-component path."""
        path, ref = parse_artifact_ref("myartifact:v1")
        assert path == "myartifact"
        assert ref == "v1"

    def test_parse_path_with_dashes_and_underscores(self) -> None:
        """Parse path with dashes and underscores."""
        path, ref = parse_artifact_ref("my-project/my_artifact:stable-release")
        assert path == "my-project/my_artifact"
        assert ref == "stable-release"

    def test_parse_path_with_dots(self) -> None:
        """Parse path with dots."""
        path, ref = parse_artifact_ref("com.example/artifact.v2:1.0.0")
        assert path == "com.example/artifact.v2"
        assert ref == "1.0.0"


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

    def test_double_slash_in_path_raises_error(self) -> None:
        """Double slash in path raises ParseError."""
        with pytest.raises(ParseError, match="Empty component"):
            parse_artifact_ref("images//ubuntu:latest")

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
        path, ref = parse_artifact_ref("images/ubuntu:@a1b2c3d4")
        assert ref == "@a1b2c3d4"

    def test_valid_hash_ref_all_digits(self) -> None:
        """Hash ref with all digits parses correctly."""
        path, ref = parse_artifact_ref("images/ubuntu:@12345678")
        assert ref == "@12345678"

    def test_valid_hash_ref_all_letters(self) -> None:
        """Hash ref with all hex letters parses correctly."""
        path, ref = parse_artifact_ref("images/ubuntu:@abcdefab")
        assert ref == "@abcdefab"

    def test_hash_ref_too_short_raises_error(self) -> None:
        """Hash ref shorter than 8 chars raises error."""
        with pytest.raises(ParseError, match="exactly 8 hex characters"):
            parse_artifact_ref("images/ubuntu:@abc123")

    def test_hash_ref_too_long_raises_error(self) -> None:
        """Hash ref longer than 8 chars raises error with suggestion."""
        with pytest.raises(ParseError, match="too long.*@a1b2c3d4"):
            parse_artifact_ref("images/ubuntu:@a1b2c3d4e5f6")

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


class TestParseEdgeCases:
    """Edge case tests for parsing."""

    def test_single_component_path(self) -> None:
        """Single component (no slashes) path."""
        path, ref = parse_artifact_ref("artifact:v1")
        assert path == "artifact"
        assert ref == "v1"

    def test_single_component_path_no_ref(self) -> None:
        """Single component path without ref."""
        path, ref = parse_artifact_ref("artifact")
        assert path == "artifact"
        assert ref == "latest"

    def test_numeric_path_component(self) -> None:
        """Numeric path component is valid."""
        path, ref = parse_artifact_ref("v2/images/ubuntu:latest")
        assert path == "v2/images/ubuntu"
        assert ref == "latest"

    def test_numeric_tag(self) -> None:
        """Numeric tag is valid."""
        path, ref = parse_artifact_ref("images/ubuntu:20230101")
        assert path == "images/ubuntu"
        assert ref == "20230101"

    def test_very_long_path(self) -> None:
        """Very long path parses correctly."""
        long_path = "/".join(f"component{i}" for i in range(10))
        path, ref = parse_artifact_ref(f"{long_path}:latest")
        assert path == long_path
        assert ref == "latest"

    def test_tag_with_version_dots(self) -> None:
        """Tag with version-style dots parses correctly."""
        path, ref = parse_artifact_ref("images/ubuntu:22.04.1")
        assert path == "images/ubuntu"
        assert ref == "22.04.1"

    def test_semver_tag(self) -> None:
        """Semantic version tag parses correctly."""
        path, ref = parse_artifact_ref("app/release:v1.2.3-rc1")
        assert path == "app/release"
        assert ref == "v1.2.3-rc1"
