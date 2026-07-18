"""Integration tests for CLI query commands (ls, info, url)."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie.cli import cli
from tests.integration.conftest import upload_test_artifact

# Patch path for get_client - must match where it's imported/used in the CLI module
PATCH_GET_CLIENT = "magpie.cli.get_client"


class TestLsCommand:
    """Integration tests for ls command."""

    def test_ls_displays_versions_in_table_format(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls command displays versions in table format."""
        # Upload two versions
        upload_test_artifact(api_client, "test/ls", b"version 1 content")
        upload_test_artifact(api_client, "test/ls", b"version 2 content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "test/ls"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Check table headers
        assert "HASH" in result.output
        assert "TAGS" in result.output
        assert "UPLOADED_BY" in result.output
        assert "UPLOADED_AT" in result.output
        # Check we have hash refs in output
        assert "@" in result.output

    def test_ls_with_empty_artifact_shows_message(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls with non-existent artifact shows appropriate message."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "nonexistent/artifact"],
            )

        assert result.exit_code == 0
        # New behavior: treats non-existent path as prefix search
        assert "No artifacts found" in result.output

    def test_ls_shows_tags(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Ls command shows tags for versions."""
        # Upload an artifact (will have 'latest' tag)
        upload_test_artifact(api_client, "test/tags", b"content with tags")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "test/tags"],
            )

        assert result.exit_code == 0
        assert "latest" in result.output

    def test_ls_requires_server(self, cli_runner_no_config: CliRunner) -> None:
        """Ls without server configured fails with error."""
        result = cli_runner_no_config.invoke(cli, ["ls", "test/artifact"])

        assert result.exit_code != 0
        assert "No server configured" in result.output

    def test_ls_without_path_lists_all_paths(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls without path argument lists all artifact paths."""
        # Upload artifacts at different paths
        upload_test_artifact(api_client, "test/artifact1", b"content1")
        upload_test_artifact(api_client, "test/artifact2", b"content2")
        upload_test_artifact(api_client, "images/ubuntu", b"content3")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should list top-level directories with trailing slash
        assert "test/" in result.output
        assert "images/" in result.output
        # Should NOT show full paths
        assert "test/artifact1" not in result.output
        assert "test/artifact2" not in result.output
        assert "images/ubuntu" not in result.output
        # Should NOT show table headers (not listing versions)
        assert "HASH" not in result.output

    def test_ls_with_partial_path_lists_matching_paths(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls with partial path lists artifact paths under that prefix."""
        upload_test_artifact(api_client, "test/artifact1", b"content1")
        upload_test_artifact(api_client, "test/artifact2", b"content2")
        upload_test_artifact(api_client, "images/ubuntu", b"content3")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "test"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should list immediate children under test/ (no prefix in output)
        assert "artifact1" in result.output
        assert "artifact2" in result.output
        # Should NOT list other paths
        assert "images" not in result.output

    def test_ls_with_root_slash_lists_all(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Test that 'magpie ls /' lists all artifacts instead of showing 'Empty path in input: /' error."""
        # Upload artifacts at different paths
        upload_test_artifact(api_client, "test/artifact1", b"content1")
        upload_test_artifact(api_client, "test/artifact2", b"content2")
        upload_test_artifact(api_client, "images/ubuntu", b"content3")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "/"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should list top-level directories with trailing slash (same as no argument)
        assert "test/" in result.output
        assert "images/" in result.output
        # Should NOT show full paths
        assert "test/artifact1" not in result.output
        # Should NOT show table headers (not listing versions)
        assert "HASH" not in result.output

    def test_ls_with_leading_slash_normalizes(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls with leading slash normalizes path correctly."""
        upload_test_artifact(api_client, "test/artifact", b"content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "/test/artifact"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should show versions table (exact match)
        assert "HASH" in result.output
        assert "@" in result.output

    def test_ls_with_exact_path_shows_versions(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls with exact artifact path shows version table."""
        upload_test_artifact(api_client, "test/artifact", b"content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "test/artifact"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should show versions table
        assert "HASH" in result.output
        assert "TAGS" in result.output
        assert "latest" in result.output

    def test_ls_with_nonexistent_prefix(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls with non-matching prefix shows appropriate message."""
        upload_test_artifact(api_client, "test/artifact", b"content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "images"],
            )

        assert result.exit_code == 0
        assert "No artifacts found" in result.output

    def test_ls_non_recursive_filters_nested_paths(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls without --recursive filters out nested paths."""
        # Upload artifacts with nesting
        upload_test_artifact(api_client, "test/artifact1", b"content1")
        upload_test_artifact(api_client, "test/artifact2", b"content2")
        upload_test_artifact(api_client, "test/sub/deep", b"content3")
        upload_test_artifact(api_client, "images/ubuntu", b"content4")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should show top-level directories only
        assert "test/" in result.output
        assert "images/" in result.output
        # Should NOT show full paths or nested paths
        assert "test/artifact1" not in result.output
        assert "test/sub/deep" not in result.output

    def test_ls_recursive_shows_all_nested_paths(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls with --recursive shows all nested paths."""
        # Upload artifacts with nesting
        upload_test_artifact(api_client, "test/artifact1", b"content1")
        upload_test_artifact(api_client, "test/artifact2", b"content2")
        upload_test_artifact(api_client, "test/sub/deep", b"content3")
        upload_test_artifact(api_client, "images/ubuntu", b"content4")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "--recursive"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should show all paths including nested
        assert "test/artifact1" in result.output
        assert "test/artifact2" in result.output
        assert "test/sub/deep" in result.output
        assert "images/ubuntu" in result.output

    def test_ls_recursive_short_flag(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Ls with -r shows all nested paths."""
        upload_test_artifact(api_client, "test/artifact1", b"content1")
        upload_test_artifact(api_client, "test/sub/deep", b"content2")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "-r"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "test/artifact1" in result.output
        assert "test/sub/deep" in result.output

    def test_ls_with_prefix_non_recursive(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Ls with prefix and no --recursive filters nested paths."""
        upload_test_artifact(api_client, "test/artifact1", b"content1")
        upload_test_artifact(api_client, "test/artifact2", b"content2")
        upload_test_artifact(api_client, "test/sub/deep", b"content3")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "test"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should show immediate artifact children under test/
        assert "artifact1" in result.output
        assert "artifact2" in result.output
        # Note: Storage service non-recursive mode doesn't discover intermediate
        # directories (test/sub/), only actual artifacts at the current level
        # Should NOT show deeply nested paths
        assert "test/sub/deep" not in result.output
        assert "deep" not in result.output

    def test_ls_with_prefix_recursive(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Ls with prefix and --recursive shows all nested paths."""
        upload_test_artifact(api_client, "test/artifact1", b"content1")
        upload_test_artifact(api_client, "test/artifact2", b"content2")
        upload_test_artifact(api_client, "test/sub/deep", b"content3")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "ls", "-r", "test"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "test/artifact1" in result.output
        assert "test/artifact2" in result.output
        assert "test/sub/deep" in result.output


class TestInfoCommand:
    """Integration tests for info command."""

    def test_info_displays_all_metadata_fields(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Info command displays all metadata fields."""
        upload_data = upload_test_artifact(
            api_client,
            "test/info",
            b"content for info test",
            source_uri="https://github.com/test/repo",
        )

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", "test/info:latest"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Check all metadata fields are present
        assert "Hash:" in result.output
        assert upload_data["hash"] in result.output
        assert "Hash Ref:" in result.output
        assert upload_data["hash_ref"] in result.output
        assert "Uploaded By:" in result.output
        assert "Uploaded At:" in result.output
        assert "Source URI:" in result.output
        assert "https://github.com/test/repo" in result.output
        assert "Tags:" in result.output
        assert "latest" in result.output

    def test_info_with_tag_resolves_correctly(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Info with tag name resolves correctly."""
        upload_data = upload_test_artifact(api_client, "test/tag-resolve", b"tag test")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", "test/tag-resolve:latest"],
            )

        assert result.exit_code == 0
        assert upload_data["hash_ref"] in result.output

    def test_info_with_hash_ref_resolves_correctly(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Info with hash ref resolves correctly."""
        upload_data = upload_test_artifact(api_client, "test/hash-resolve", b"hash test")
        hash_ref = upload_data["hash_ref"]

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", f"test/hash-resolve:{hash_ref}"],
            )

        assert result.exit_code == 0
        assert hash_ref in result.output

    def test_info_not_found(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Info for non-existent artifact returns error."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", "nonexistent/artifact"],
            )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_info_with_bad_ref_on_existing_artifact_is_not_a_prefix(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """A real artifact with a nonexistent tag/ref must not be misreported as a prefix.

        The listing endpoint returns the path itself (not just children) when it
        IS an artifact, so this guards against is_path_prefix() mistaking that
        self-match for a child and claiming the artifact is "a path prefix, not
        an artifact" when it's actually just missing the requested ref.
        """
        upload_test_artifact(api_client, "test/badref", b"real artifact content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", "test/badref:nonexistent-tag"],
            )

        assert result.exit_code != 0
        assert "Artifact not found: test/badref:nonexistent-tag" in result.output
        assert "not an artifact" not in result.output

    def test_info_on_path_prefix_gives_prefix_aware_error(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Info on a path that is a prefix (has children) points to `ls`, not a bare 404."""
        upload_test_artifact(api_client, "smoke/hello", b"prefix test content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", "smoke"],
            )

        assert result.exit_code != 0
        assert "not an artifact" in result.output
        assert "magpie ls smoke/" in result.output
        # Should not surface the implementation-detail default-tag message
        assert "smoke:latest" not in result.output

    def test_info_on_path_prefix_with_trailing_slash(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Info on a prefix with a trailing slash gives the same prefix-aware error."""
        upload_test_artifact(api_client, "smoke/hello", b"prefix test content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "info", "smoke/"],
            )

        assert result.exit_code != 0
        assert "not an artifact" in result.output
        assert "magpie ls smoke/" in result.output

    def test_info_requires_server(self, cli_runner_no_config: CliRunner) -> None:
        """Info without server configured fails with error."""
        result = cli_runner_no_config.invoke(cli, ["info", "test/artifact"])

        assert result.exit_code != 0
        assert "No server configured" in result.output


class TestUrlCommand:
    """Integration tests for url command."""

    def test_url_outputs_bare_url(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Url command outputs bare URL for scripting."""
        upload_test_artifact(api_client, "test/url", b"url test content")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "url", "test/url:latest"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # Check output is a bare URL with tag symlink path
        output = result.output.strip()
        assert output == "http://test/artifacts/test/url/latest"

    def test_url_suitable_for_curl(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Url output is suitable for curl/wget (single line, no extra text)."""
        upload_test_artifact(api_client, "test/curl", b"curl test")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "https://artifacts.example.com", "url", "test/curl"],
            )

        assert result.exit_code == 0
        output = result.output.strip()
        # Should be a single line
        assert "\n" not in output
        # Should start with the server URL
        assert output.startswith("https://artifacts.example.com/artifacts/")
        # Should not have any extra text
        assert output.count("http") == 1

    def test_url_uses_tag_symlink_when_tag_requested(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Url uses tag symlink path when user requests by tag name."""
        upload_test_artifact(api_client, "test/resolve", b"resolve test")

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "url", "test/resolve:latest"],
            )

        assert result.exit_code == 0
        # URL should use tag symlink path (not blobs/ path)
        output = result.output.strip()
        assert output == "http://test/artifacts/test/resolve/latest"

    def test_url_uses_blobs_path_when_hash_requested(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Url uses blobs/ path when user requests by hash ref."""
        upload_data = upload_test_artifact(api_client, "test/hash-url", b"hash url test")
        hash_ref = upload_data["hash_ref"]

        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "url", f"test/hash-url:{hash_ref}"],
            )

        assert result.exit_code == 0
        # URL should use blobs/ path (hash without @ prefix)
        output = result.output.strip()
        blob_name = hash_ref.lstrip("@")
        assert output == f"http://test/artifacts/test/hash-url/blobs/{blob_name}"

    def test_url_not_found(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Url for non-existent artifact returns error."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "url", "nonexistent/artifact"],
            )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_url_requires_server(self, cli_runner_no_config: CliRunner) -> None:
        """Url without server configured fails with error."""
        result = cli_runner_no_config.invoke(cli, ["url", "test/artifact"])

        assert result.exit_code != 0
        assert "No server configured" in result.output
