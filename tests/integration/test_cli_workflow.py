"""Integration tests for CLI workflows with multi-segment paths and complete lifecycle.

This module tests CLI commands using Click's CliRunner, focusing on:
1. Multi-segment paths (org/project/artifact style paths)
2. Complete artifact lifecycle (push -> ls -> get -> tag -> amend -> untag)
3. Hash ref vs tag retrieval comparison

Generated with assistance from Claude Code (Opus 4.5).
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie.cli import cli
from magpie.storage.service import StorageService
from tests.integration.conftest import (
    MockClientWithDownload,
    MockStreamResponse,
    upload_test_artifact,
)

# Patch path for get_client - must match where it's imported/used in the CLI module
PATCH_GET_CLIENT = "magpie.cli.get_client"


class TestMultiSegmentPathPush:
    """Tests for push command with multi-segment paths (org/project/artifact)."""

    def test_push_three_segment_path(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push to org/project/artifact path works correctly."""
        test_file = tmp_path / "artifact.bin"
        test_content = b"multi-segment push content"
        test_file.write_bytes(test_content)

        expected_hash = hashlib.sha256(test_content).hexdigest()

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "push",
                    str(test_file),
                    "--to",
                    "acme/webapp/frontend",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert expected_hash in result.output
        assert "Uploaded:" in result.output or "Duplicate:" in result.output

    def test_push_four_segment_path(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push to org/project/component/artifact path works correctly."""
        test_file = tmp_path / "deep_artifact.bin"
        test_content = b"deep nested push content"
        test_file.write_bytes(test_content)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "push",
                    str(test_file),
                    "--to",
                    "acme/webapp/frontend/bundle",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert "Uploaded:" in result.output or "Duplicate:" in result.output


class TestMultiSegmentPathGet:
    """Tests for get command with multi-segment paths."""

    def test_get_three_segment_path(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get from org/project/artifact path works correctly."""
        test_content = b"content for get test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/acme/webapp/backend", files=files)

        output_file = tmp_path / "downloaded.bin"
        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "acme/webapp/backend:latest",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert output_file.exists()
        assert output_file.read_bytes() == test_content

    def test_get_four_segment_path(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get from org/project/component/artifact path works correctly."""
        test_content = b"deep nested get content"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/acme/webapp/api/schema", files=files)

        output_file = tmp_path / "schema_downloaded.bin"
        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "acme/webapp/api/schema",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert output_file.read_bytes() == test_content


class TestMultiSegmentPathTag:
    """Tests for tag command with multi-segment paths."""

    def test_tag_three_segment_path(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Tag on org/project/artifact path works correctly."""
        upload_data = upload_test_artifact(api_client, "acme/webapp/config", b"config content")
        hash_ref = upload_data["hash_ref"]

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "tag",
                    f"acme/webapp/config:{hash_ref}",
                    "--as",
                    "v1.0.0",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "v1.0.0" in result.output

    def test_tag_four_segment_path(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Tag on org/project/component/artifact path works correctly."""
        upload_test_artifact(api_client, "acme/platform/db/migrations", b"migration content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "tag",
                    "acme/platform/db/migrations:latest",
                    "--as",
                    "v2.0.0",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "v2.0.0" in result.output


class TestMultiSegmentPathAmend:
    """Tests for amend command with multi-segment paths."""

    def test_amend_three_segment_path(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Amend on org/project/artifact path works correctly."""
        upload_test_artifact(api_client, "acme/webapp/assets", b"assets content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "amend",
                    "acme/webapp/assets:latest",
                    "--source-uri",
                    "https://github.com/acme/webapp/assets",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Updated:" in result.output
        assert "https://github.com/acme/webapp/assets" in result.output

    def test_amend_four_segment_path(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Amend on org/project/component/artifact path works correctly."""
        upload_test_artifact(api_client, "acme/platform/auth/keys", b"keys content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "amend",
                    "acme/platform/auth/keys:latest",
                    "--source-uri",
                    "https://vault.acme.com/keys/v1",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "https://vault.acme.com/keys/v1" in result.output


class TestMultiSegmentPathUntag:
    """Tests for untag command with multi-segment paths."""

    def test_untag_three_segment_path(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Untag on org/project/artifact path works correctly."""
        upload_test_artifact(api_client, "acme/webapp/cache", b"cache content")

        # Create a tag first via API
        api_client.post(
            "/api/v1/artifacts/acme/webapp/cache/latest/tags",
            json={"tag_name": "temp-tag"},
        )

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "untag", "acme/webapp/cache", "temp-tag"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Removed tag 'temp-tag'" in result.output

    def test_untag_four_segment_path(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Untag on org/project/component/artifact path works correctly."""
        upload_test_artifact(api_client, "acme/platform/cdn/static", b"static content")

        api_client.post(
            "/api/v1/artifacts/acme/platform/cdn/static/latest/tags",
            json={"tag_name": "preview"},
        )

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "untag",
                    "acme/platform/cdn/static",
                    "preview",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Removed tag 'preview'" in result.output


class TestMultiSegmentPathInfo:
    """Tests for info command with multi-segment paths."""

    def test_info_three_segment_path(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Info on org/project/artifact path displays metadata correctly."""
        upload_data = upload_test_artifact(
            api_client,
            "acme/webapp/manifest",
            b"manifest content",
            source_uri="https://github.com/acme/webapp",
        )

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", "acme/webapp/manifest:latest"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert upload_data["hash"] in result.output
        assert upload_data["hash_ref"] in result.output
        assert "Source URI:" in result.output
        assert "https://github.com/acme/webapp" in result.output

    def test_info_four_segment_path(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Info on org/project/component/artifact path displays metadata correctly."""
        upload_data = upload_test_artifact(
            api_client,
            "acme/platform/search/index",
            b"index content",
            source_uri="https://github.com/acme/platform/search",
        )

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "info",
                    "acme/platform/search/index:latest",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert upload_data["hash_ref"] in result.output


class TestCompleteArtifactLifecycle:
    """End-to-end test of complete artifact lifecycle through CLI."""

    def test_complete_artifact_lifecycle(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Test complete artifact lifecycle: push -> ls -> get -> tag -> info -> amend -> untag."""
        # Test data
        artifact_path = "lifecycle/test/artifact"
        test_content = b"lifecycle test content - unique data for verification"
        test_file = tmp_path / "lifecycle_test.bin"
        test_file.write_bytes(test_content)
        expected_hash = hashlib.sha256(test_content).hexdigest()

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            # Step 1: Push artifact
            push_result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "push",
                    str(test_file),
                    "--to",
                    artifact_path,
                ],
            )

            assert push_result.exit_code == 0, f"Push failed: {push_result.output}"
            assert expected_hash in push_result.output
            assert "Hash ref:" in push_result.output

            # Extract hash ref from output for later use
            hash_ref = None
            for line in push_result.output.split("\n"):
                if "Hash ref:" in line:
                    hash_ref = line.split("Hash ref:")[-1].strip()
                    break
            assert hash_ref is not None, "Could not extract hash_ref from push output"

            # Step 2: Verify via ls
            ls_result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", artifact_path],
            )

            assert ls_result.exit_code == 0, f"Ls failed: {ls_result.output}"
            assert "latest" in ls_result.output
            assert hash_ref in ls_result.output

            # Step 3: Get and verify content matches
            output_file = tmp_path / "downloaded_lifecycle.bin"
            get_result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    f"{artifact_path}:latest",
                    "-o",
                    str(output_file),
                ],
            )

            assert get_result.exit_code == 0, f"Get failed: {get_result.output}"
            assert output_file.exists()
            assert output_file.read_bytes() == test_content

            # Step 4: Tag as stable
            tag_result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "tag",
                    f"{artifact_path}:latest",
                    "--as",
                    "stable",
                ],
            )

            assert tag_result.exit_code == 0, f"Tag failed: {tag_result.output}"
            assert "stable" in tag_result.output

            # Step 5: Get via tag name (stable)
            output_file_stable = tmp_path / "downloaded_stable.bin"
            get_stable_result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    f"{artifact_path}:stable",
                    "-o",
                    str(output_file_stable),
                ],
            )

            assert get_stable_result.exit_code == 0, (
                f"Get stable failed: {get_stable_result.output}"
            )
            assert output_file_stable.read_bytes() == test_content

            # Step 6: Amend source-uri
            amend_result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "amend",
                    f"{artifact_path}:stable",
                    "--source-uri",
                    "https://github.com/lifecycle/test@v1.0.0",
                ],
            )

            assert amend_result.exit_code == 0, f"Amend failed: {amend_result.output}"
            assert "Updated:" in amend_result.output
            assert "https://github.com/lifecycle/test@v1.0.0" in amend_result.output

            # Step 7: Verify amended metadata via info
            info_result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", f"{artifact_path}:stable"],
            )

            assert info_result.exit_code == 0, f"Info failed: {info_result.output}"
            assert expected_hash in info_result.output
            assert "https://github.com/lifecycle/test@v1.0.0" in info_result.output
            assert "stable" in info_result.output
            assert "latest" in info_result.output

            # Step 8: Untag the stable tag
            untag_result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "untag", artifact_path, "stable"],
            )

            assert untag_result.exit_code == 0, f"Untag failed: {untag_result.output}"
            assert "Removed tag 'stable'" in untag_result.output

            # Step 9: Verify tag removed via ls
            ls_after_untag = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", artifact_path],
            )

            assert ls_after_untag.exit_code == 0
            assert "stable" not in ls_after_untag.output
            # latest should still be there
            assert "latest" in ls_after_untag.output


class TestHashRefVsTagRetrieval:
    """Tests comparing hash ref retrieval vs tag retrieval."""

    def test_hash_ref_and_tag_return_identical_content(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Download by tag and hash ref return identical content."""
        test_content = b"content for hash vs tag comparison test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        upload_response = api_client.post("/api/v1/upload/hashvstag/compare", files=files)
        assert upload_response.status_code == 200
        upload_data = upload_response.json()
        hash_ref = upload_data["hash_ref"]

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            # Download by tag
            output_by_tag = tmp_path / "by_tag.bin"
            tag_result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "hashvstag/compare:latest",
                    "-o",
                    str(output_by_tag),
                ],
            )

            assert tag_result.exit_code == 0, f"Tag get failed: {tag_result.output}"

            # Download by hash ref
            output_by_hash = tmp_path / "by_hash.bin"
            hash_result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    f"hashvstag/compare:{hash_ref}",
                    "-o",
                    str(output_by_hash),
                ],
            )

            assert hash_result.exit_code == 0, f"Hash get failed: {hash_result.output}"

        # Verify both return identical content
        assert output_by_tag.read_bytes() == test_content
        assert output_by_hash.read_bytes() == test_content
        assert output_by_tag.read_bytes() == output_by_hash.read_bytes()

    def test_tag_uses_symlink_path_hash_uses_blob_path(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Verify tag retrieval uses symlink path, hash ref uses blobs/ path."""
        test_content = b"content for path verification"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        upload_response = api_client.post("/api/v1/upload/pathtest/verify", files=files)
        assert upload_response.status_code == 200
        upload_data = upload_response.json()
        hash_ref = upload_data["hash_ref"]

        # Track URLs requested
        urls_requested: list[str] = []

        mock_client = MockClientWithDownload(api_client, test_storage_service)
        original_stream = mock_client.stream

        def tracking_stream(method: str, url: str) -> MockStreamResponse:
            urls_requested.append(url)
            return original_stream(method, url)

        mock_client.stream = tracking_stream

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            # Get by tag
            output_tag = tmp_path / "tag_path.bin"
            cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "pathtest/verify:latest",
                    "-o",
                    str(output_tag),
                ],
            )

            # Get by hash ref
            output_hash = tmp_path / "hash_path.bin"
            cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    f"pathtest/verify:{hash_ref}",
                    "-o",
                    str(output_hash),
                ],
            )

        # Verify URL patterns
        assert len(urls_requested) == 2
        # First request (by tag) should use tag symlink path
        assert urls_requested[0] == "/artifacts/pathtest/verify/latest"
        # Second request (by hash) should use blobs/ path
        blob_name = hash_ref.lstrip("@")
        assert urls_requested[1] == f"/artifacts/pathtest/verify/blobs/{blob_name}"

    def test_get_specific_version_by_hash_while_latest_differs(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get specific version by hash ref while latest points to different content."""
        # Upload first version
        content_v1 = b"version 1 content"
        files_v1 = {"file": ("artifact.bin", io.BytesIO(content_v1), "application/octet-stream")}
        upload_v1 = api_client.post("/api/v1/upload/versions/test", files=files_v1)
        assert upload_v1.status_code == 200
        hash_ref_v1 = upload_v1.json()["hash_ref"]

        # Upload second version (this becomes "latest")
        content_v2 = b"version 2 content - different"
        files_v2 = {"file": ("artifact.bin", io.BytesIO(content_v2), "application/octet-stream")}
        upload_v2 = api_client.post("/api/v1/upload/versions/test", files=files_v2)
        assert upload_v2.status_code == 200

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            # Get latest (should be v2)
            output_latest = tmp_path / "latest.bin"
            cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "versions/test:latest",
                    "-o",
                    str(output_latest),
                ],
            )

            # Get v1 by hash ref
            output_v1 = tmp_path / "v1.bin"
            cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    f"versions/test:{hash_ref_v1}",
                    "-o",
                    str(output_v1),
                ],
            )

        # Latest should return v2 content
        assert output_latest.read_bytes() == content_v2
        # Hash ref should return v1 content
        assert output_v1.read_bytes() == content_v1
        # They should be different
        assert output_latest.read_bytes() != output_v1.read_bytes()
