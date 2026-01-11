"""Integration tests for CLI push and get commands."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie.cli import cli
from magpie.cli.commands.get import parse_artifact_ref
from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_storage_service
from magpie.storage.service import StorageService

# Patch path for get_client - must match where it's imported/used in the CLI module
# Since CLIContext imports get_client from magpie.cli.client, we patch there
PATCH_GET_CLIENT = "magpie.cli.get_client"


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def test_storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing."""
    return StorageService(test_config)


@pytest.fixture
def api_client(test_storage_service: StorageService) -> TestClient:
    """Create test API client with overridden storage service dependency."""

    def override_storage_service() -> StorageService:
        return test_storage_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create Click CLI test runner."""
    return CliRunner()


class MockClientWithDownload:
    """Wrapper around TestClient that handles download endpoints.

    The /artifacts/{path}/{hash_ref} download path isn't a real API endpoint -
    it's typically served by a static file server. This wrapper intercepts
    those requests and returns the appropriate content.
    """

    def __init__(self, api_client: TestClient, storage_service: StorageService) -> None:
        self.api_client = api_client
        self.storage_service = storage_service

    def get(self, url: str) -> "httpx.Response":
        """Handle GET requests, routing downloads to storage."""
        if url.startswith("/artifacts/"):
            return self._handle_download(url)
        return self.api_client.get(url)

    def post(self, url: str, **kwargs: object) -> "httpx.Response":
        """Delegate POST to api_client."""
        return self.api_client.post(url, **kwargs)

    def _handle_download(self, url: str) -> MagicMock:
        """Handle download from /artifacts/{path}/{hash_ref} or /artifacts/{path}/blobs/{hash}."""
        from magpie.storage.blob import read_blob
        from magpie.storage.paths import artifact_dir_path

        # Parse URL - handle both patterns:
        # /artifacts/{path}/{tag}  (tag symlinks)
        # /artifacts/{path}/blobs/{hash}  (direct blob access)
        parts = url.split("/")
        # parts = ['', 'artifacts', path_parts..., ref_or_blobs, maybe_hash]

        if "blobs" in parts:
            # Pattern: /artifacts/{path}/blobs/{hash}
            blobs_idx = parts.index("blobs")
            path = "/".join(parts[2:blobs_idx])
            hash_ref = "@" + parts[blobs_idx + 1]  # Add @ prefix for lookup
        else:
            # Pattern: /artifacts/{path}/{hash_ref}
            hash_ref = parts[-1]
            path = "/".join(parts[2:-1])

        try:
            # Get the full hash from storage service
            info = self.storage_service.get_artifact_info(path, hash_ref)

            # Read the blob using the full hash
            artifact_dir = artifact_dir_path(
                self.storage_service.config.storage_path, path
            )
            blob_file = read_blob(artifact_dir, info.hash)
            content = blob_file.read_bytes()

            response = MagicMock()
            response.status_code = 200
            response.content = content
            return response
        except Exception:
            response = MagicMock()
            response.status_code = 404
            response.content = b""
            return response

    def __enter__(self) -> "MockClientWithDownload":
        return self

    def __exit__(self, *args: object) -> None:
        pass


class TestParseArtifactRef:
    """Tests for artifact reference parsing."""

    def test_parse_with_ref(self) -> None:
        """Parse artifact reference with explicit ref."""
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

    def test_parse_nested_path_with_ref(self) -> None:
        """Parse nested path with ref."""
        path, ref = parse_artifact_ref("project/images/ubuntu:v1.0")
        assert path == "project/images/ubuntu"
        assert ref == "v1.0"


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
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path,
        test_storage_service: StorageService
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
                    "--server", "http://test",
                    "push", str(test_file),
                    "--to", "source/uri-test",
                    "--source-uri", "git://repo@v1.0"
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

    def test_push_requires_server(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Push without server configured fails with error."""
        test_file = tmp_path / "no_server.bin"
        test_file.write_bytes(b"content")

        result = cli_runner.invoke(
            cli,
            ["push", str(test_file), "--to", "test/artifact"],
        )

        assert result.exit_code != 0
        assert "No server configured" in result.output


class TestGetCommand:
    """Integration tests for get command."""

    def test_get_downloads_and_verifies_hash(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path,
        test_storage_service: StorageService
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
                    "--server", "http://test",
                    "get", "get/test:latest",
                    "-o", str(output_file),
                ],
            )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
        assert "Downloaded:" in result.output
        assert output_file.exists()
        assert output_file.read_bytes() == test_content

    def test_get_with_no_verify_skips_verification(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path,
        test_storage_service: StorageService
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
                    "--server", "http://test",
                    "get", "noverify/test",
                    "-o", str(output_file),
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

        # Mock download response with corrupted content
        download_response = MagicMock()
        download_response.status_code = 200
        download_response.content = b"CORRUPTED CONTENT"

        def mock_get(url: str) -> MagicMock:
            if "/info" in url:
                return info_response
            return download_response

        mock_client.get = mock_get
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server", "http://test",
                    "get", "mismatch/test",
                    "-o", str(output_file),
                ],
            )

        assert result.exit_code != 0
        assert "Hash mismatch" in result.output

    def test_get_defaults_to_latest_ref(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path,
        test_storage_service: StorageService
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
                    "--server", "http://test",
                    "get", "latest/test",
                    "-o", str(output_file),
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
                    "--server", "http://test",
                    "get", "nonexistent/artifact",
                    "-o", str(output_file),
                ],
            )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_get_requires_server(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Get without server configured fails with error."""
        output_file = tmp_path / "noserver.bin"

        result = cli_runner.invoke(
            cli,
            ["get", "test/artifact", "-o", str(output_file)],
        )

        assert result.exit_code != 0
        assert "No server configured" in result.output

    def test_get_derives_output_filename_from_path(
        self, cli_runner: CliRunner, api_client: TestClient, tmp_path: Path,
        test_storage_service: StorageService
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
                        "--server", "http://test",
                        "get", "path/myartifact",
                    ],
                )

                assert result.exit_code == 0
                # Should derive "myartifact" from "path/myartifact"
                assert Path("myartifact").exists()
                assert Path("myartifact").read_bytes() == test_content
