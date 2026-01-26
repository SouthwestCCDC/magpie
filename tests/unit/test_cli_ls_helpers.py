"""Unit tests for ls command helper functions."""

from __future__ import annotations

from magpie.cli.commands.ls import _extract_immediate_children


class TestExtractImmediateChildren:
    """Tests for _extract_immediate_children helper function."""

    def test_empty_paths_returns_empty(self) -> None:
        """Empty paths list returns empty list."""
        result = _extract_immediate_children([], "")
        assert result == []

    def test_root_level_shows_top_directories(self) -> None:
        """Root level listing shows top-level directories with trailing slash."""
        paths = [
            "external-test/file.txt",
            "iso/vyos-sagitta-1.4.4.iso",
            "orchestrator-test/file.txt",
            "test/hello.txt",
        ]
        result = _extract_immediate_children(paths, "")

        # Should show directories with trailing slash
        assert result == [
            "external-test/",
            "iso/",
            "orchestrator-test/",
            "test/",
        ]

    def test_root_level_with_root_artifact(self) -> None:
        """Root level with direct artifact shows it without slash."""
        paths = [
            "root-artifact",
            "test/nested.txt",
        ]
        result = _extract_immediate_children(paths, "")

        assert result == [
            "root-artifact",
            "test/",
        ]

    def test_prefix_shows_children_without_prefix(self) -> None:
        """Listing with prefix shows children relative to prefix."""
        paths = [
            "test/artifact1",
            "test/artifact2",
            "test/sub/deep",
        ]
        result = _extract_immediate_children(paths, "test")

        # Should show immediate children relative to test/
        assert result == [
            "artifact1",
            "artifact2",
            "sub/",
        ]

    def test_prefix_with_leading_slash_normalized(self) -> None:
        """Prefix with leading slash is handled correctly."""
        paths = [
            "test/artifact1",
            "test/artifact2",
        ]
        result = _extract_immediate_children(paths, "/test")

        assert result == [
            "artifact1",
            "artifact2",
        ]

    def test_prefix_with_trailing_slash_normalized(self) -> None:
        """Prefix with trailing slash is handled correctly."""
        paths = [
            "test/artifact1",
            "test/artifact2",
        ]
        result = _extract_immediate_children(paths, "test/")

        assert result == [
            "artifact1",
            "artifact2",
        ]

    def test_deeply_nested_paths_show_first_level_only(self) -> None:
        """Deeply nested paths show only first level under prefix."""
        paths = [
            "namespace/category/subcategory/artifact",
            "namespace/category/another/item",
            "namespace/other",
        ]
        result = _extract_immediate_children(paths, "namespace")

        assert result == [
            "category/",
            "other",
        ]

    def test_mixed_depth_paths(self) -> None:
        """Mixed depth paths are handled correctly."""
        paths = [
            "test/artifact1",
            "test/artifact2",
            "test/sub/deep",
            "images/ubuntu",
        ]
        result = _extract_immediate_children(paths, "")

        assert result == [
            "images/",
            "test/",
        ]

    def test_single_segment_artifact_at_root(self) -> None:
        """Single-segment artifact at root is shown without slash."""
        paths = [
            "standalone-artifact",
        ]
        result = _extract_immediate_children(paths, "")

        # Single-segment artifact should not have trailing slash
        assert result == [
            "standalone-artifact",
        ]

    def test_duplicate_children_deduplicated(self) -> None:
        """Multiple paths in same child directory are deduplicated."""
        paths = [
            "test/artifact1",
            "test/artifact2",
            "test/artifact3",
        ]
        result = _extract_immediate_children(paths, "")

        # Should only show "test/" once
        assert result == ["test/"]

    def test_sorted_output(self) -> None:
        """Output is sorted alphabetically."""
        paths = [
            "zebra/file",
            "alpha/file",
            "beta/file",
        ]
        result = _extract_immediate_children(paths, "")

        assert result == ["alpha/", "beta/", "zebra/"]

    def test_deeper_prefix(self) -> None:
        """Works with deeper prefix paths."""
        paths = [
            "namespace/category/item1",
            "namespace/category/item2",
            "namespace/category/sub/deep",
        ]
        result = _extract_immediate_children(paths, "namespace/category")

        assert result == [
            "item1",
            "item2",
            "sub/",
        ]
