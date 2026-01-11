"""Shared fixtures for integration tests."""

from __future__ import annotations

import pytest

from magpie.auth.service import TokenInfo
from magpie.server.app import app
from magpie.server.deps import require_admin_scope


def _noop_require_admin_scope() -> TokenInfo:
    """No-op override for require_admin_scope in tests."""
    # Return a dummy TokenInfo since tests don't need real auth
    from magpie.auth.models import TokenScope
    return TokenInfo(
        id=1,
        name="test-token",
        description="Test token",
        scope=TokenScope.ADMIN,
        disabled=False,
    )


@pytest.fixture(autouse=True)
def override_auth_dependencies():
    """Override auth dependencies for integration tests.

    Integration tests run against the FastAPI app directly without auth,
    so we bypass the require_admin_scope dependency. This fixture disables
    auth checking for all integration tests.

    For tests that specifically test auth behavior, use the e2e tests
    which run against the full stack including authentication.
    """
    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    yield
    # Clean up - remove our overrides but preserve any others
    app.dependency_overrides.pop(require_admin_scope, None)
