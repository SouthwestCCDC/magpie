"""Shared fixtures for concurrency tests."""

from __future__ import annotations

import pytest

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenInfo
from magpie.server.app import app
from magpie.server.deps import (
    require_admin_scope,
    require_admin_scope_header,
    require_write_scope,
)


def _noop_require_admin_scope() -> TokenInfo:
    """No-op override for require_admin_scope in tests."""
    return TokenInfo(
        name="test-token",
        scope=TokenScope.ADMIN,
    )


def _noop_require_write_scope() -> None:
    """No-op override for require_write_scope in tests."""
    return None


def _noop_require_admin_scope_header() -> None:
    """No-op override for require_admin_scope_header in tests."""
    return None


@pytest.fixture(autouse=True)
def override_auth_dependencies():
    """Override auth dependencies for concurrency tests.

    Concurrency tests run against the FastAPI app directly without Caddy,
    so there are no Authorization headers. This fixture disables auth
    checking for all concurrency tests.
    """
    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope_header
    app.dependency_overrides[require_write_scope] = _noop_require_write_scope
    yield
    app.dependency_overrides.pop(require_admin_scope, None)
    app.dependency_overrides.pop(require_admin_scope_header, None)
    app.dependency_overrides.pop(require_write_scope, None)
