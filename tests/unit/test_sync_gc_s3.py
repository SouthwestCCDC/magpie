"""Unit tests for magpie-ctl sync gc-s3 command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from magpie.config import MagpieSettings
from magpie.ctl import cli as ctl_cli
from magpie.ctl.commands.sync import (
    _extract_blob_name_from_path,
    _find_orphaned_blobs,
    _parse_manifest_content,
)


class TestParseManifestContent:
    """Tests for _parse_manifest_content function."""

    def test_parses_valid_manifest(self) -> None:
        """Should extract blob hashes from valid manifest JSON."""
        content = json.dumps(
            {
                "version": 1,
                "tags": {
                    "latest": "@abc12345",
                    "v1.0": "@def67890",
                },
            }
        )
        result = _parse_manifest_content(content)
        assert result == {"abc12345", "def67890"}

    def test_handles_empty_tags(self) -> None:
        """Should return empty set for manifest with no tags."""
        content = json.dumps({"version": 1, "tags": {}})
        result = _parse_manifest_content(content)
        assert result == set()

    def test_handles_missing_tags_key(self) -> None:
        """Should return empty set when tags key is missing."""
        content = json.dumps({"version": 1})
        result = _parse_manifest_content(content)
        assert result == set()

    def test_handles_invalid_json(self) -> None:
        """Should return empty set for invalid JSON."""
        result = _parse_manifest_content("not valid json{")
        assert result == set()

    def test_handles_empty_string(self) -> None:
        """Should return empty set for empty string."""
        result = _parse_manifest_content("")
        assert result == set()

    def test_handles_null_hash_refs(self) -> None:
        """Should skip null hash refs."""
        content = json.dumps(
            {
                "version": 1,
                "tags": {
                    "latest": "@abc12345",
                    "empty": None,
                },
            }
        )
        result = _parse_manifest_content(content)
        assert result == {"abc12345"}

    def test_truncates_long_hashes(self) -> None:
        """Should truncate hash refs to 8 characters."""
        content = json.dumps(
            {
                "version": 1,
                "tags": {
                    "latest": "@abc12345extralongchars",
                },
            }
        )
        result = _parse_manifest_content(content)
        assert result == {"abc12345"}

    def test_deduplicates_hashes(self) -> None:
        """Should deduplicate when multiple tags point to same hash."""
        content = json.dumps(
            {
                "version": 1,
                "tags": {
                    "latest": "@abc12345",
                    "stable": "@abc12345",
                    "v1.0": "@abc12345",
                },
            }
        )
        result = _parse_manifest_content(content)
        assert result == {"abc12345"}


class TestExtractBlobNameFromPath:
    """Tests for _extract_blob_name_from_path function."""

    def test_extracts_blob_name(self) -> None:
        """Should extract blob name from standard path."""
        result = _extract_blob_name_from_path("artifact/blobs/abc12345")
        assert result == "abc12345"

    def test_extracts_from_nested_path(self) -> None:
        """Should extract blob name from deeply nested path."""
        result = _extract_blob_name_from_path("project/subdir/artifact/blobs/def67890")
        assert result == "def67890"

    def test_returns_none_for_non_blob_path(self) -> None:
        """Should return None for paths that don't contain blobs."""
        result = _extract_blob_name_from_path("artifact/metadata/abc12345.json")
        assert result is None

    def test_returns_none_for_empty_path(self) -> None:
        """Should return None for empty path."""
        result = _extract_blob_name_from_path("")
        assert result is None

    def test_returns_none_when_blobs_is_last(self) -> None:
        """Should return None when 'blobs' is the last component."""
        result = _extract_blob_name_from_path("artifact/blobs")
        assert result is None


class TestFindOrphanedBlobs:
    """Tests for _find_orphaned_blobs function."""

    @patch("magpie.ctl.commands.sync._list_s3_files_rclone")
    @patch("magpie.ctl.commands.sync._get_s3_file_content_rclone")
    def test_finds_orphaned_blobs_rclone(
        self,
        mock_get_content: MagicMock,
        mock_list_files: MagicMock,
    ) -> None:
        """Should identify blobs not referenced by any manifest using rclone."""

        # Setup mock responses
        def list_files_side_effect(bucket: str, prefix: str, pattern: str, debug: bool) -> list:
            if ".magpie" in pattern:
                return ["artifact/.magpie"]
            elif "blobs" in pattern:
                return [
                    "artifact/blobs/abc12345",  # Tagged
                    "artifact/blobs/orphaned1",  # Orphaned
                    "artifact/blobs/orphaned2",  # Orphaned
                ]
            return []

        mock_list_files.side_effect = list_files_side_effect
        mock_get_content.return_value = json.dumps({"version": 1, "tags": {"latest": "@abc12345"}})

        orphaned, manifests, tagged, total, errors = _find_orphaned_blobs(
            "test-bucket", "", "rclone", False, True
        )

        assert orphaned == ["artifact/blobs/orphaned1", "artifact/blobs/orphaned2"]
        assert manifests == 1
        assert tagged == 1
        assert total == 3
        assert errors == []

    @patch("magpie.ctl.commands.sync._list_s3_files_aws")
    @patch("magpie.ctl.commands.sync._get_s3_file_content_aws")
    def test_finds_orphaned_blobs_aws(
        self,
        mock_get_content: MagicMock,
        mock_list_files: MagicMock,
    ) -> None:
        """Should identify blobs not referenced by any manifest using aws cli."""

        def list_files_side_effect(bucket: str, prefix: str, pattern: str, debug: bool) -> list:
            if ".magpie" in pattern:
                return ["proj/artifact/.magpie"]
            elif "blobs" in pattern:
                return [
                    "proj/artifact/blobs/def67890",  # Tagged
                    "proj/artifact/blobs/orphan",  # Orphaned
                ]
            return []

        mock_list_files.side_effect = list_files_side_effect
        mock_get_content.return_value = json.dumps({"version": 1, "tags": {"v1": "@def67890"}})

        orphaned, manifests, tagged, total, errors = _find_orphaned_blobs(
            "test-bucket", "backup", "aws", False, True
        )

        assert orphaned == ["proj/artifact/blobs/orphan"]
        assert manifests == 1
        assert tagged == 1
        assert total == 2

    @patch("magpie.ctl.commands.sync._list_s3_files_rclone")
    @patch("magpie.ctl.commands.sync._get_s3_file_content_rclone")
    def test_no_orphans_when_all_tagged(
        self,
        mock_get_content: MagicMock,
        mock_list_files: MagicMock,
    ) -> None:
        """Should return empty list when all blobs are tagged."""

        def list_files_side_effect(bucket: str, prefix: str, pattern: str, debug: bool) -> list:
            if ".magpie" in pattern:
                return ["artifact/.magpie"]
            elif "blobs" in pattern:
                return ["artifact/blobs/abc12345"]
            return []

        mock_list_files.side_effect = list_files_side_effect
        mock_get_content.return_value = json.dumps({"version": 1, "tags": {"latest": "@abc12345"}})

        orphaned, _, _, _, errors = _find_orphaned_blobs("test-bucket", "", "rclone", False, True)

        assert orphaned == []
        assert errors == []

    @patch("magpie.ctl.commands.sync._list_s3_files_rclone")
    @patch("magpie.ctl.commands.sync._get_s3_file_content_rclone")
    def test_handles_multiple_manifests(
        self,
        mock_get_content: MagicMock,
        mock_list_files: MagicMock,
    ) -> None:
        """Should aggregate tags from multiple manifests."""

        def list_files_side_effect(bucket: str, prefix: str, pattern: str, debug: bool) -> list:
            if ".magpie" in pattern:
                return ["art1/.magpie", "art2/.magpie"]
            elif "blobs" in pattern:
                return [
                    "art1/blobs/hash1111",
                    "art1/blobs/hash2222",
                    "art2/blobs/hash3333",
                    "art2/blobs/orphaned",
                ]
            return []

        manifest_contents = {
            "art1/.magpie": json.dumps(
                {"version": 1, "tags": {"v1": "@hash1111", "v2": "@hash2222"}}
            ),
            "art2/.magpie": json.dumps({"version": 1, "tags": {"latest": "@hash3333"}}),
        }

        def get_content_side_effect(bucket: str, prefix: str, path: str, debug: bool) -> str:
            return manifest_contents.get(path, "")

        mock_list_files.side_effect = list_files_side_effect
        mock_get_content.side_effect = get_content_side_effect

        orphaned, manifests, tagged, total, errors = _find_orphaned_blobs(
            "test-bucket", "", "rclone", False, True
        )

        assert orphaned == ["art2/blobs/orphaned"]
        assert manifests == 2
        assert tagged == 3  # hash1111, hash2222, hash3333
        assert total == 4

    @patch("magpie.ctl.commands.sync._list_s3_files_rclone")
    @patch("magpie.ctl.commands.sync._get_s3_file_content_rclone")
    def test_reports_manifest_read_errors(
        self,
        mock_get_content: MagicMock,
        mock_list_files: MagicMock,
    ) -> None:
        """Should report errors when manifest cannot be read."""

        def list_files_side_effect(bucket: str, prefix: str, pattern: str, debug: bool) -> list:
            if ".magpie" in pattern:
                return ["artifact/.magpie"]
            return []

        mock_list_files.side_effect = list_files_side_effect
        mock_get_content.return_value = None  # Simulate read failure

        _, _, _, _, errors = _find_orphaned_blobs("test-bucket", "", "rclone", False, True)

        assert len(errors) == 1
        assert "Failed to read manifest" in errors[0]


class TestGcS3Command:
    """Integration tests for sync gc-s3 command."""

    @pytest.fixture
    def cli_runner(self) -> CliRunner:
        """Create Click CLI test runner."""
        return CliRunner()

    def test_requires_s3_bucket(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Should fail when S3 bucket is not configured."""
        result = cli_runner.invoke(
            ctl_cli,
            ["sync", "gc-s3"],
            env={"MAGPIE_STORAGE_PATH": str(tmp_path)},
        )

        assert result.exit_code != 0
        assert "MAGPIE_S3_BUCKET" in result.output

    def test_requires_sync_tool(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Should fail when no sync tool is available."""
        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value=None):
            with patch("magpie.ctl.get_settings", return_value=settings):
                result = cli_runner.invoke(
                    ctl_cli,
                    ["sync", "gc-s3"],
                )

                assert result.exit_code != 0
                assert "rclone" in result.output.lower() or "aws" in result.output.lower()

    @patch("magpie.ctl.commands.sync._find_orphaned_blobs")
    @patch("magpie.ctl.commands.sync._get_s3_file_size_rclone")
    def test_dry_run_is_default(
        self,
        mock_get_sizes: MagicMock,
        mock_find_orphaned: MagicMock,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Should run in dry-run mode by default (safe by default)."""
        mock_find_orphaned.return_value = (
            ["artifact/blobs/orphan1"],  # orphaned paths
            1,  # manifest count
            1,  # tagged count
            2,  # total blobs
            [],  # errors
        )
        mock_get_sizes.return_value = {"artifact/blobs/orphan1": 1024}

        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value="/usr/bin/rclone"):
            with patch("magpie.ctl.get_settings", return_value=settings):
                result = cli_runner.invoke(
                    ctl_cli,
                    ["sync", "gc-s3"],
                )

                assert result.exit_code == 0, f"Output: {result.output}"
                assert "Preview" in result.output
                assert "Would delete" in result.output or "Run with --execute" in result.output

    @patch("magpie.ctl.commands.sync._find_orphaned_blobs")
    @patch("magpie.ctl.commands.sync._get_s3_file_size_rclone")
    @patch("magpie.ctl.commands.sync._delete_s3_file_rclone")
    def test_execute_actually_deletes(
        self,
        mock_delete: MagicMock,
        mock_get_sizes: MagicMock,
        mock_find_orphaned: MagicMock,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Should actually delete blobs when --execute is provided."""
        mock_find_orphaned.return_value = (
            ["artifact/blobs/orphan1", "artifact/blobs/orphan2"],
            1,
            1,
            3,
            [],
        )
        mock_get_sizes.return_value = {
            "artifact/blobs/orphan1": 1024,
            "artifact/blobs/orphan2": 2048,
        }
        mock_delete.return_value = True

        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value="/usr/bin/rclone"):
            with patch("magpie.ctl.get_settings", return_value=settings):
                result = cli_runner.invoke(
                    ctl_cli,
                    ["sync", "gc-s3", "--execute"],
                )

                assert result.exit_code == 0, f"Output: {result.output}"
                assert "Complete" in result.output
                assert "Deleted: 2" in result.output
                assert mock_delete.call_count == 2

    @patch("magpie.ctl.commands.sync._find_orphaned_blobs")
    @patch("magpie.ctl.commands.sync._get_s3_file_size_rclone")
    def test_no_orphans_message(
        self,
        mock_get_sizes: MagicMock,
        mock_find_orphaned: MagicMock,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Should show appropriate message when no orphans found."""
        mock_find_orphaned.return_value = ([], 2, 5, 5, [])
        mock_get_sizes.return_value = {}

        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value="/usr/bin/rclone"):
            with patch("magpie.ctl.get_settings", return_value=settings):
                result = cli_runner.invoke(
                    ctl_cli,
                    ["sync", "gc-s3"],
                )

                assert result.exit_code == 0
                assert (
                    "No orphaned blobs found" in result.output
                    or "Orphaned blobs: 0" in result.output
                )


class TestGcS3JsonOutput:
    """Tests for JSON output mode."""

    @pytest.fixture
    def cli_runner(self) -> CliRunner:
        """Create Click CLI test runner."""
        return CliRunner()

    @patch("magpie.ctl.commands.sync._find_orphaned_blobs")
    @patch("magpie.ctl.commands.sync._get_s3_file_size_rclone")
    def test_json_output_dry_run(
        self,
        mock_get_sizes: MagicMock,
        mock_find_orphaned: MagicMock,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Should output valid JSON in dry-run mode."""
        mock_find_orphaned.return_value = (["artifact/blobs/orphan1"], 2, 3, 4, [])
        mock_get_sizes.return_value = {"artifact/blobs/orphan1": 1024}

        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value="/usr/bin/rclone"):
            with patch("magpie.ctl.get_settings", return_value=settings):
                result = cli_runner.invoke(
                    ctl_cli,
                    ["--format", "json", "sync", "gc-s3"],
                )

                assert result.exit_code == 0, f"Output: {result.output}"
                data = json.loads(result.output)
                assert data["status"] == "ok"
                assert data["data"]["dry_run"] is True
                assert data["data"]["manifests_found"] == 2
                assert data["data"]["tagged_blobs"] == 3
                assert data["data"]["total_blobs"] == 4
                assert data["data"]["orphaned_count"] == 1
                assert data["data"]["orphaned_bytes"] == 1024
                assert data["data"]["orphaned_paths"] == ["artifact/blobs/orphan1"]

    @patch("magpie.ctl.commands.sync._find_orphaned_blobs")
    @patch("magpie.ctl.commands.sync._get_s3_file_size_rclone")
    @patch("magpie.ctl.commands.sync._delete_s3_file_rclone")
    def test_json_output_execute(
        self,
        mock_delete: MagicMock,
        mock_get_sizes: MagicMock,
        mock_find_orphaned: MagicMock,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Should output valid JSON after execution."""
        mock_find_orphaned.return_value = (["artifact/blobs/orphan1"], 1, 2, 3, [])
        mock_get_sizes.return_value = {"artifact/blobs/orphan1": 2048}
        mock_delete.return_value = True

        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value="/usr/bin/rclone"):
            with patch("magpie.ctl.get_settings", return_value=settings):
                result = cli_runner.invoke(
                    ctl_cli,
                    ["--format", "json", "sync", "gc-s3", "--execute"],
                )

                assert result.exit_code == 0, f"Output: {result.output}"
                data = json.loads(result.output)
                assert data["status"] == "ok"
                assert data["data"]["dry_run"] is False
                assert data["data"]["deleted_count"] == 1
                assert data["data"]["deleted_bytes"] == 2048
                # After execution, orphaned_paths should be empty
                assert data["data"]["orphaned_paths"] == []
