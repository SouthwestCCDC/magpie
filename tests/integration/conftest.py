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
    # Return a dummy TokenInfo since tests don't need real auth
    # TokenInfo only has 'name' and 'scope' fields
    from magpie.auth.models import TokenScope

    return TokenInfo(
        name="test-token",
        scope=TokenScope.ADMIN,
    )


def _noop_require_admin_scope_header() -> None:
    """No-op override for require_admin_scope_header in tests."""
    pass


def _noop_require_write_scope() -> None:
    """No-op override for require_write_scope in tests."""
    pass


@pytest.fixture(autouse=True)
def override_auth_dependencies(request):
    """Override auth dependencies for integration tests.

    Integration tests run against the FastAPI app directly without auth,
    so we bypass all scope-checking dependencies. This fixture disables
    auth checking for most integration tests.

    Test modules that define their own token fixtures (admin_token, read_token,
    write_token) are testing authentication behavior and are skipped.
    """
    # Check if this test module defines its own token fixtures
    # (meaning it's an auth-testing module)
    module = request.module
    module_fixtures = getattr(module, "__dict__", {})

    # Check if the module has token fixtures defined
    has_module_token_fixtures = any(
        name in module_fixtures for name in ["admin_token", "read_token", "write_token"]
    )

    if has_module_token_fixtures:
        # Module is testing real authentication - don't override
        yield
        return

    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope_header
    app.dependency_overrides[require_write_scope] = _noop_require_write_scope
    yield
    # Clean up - remove our overrides but preserve any others
    app.dependency_overrides.pop(require_admin_scope, None)
    app.dependency_overrides.pop(require_admin_scope_header, None)
    app.dependency_overrides.pop(require_write_scope, None)
