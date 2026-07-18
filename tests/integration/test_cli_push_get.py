"""Integration tests for CLI push and get commands."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie.cli import cli
from magpie.cli.commands.parse import parse_artifact_ref
from magpie.storage.service import StorageService
from tests.integration.conftest import (
    MockClientWithDownload,
    MockStreamResponse,
    upload_test_artifact,
)

# Patch path for get_client - must match where it's imported/used in the CLI module
# Since CLIContext imports get_client from magpie.cli.client, we patch there
PATCH_GET_CLIENT = "magpie.cli.get_client"


class TestParseArtifactRef:
    """Tests for artifact reference parsing."""

    def test_parse_with_ref(self) -> None:
        """Parse artifact reference with explicit ref."""
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

    def test_parse_nested_path_with_ref(self) -> None:
        """Parse nested path with ref."""
        result = parse_artifact_ref("project/images/ubuntu:v1.0")
        assert result.path == "project/images/ubuntu"
        assert result.ref == "v1.0"


class TestPushCommand:
    """Integration tests for push command."""

    def test_push_uploads_file_and_returns_hash(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push command uploads file and returns hash."""
        test_file = tmp_path / "test.bin"
        test_content = b"test content for push"
        test_file.write_bytes(test_content)

        expected_hash = hashlib.sha256(test_content).hexdigest()

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "push", str(test_file), "--to", "test/artifact"],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert expected_hash in result.output
        assert "Uploaded:" in result.output or "Duplicate:" in result.output
        assert "Hash ref:" in result.output

    def test_push_with_source_uri(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Push with --source-uri includes it in metadata."""
        test_file = tmp_path / "source_uri_test.bin"
        test_content = b"content with source uri"
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
                    "source/uri-test",
                    "--source-uri",
                    "git://repo@v1.0",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"

        # Verify source_uri was stored in metadata
        info = test_storage_service.get_artifact_info("source/uri-test", "latest")
        assert info.source_uri == "git://repo@v1.0"

    def test_push_duplicate_returns_same_hash(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Duplicate upload returns same hash with is_duplicate indication."""
        test_file = tmp_path / "duplicate_test.bin"
        test_content = b"duplicate content test"
        test_file.write_bytes(test_content)

        expected_hash = hashlib.sha256(test_content).hexdigest()

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            # First upload
            result1 = cli_runner.invoke(
                cli,
                ["--server", "http://test", "push", str(test_file), "--to", "dup/test"],
            )
            assert result1.exit_code == 0
            assert "Uploaded:" in result1.output

            # Second upload (same content)
            result2 = cli_runner.invoke(
                cli,
                ["--server", "http://test", "push", str(test_file), "--to", "dup/test"],
            )
            assert result2.exit_code == 0
            assert "Duplicate:" in result2.output
            assert expected_hash in result2.output

    def test_push_requires_server(self, cli_runner_no_config: CliRunner, tmp_path: Path) -> None:
        """Push without server configured fails with error."""
        test_file = tmp_path / "no_server.bin"
        test_file.write_bytes(b"content")

        result = cli_runner_no_config.invoke(
            cli,
            ["push", str(test_file), "--to", "test/artifact"],
        )

        assert result.exit_code != 0
        assert "No server configured" in result.output


class TestGetCommand:
    """Integration tests for get command."""

    def test_get_downloads_and_verifies_hash(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get command downloads artifact and verifies hash."""
        # First upload a file
        test_content = b"content for download test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        upload_response = api_client.post("/api/v1/upload/get/test", files=files)
        assert upload_response.status_code == 200

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
                    "get/test:latest",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert "Downloaded:" in result.output
        assert output_file.exists()
        assert output_file.read_bytes() == test_content

    def test_get_with_no_verify_skips_verification(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get with --no-verify skips hash verification."""
        # Upload a file
        test_content = b"content for no-verify test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/noverify/test", files=files)

        output_file = tmp_path / "noverify.bin"

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "noverify/test",
                    "-o",
                    str(output_file),
                    "--no-verify",
                ],
            )

        assert result.exit_code == 0
        assert output_file.exists()

    def test_get_detects_hash_mismatch(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Get detects hash mismatch when download is corrupted."""
        # Upload a file
        test_content = b"content for mismatch test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        upload_resp = api_client.post("/api/v1/upload/mismatch/test", files=files)
        upload_data = upload_resp.json()

        output_file = tmp_path / "mismatch.bin"

        # Create a mock client that returns corrupted content
        mock_client = MagicMock()

        # Mock info response
        info_response = MagicMock()
        info_response.status_code = 200
        info_response.json.return_value = {
            "hash": upload_data["hash"],
            "hash_ref": upload_data["hash_ref"],
        }

        def mock_get(url: str) -> MagicMock:
            if "/info" in url:
                return info_response
            # Fallback for any other GET requests
            fallback = MagicMock()
            fallback.status_code = 404
            return fallback

        # Mock streaming response with corrupted content
        def mock_stream(method: str, url: str) -> MockStreamResponse:
            return MockStreamResponse(200, b"CORRUPTED CONTENT")

        mock_client.get = mock_get
        mock_client.stream = mock_stream
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "mismatch/test",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code != 0
        assert "Hash mismatch" in result.output

    def test_get_defaults_to_latest_ref(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get without explicit ref uses 'latest'."""
        # Upload a file
        test_content = b"content for latest test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/latest/test", files=files)

        output_file = tmp_path / "latest.bin"

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            # No explicit ref - should default to latest
            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "latest/test",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0
        assert output_file.read_bytes() == test_content

    def test_get_artifact_not_found(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Get returns error for non-existent artifact."""
        output_file = tmp_path / "notfound.bin"

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "nonexistent/artifact",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_get_with_bad_ref_on_existing_artifact_is_not_a_prefix(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """A real artifact with a nonexistent tag/ref must not be misreported as a prefix."""
        upload_test_artifact(api_client, "test/badref", b"real artifact content")
        output_file = tmp_path / "badref.bin"

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "test/badref:nonexistent-tag",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code != 0
        assert "Artifact not found: test/badref:nonexistent-tag" in result.output
        assert "not an artifact" not in result.output

    def test_get_on_path_prefix_gives_prefix_aware_error(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Get on a path that is a prefix (has children) points to `ls`, not a bare 404."""
        upload_test_artifact(api_client, "smoke/hello", b"prefix test content")
        output_file = tmp_path / "smoke.bin"

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "get", "smoke", "-o", str(output_file)],
            )

        assert result.exit_code != 0
        assert "not an artifact" in result.output
        assert "magpie ls smoke/" in result.output
        assert "smoke:latest" not in result.output

    def test_get_by_hash_ref_uses_blobs_path(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get by hash ref uses blobs/ path instead of tag symlink."""
        # Upload a file
        test_content = b"content for hash ref get test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        upload_response = api_client.post("/api/v1/upload/hashref/gettest", files=files)
        assert upload_response.status_code == 200
        upload_data = upload_response.json()
        hash_ref = upload_data["hash_ref"]  # e.g., "@abc12345"

        output_file = tmp_path / "hashref_downloaded.bin"

        # Track what URL is used for download
        urls_requested: list[str] = []

        mock_client = MockClientWithDownload(api_client, test_storage_service)
        original_stream = mock_client.stream

        def tracking_stream(method: str, url: str) -> MockStreamResponse:
            urls_requested.append(url)
            return original_stream(method, url)

        mock_client.stream = tracking_stream

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    f"hashref/gettest:{hash_ref}",  # Request by hash ref
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert output_file.exists()
        assert output_file.read_bytes() == test_content

        # Verify download URL used blobs/ path
        assert len(urls_requested) == 1
        blob_name = hash_ref.lstrip("@")
        expected_url = f"/artifacts/hashref/gettest/blobs/{blob_name}"
        assert urls_requested[0] == expected_url

    def test_get_by_tag_uses_tag_symlink_path(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get by tag name uses tag symlink path (not blobs/)."""
        # Upload a file
        test_content = b"content for tag get test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        upload_response = api_client.post("/api/v1/upload/tagref/gettest", files=files)
        assert upload_response.status_code == 200

        output_file = tmp_path / "tagref_downloaded.bin"

        # Track what URL is used for download
        urls_requested: list[str] = []

        mock_client = MockClientWithDownload(api_client, test_storage_service)
        original_stream = mock_client.stream

        def tracking_stream(method: str, url: str) -> MockStreamResponse:
            urls_requested.append(url)
            return original_stream(method, url)

        mock_client.stream = tracking_stream

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "tagref/gettest:latest",  # Request by tag name
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert output_file.exists()
        assert output_file.read_bytes() == test_content

        # Verify download URL used tag symlink path (not blobs/)
        assert len(urls_requested) == 1
        expected_url = "/artifacts/tagref/gettest/latest"
        assert urls_requested[0] == expected_url

    def test_get_requires_server(self, cli_runner_no_config: CliRunner, tmp_path: Path) -> None:
        """Get without server configured fails with error."""
        output_file = tmp_path / "noserver.bin"

        result = cli_runner_no_config.invoke(
            cli,
            ["get", "test/artifact", "-o", str(output_file)],
        )

        assert result.exit_code != 0
        assert "No server configured" in result.output

    def test_get_derives_output_filename_from_path(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get without -o derives output filename from artifact path."""
        # Upload a file
        test_content = b"content for derived name test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/path/myartifact", files=files)

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            # Use isolated filesystem to check derived filename
            with cli_runner.isolated_filesystem(temp_dir=tmp_path):
                result = cli_runner.invoke(
                    cli,
                    [
                        "--server",
                        "http://test",
                        "get",
                        "path/myartifact",
                    ],
                )

                assert result.exit_code == 0
                # Should derive "myartifact" from "path/myartifact"
                assert Path("myartifact").exists()
                assert Path("myartifact").read_bytes() == test_content

    def test_get_quiet_flag_suppresses_progress(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get with --quiet flag suppresses progress output."""
        # Upload a file
        test_content = b"content for quiet test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/quiet/test", files=files)

        output_file = tmp_path / "quiet.bin"

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            # Test with --quiet flag
            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "quiet/test",
                    "-o",
                    str(output_file),
                    "--quiet",
                ],
            )

        assert result.exit_code == 0
        assert output_file.exists()
        # Progress bar output should not appear (only final "Downloaded:" message)
        assert "Downloaded:" in result.output

    def test_get_progress_suppressed_when_not_tty(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get suppresses progress when stdout is not a TTY."""
        # Upload a file
        test_content = b"content for non-tty test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/nontty/test", files=files)

        output_file = tmp_path / "nontty.bin"

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            # CliRunner by default simulates non-TTY environment
            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "nontty/test",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0
        assert output_file.exists()
        # Should still get the Downloaded message
        assert "Downloaded:" in result.output

    def test_get_refuses_to_overwrite_existing_file(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get refuses to overwrite existing file without --force."""
        # Upload a file
        test_content = b"content for overwrite test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/overwrite/test", files=files)

        # Create existing output file
        output_file = tmp_path / "existing.bin"
        output_file.write_bytes(b"original content")

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "overwrite/test",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code != 0
        assert "already exists" in result.output
        assert "--force" in result.output
        # Original file should not be modified
        assert output_file.read_bytes() == b"original content"

    def test_get_overwrites_existing_file_with_force(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get overwrites existing file when --force is specified."""
        # Upload a file
        test_content = b"new content for force test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/force/test", files=files)

        # Create existing output file
        output_file = tmp_path / "force_overwrite.bin"
        output_file.write_bytes(b"original content")

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "force/test",
                    "-o",
                    str(output_file),
                    "--force",
                ],
            )

        assert result.exit_code == 0
        assert "Downloaded:" in result.output
        # File should be overwritten with new content
        assert output_file.read_bytes() == test_content

    def test_get_force_short_flag(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get -f short flag works same as --force."""
        # Upload a file
        test_content = b"content for short flag test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/shortflag/test", files=files)

        # Create existing output file
        output_file = tmp_path / "short_flag.bin"
        output_file.write_bytes(b"original content")

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "shortflag/test",
                    "-o",
                    str(output_file),
                    "-f",
                ],
            )

        assert result.exit_code == 0
        assert output_file.read_bytes() == test_content

    def test_get_no_force_needed_for_new_file(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get does not require --force when output file does not exist."""
        # Upload a file
        test_content = b"content for new file test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/newfile/test", files=files)

        output_file = tmp_path / "new_file.bin"
        # Ensure file does not exist
        assert not output_file.exists()

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "newfile/test",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0
        assert output_file.exists()
        assert output_file.read_bytes() == test_content

    def test_get_skips_download_when_local_hash_matches(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get skips download when local file has matching hash (no --force needed)."""
        # Upload a file
        test_content = b"content for hash match test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/hashmatch/test", files=files)

        # Create existing output file with SAME content (same hash)
        output_file = tmp_path / "hash_match.bin"
        output_file.write_bytes(test_content)

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "hashmatch/test",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0
        assert "already exists with matching hash" in result.output
        # File should not have been modified (same content anyway)
        assert output_file.read_bytes() == test_content

    def test_get_skips_download_no_force_needed_for_identical_file(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get does not require --force when existing file is identical to remote."""
        # Upload a file
        test_content = b"identical content test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/identical/test", files=files)

        # Create existing output file with SAME content
        output_file = tmp_path / "identical.bin"
        output_file.write_bytes(test_content)

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        # Track if stream was called (should NOT be called for identical files)
        original_stream = mock_client.stream
        stream_called = []

        def tracking_stream(method: str, url: str):
            stream_called.append((method, url))
            return original_stream(method, url)

        mock_client.stream = tracking_stream

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "identical/test",
                    "-o",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0
        # Download should have been skipped
        assert len(stream_called) == 0, "Download should have been skipped for identical file"
        assert "already exists with matching hash" in result.output

    def test_get_force_redownloads_even_when_hash_matches(
        self,
        cli_runner: CliRunner,
        api_client: TestClient,
        tmp_path: Path,
        test_storage_service: StorageService,
    ) -> None:
        """Get with --force re-downloads even when local file has matching hash."""
        # Upload a file
        test_content = b"content for force hash match test"
        files = {"file": ("artifact.bin", io.BytesIO(test_content), "application/octet-stream")}
        api_client.post("/api/v1/upload/forcehashmatch/test", files=files)

        # Create existing output file with SAME content (same hash)
        output_file = tmp_path / "force_hash_match.bin"
        output_file.write_bytes(test_content)

        mock_client = MockClientWithDownload(api_client, test_storage_service)

        # Track if stream was called (SHOULD be called when --force is used)
        original_stream = mock_client.stream
        stream_called = []

        def tracking_stream(method: str, url: str):
            stream_called.append((method, url))
            return original_stream(method, url)

        mock_client.stream = tracking_stream

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "get",
                    "forcehashmatch/test",
                    "-o",
                    str(output_file),
                    "--force",
                ],
            )

        assert result.exit_code == 0
        # Download should have happened despite matching hash
        assert len(stream_called) == 1, "Download should have occurred with --force"
        assert "Downloaded:" in result.output
        # File should still have correct content
        assert output_file.read_bytes() == test_content


class TestPushQuietFlag:
    """Tests for push command --quiet flag and TTY detection."""

    def test_push_quiet_flag_suppresses_progress(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push with --quiet flag suppresses progress output."""
        test_file = tmp_path / "quiet_push.bin"
        test_content = b"quiet push content"
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
                    "quiet/push-test",
                    "--quiet",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        # Should still get the Uploaded/Duplicate and Hash ref messages
        assert "Uploaded:" in result.output or "Duplicate:" in result.output
        assert "Hash ref:" in result.output

    def test_push_progress_suppressed_when_not_tty(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push suppresses progress when stdout is not a TTY."""
        test_file = tmp_path / "nontty_push.bin"
        test_content = b"non-tty push content"
        test_file.write_bytes(test_content)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            # CliRunner by default simulates non-TTY environment
            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "push",
                    str(test_file),
                    "--to",
                    "nontty/push-test",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        # Should still get the Uploaded/Duplicate and Hash ref messages
        assert "Uploaded:" in result.output or "Duplicate:" in result.output
        assert "Hash ref:" in result.output


class TestPushOutputFeatures:
    """Tests for push command output features (tagging, download URL, source-uri info)."""

    def test_push_shows_tagged_latest_when_auto_tagging_enabled(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push without --no-latest shows 'Tagged: latest' in output."""
        test_file = tmp_path / "auto_tag.bin"
        test_content = b"auto tag content"
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
                    "autotag/test",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert "Tagged:   latest" in result.output

    def test_push_no_tagged_latest_when_no_latest_flag_used(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push with --no-latest does NOT show 'Tagged: latest' in output."""
        test_file = tmp_path / "no_tag.bin"
        test_content = b"no tag content"
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
                    "notag/test",
                    "--no-latest",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert "Tagged:" not in result.output

    def test_push_download_url_uses_latest_when_auto_tagging(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push without --no-latest uses /latest in download URL."""
        test_file = tmp_path / "download_latest.bin"
        test_content = b"download latest content"
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
                    "download/latest-test",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert "Download: http://test/artifacts/download/latest-test/latest" in result.output

    def test_push_download_url_uses_hash_ref_when_no_latest(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push with --no-latest uses hash ref in download URL."""
        test_file = tmp_path / "download_hash.bin"
        test_content = b"download hash content"
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
                    "download/hash-test",
                    "--no-latest",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        # Download URL should use blobs/ path with short hash (no @ prefix)
        short_hash = expected_hash[:8]
        expected_url = f"Download: http://test/artifacts/download/hash-test/blobs/{short_hash}"
        assert expected_url in result.output

    def test_push_shows_source_uri_info_when_not_provided(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push without --source-uri shows info message about provenance."""
        test_file = tmp_path / "no_source_uri.bin"
        test_content = b"no source uri content"
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
                    "nosourceuri/test",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert "Info: No --source-uri provided" in result.output
        assert "provenance" in result.output.lower()

    def test_push_no_source_uri_info_when_provided(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path
    ) -> None:
        """Push with --source-uri does NOT show info message."""
        test_file = tmp_path / "with_source_uri.bin"
        test_content = b"with source uri content"
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
                    "withsourceuri/test",
                    "--source-uri",
                    "git://repo@v1.0",
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert "Info: No --source-uri provided" not in result.output
