"""Shared fixtures for integration tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from magpie.auth.service import TokenInfo
from magpie.server.app import app
from magpie.server.deps import (
    require_admin_scope,
    require_admin_scope_header,
    require_write_scope,
)


@pytest.fixture
def cli_runner_no_config() -> CliRunner:
    """Create CLI runner that simulates no server/token configuration.

    This fixture patches get_server and get_token to return empty strings
    when no CLI override is provided, simulating an environment where no
    config file exists and no environment variables are set. Use this for
    tests that verify "No server configured" or "No token configured" error
    handling.

    The patches respect CLI overrides (--server, --token) so tests can verify
    that a missing token fails even when server is provided via CLI.
    """
    runner = CliRunner()

    # Store original invoke method
    original_invoke = runner.invoke

    def mock_get_server(cli_override=None, config_path=None):
        """Return CLI override if provided, otherwise empty string."""
        return cli_override if cli_override else ""

    def mock_get_token(cli_override=None, config_path=None):
        """Return CLI override if provided, otherwise empty string."""
        return cli_override if cli_override else ""

    def patched_invoke(*args, **kwargs):
        # Patch where the functions are used (magpie.cli module), not where they're defined
        with patch("magpie.cli.get_server", side_effect=mock_get_server):
            with patch("magpie.cli.get_token", side_effect=mock_get_token):
                return original_invoke(*args, **kwargs)

    runner.invoke = patched_invoke
    return runner


def _noop_require_admin_scope() -> TokenInfo:
    """No-op override for require_admin_scope in tests."""
    from magpie.auth.models import TokenScope

    return TokenInfo(
        name="test-token",
        scope=TokenScope.ADMIN,
    )


def _noop_require_write_scope() -> None:
    """No-op override for require_write_scope in tests.

    Returns None as this dependency only validates scope but does not
    return token info.
    """
    return None


def _noop_require_admin_scope_header() -> None:
    """No-op override for require_admin_scope_header in tests.

    Returns None as this dependency only validates scope but does not
    return token info.
    """
    return None


@pytest.fixture(autouse=True)
def override_auth_dependencies(request):
    """Override auth dependencies for integration tests.

    Integration tests run against the FastAPI app directly without Caddy,
    so there are no Authorization headers. This fixture disables auth
    checking for all integration tests.

    Test modules that define their own token fixtures (admin_token, read_token,
    write_token) are testing authentication behavior and are skipped.
    """
    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope_header
    app.dependency_overrides[require_write_scope] = _noop_require_write_scope
    yield
    app.dependency_overrides.pop(require_admin_scope, None)
    app.dependency_overrides.pop(require_admin_scope_header, None)
    app.dependency_overrides.pop(require_write_scope, None)
