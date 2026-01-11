"""Shared fixtures for integration tests."""

from __future__ import annotations

import pytest

from magpie.server.app import app
from magpie.server.deps import require_admin_scope_header, require_write_scope


def _noop_require_write_scope() -> None:
    """No-op override for require_write_scope in tests."""
    pass


def _noop_require_admin_scope() -> None:
    """No-op override for require_admin_scope_header in tests."""
    pass


@pytest.fixture(autouse=True)
def override_auth_dependencies():
    """Override auth dependencies for integration tests.

    Integration tests run against the FastAPI app directly without Caddy,
    so there are no X-Magpie-Scope headers. This fixture disables auth
    checking for all integration tests.

    For tests that specifically test auth behavior, use the e2e tests
    which run against the full stack including Caddy.
    """
    app.dependency_overrides[require_write_scope] = _noop_require_write_scope
    app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope
    yield
    # Clean up - remove our overrides but preserve any others
    app.dependency_overrides.pop(require_write_scope, None)
    app.dependency_overrides.pop(require_admin_scope_header, None)
