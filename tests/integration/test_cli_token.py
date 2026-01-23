"""Integration tests for CLI token commands."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenInfo, TokenService
from magpie.cli import cli
from magpie.server.app import app
from magpie.server.deps import get_token_service, require_admin_scope

# Patch path for get_client - must match where it's imported/used in the CLI module
PATCH_GET_CLIENT = "magpie.cli.get_client"


@pytest.fixture
def api_client(token_service: TokenService) -> TestClient:
    """Create test client with overridden token service dependency.

    This fixture keeps auth mocked (via require_admin_scope override) but also
    provides the token_service dependency so token creation works.
    """
    # Keep the auth override to mock authentication
    def _noop_require_admin_scope() -> TokenInfo:
        """No-op override for require_admin_scope in tests."""
        return TokenInfo(
            name="test-token",
            scope=TokenScope.ADMIN,
        )

    def override_token_service() -> TokenService:
        return token_service

    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    app.dependency_overrides[get_token_service] = override_token_service
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestTokenCreateCommand:
    """Integration tests for token create command."""

    def test_token_create_with_defaults(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Token create with defaults creates read-scoped token."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "token", "create", "--name", "test-token"],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Created token: test-token" in result.output
        assert "Scope: read" in result.output
        assert "mgp_" in result.output

    def test_token_create_with_write_scope(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Token create can specify write scope."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "token",
                    "create",
                    "--name",
                    "write-token",
                    "--scope",
                    "write",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Created token: write-token" in result.output
        assert "Scope: write" in result.output
        assert "mgp_" in result.output

    def test_token_create_with_admin_scope(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Token create can specify admin scope."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "token",
                    "create",
                    "--name",
                    "admin-token",
                    "--scope",
                    "admin",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Created token: admin-token" in result.output
        assert "Scope: admin" in result.output
        assert "mgp_" in result.output

    def test_token_create_case_insensitive_scope(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Token create accepts case-insensitive scope values."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "token",
                    "create",
                    "--name",
                    "mixed-case",
                    "--scope",
                    "WRITE",
                ],
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Created token: mixed-case" in result.output
        assert "Scope: write" in result.output

    def test_token_create_duplicate_name_fails(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Token create fails when token name already exists."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            # Create first token
            result1 = cli_runner.invoke(
                cli,
                ["--server", "http://test", "token", "create", "--name", "duplicate-name"],
            )
            assert result1.exit_code == 0

            # Try to create second token with same name
            result2 = cli_runner.invoke(
                cli,
                ["--server", "http://test", "token", "create", "--name", "duplicate-name"],
            )

        assert result2.exit_code != 0
        assert "already exists" in result2.output.lower() or "duplicate" in result2.output.lower()

    def test_token_create_invalid_name_fails(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Token create fails with invalid token name."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            # Try names with invalid characters
            invalid_names = [
                "-starts-with-dash",
                ".starts-with-dot",
                "has spaces",
                "has@special",
            ]

            for name in invalid_names:
                result = cli_runner.invoke(
                    cli,
                    ["--server", "http://test", "token", "create", "--name", name],
                )

                assert result.exit_code != 0, f"Expected failure for name: {name}"

    def test_token_create_requires_name(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Token create requires --name option."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "token", "create"],
            )

        assert result.exit_code != 0
        assert "--name" in result.output or "Missing option" in result.output

    def test_token_create_requires_server(self, cli_runner_no_config: CliRunner) -> None:
        """Token create without server configured fails with error."""
        result = cli_runner_no_config.invoke(
            cli,
            ["token", "create", "--name", "test-token"],
        )

        assert result.exit_code != 0
        assert "No server configured" in result.output

    def test_token_create_shows_warning(
        self, cli_runner: CliRunner, api_client: TestClient
    ) -> None:
        """Token create shows warning that token is only shown once."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                ["--server", "http://test", "token", "create", "--name", "warning-test"],
            )

        assert result.exit_code == 0
        assert "only be shown once" in result.output.lower() or "save this" in result.output.lower()

    def test_token_create_json_output(self, cli_runner: CliRunner, api_client: TestClient) -> None:
        """Token create supports JSON output format."""
        with patch(PATCH_GET_CLIENT) as mock_get_client:
            mock_get_client.return_value = api_client

            result = cli_runner.invoke(
                cli,
                [
                    "--server",
                    "http://test",
                    "--format",
                    "json",
                    "token",
                    "create",
                    "--name",
                    "json-token",
                ],
            )

        assert result.exit_code == 0
        # Parse output as JSON and verify structure
        import json

        output = json.loads(result.output)
        assert output["data"]["name"] == "json-token"
        assert output["data"]["scope"] == "read"
        assert "token" in output["data"]
        assert output["data"]["token"].startswith("mgp_")
