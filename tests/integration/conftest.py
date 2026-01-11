"""Shared fixtures for integration tests."""

from __future__ import annotations

import pytest

from magpie.auth.service import TokenInfo
from magpie.server.app import app
from magpie.server.deps import require_admin_scope


def _noop_require_admin_scope() -> TokenInfo:
    """No-op override for require_admin_scope in tests."""
    from magpie.auth.models import TokenScope

    return TokenInfo(
        name="test-token",
        scope=TokenScope.ADMIN,
        enabled=True,
        created_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture(autouse=True)
def override_auth_dependencies():
    """Override auth dependencies for integration tests.

    Integration tests run against the FastAPI app directly without Caddy,
    so there are no Authorization headers. This fixture disables auth
    checking for all integration tests.

    For tests that specifically test auth behavior, use the e2e tests
    which run against the full stack including Caddy.
    """
    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    yield
    app.dependency_overrides.pop(require_admin_scope, None)
