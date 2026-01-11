"""Shared fixtures for integration tests."""

from __future__ import annotations

import pytest

from magpie.auth.service import TokenInfo
from magpie.server.app import app
from magpie.server.deps import (
    require_admin_scope,
    require_admin_scope_header,
    require_write_scope,
)


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
