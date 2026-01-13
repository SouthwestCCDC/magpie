"""Shared fixtures for concurrency tests.

AI-assisted: Generated with Claude Code (Opus 4.5).
"""

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

    Note: This fixture modifies global FastAPI app state (dependency_overrides).
    The cleanup uses pop() with a default value to handle cases where the key
    may have been removed by other test cleanup code, ensuring cleanup succeeds
    even if tests fail partway through.
    """
    # Store original state to restore on cleanup
    original_overrides = {
        require_admin_scope: app.dependency_overrides.get(require_admin_scope),
        require_admin_scope_header: app.dependency_overrides.get(require_admin_scope_header),
        require_write_scope: app.dependency_overrides.get(require_write_scope),
    }
    try:
        app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
        app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope_header
        app.dependency_overrides[require_write_scope] = _noop_require_write_scope
        yield
    finally:
        # Restore original state (remove overrides we added)
        for dep, original_value in original_overrides.items():
            if original_value is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = original_value
